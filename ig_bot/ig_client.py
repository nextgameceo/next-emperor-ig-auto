"""Instagram Graph API（Facebookログイン方式・ページトークン）の最小クライアント（標準ライブラリのみ）。

- トークンはリクエストにのみ使い、ログ・例外メッセージには出さない
- 読み取り系は一時エラー時に回数上限つきで再試行する
- 公開（media_publish）は二重投稿を避けるため自動再試行しない（呼び出し側で投稿済み確認を行う）
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

GRAPH = "https://graph.facebook.com"
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613, 9007}


class IGError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.code, self.subcode, self.transient = code, subcode, transient

    @property
    def token_invalid(self) -> bool:
        return self.code == 190


def _default_transport(req: urllib.request.Request, timeout: int) -> dict:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class IGClient:
    def __init__(self, token: str, ig_user_id: str, version: str = "v26.0",
                 transport: Callable | None = None, sleep: Callable = time.sleep,
                 max_attempts: int = 3, timeout: int = 60):
        self._token = token
        self.ig_user_id = ig_user_id
        self.base = f"{GRAPH}/{version}"
        self._transport = transport or _default_transport
        self._sleep = sleep
        self.max_attempts = max_attempts
        self.timeout = timeout

    # ------------------------------------------------------------ low level
    def _request(self, method: str, path: str, params: dict | None = None, retry: bool = True) -> dict:
        params = dict(params or {})
        params["access_token"] = self._token
        data = urllib.parse.urlencode(params).encode()
        url = f"{self.base}/{path.lstrip('/')}"
        if method == "GET":
            req = urllib.request.Request(f"{url}?{data.decode()}", method="GET")
        else:
            req = urllib.request.Request(url, data=data, method="POST",
                                         headers={"content-type": "application/x-www-form-urlencoded"})
        attempts = self.max_attempts if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                return self._transport(req, self.timeout)
            except urllib.error.HTTPError as e:
                err = self._parse_error(e)
            except (urllib.error.URLError, TimeoutError) as e:
                err = IGError(f"Instagram APIに接続できません ({type(e).__name__})", transient=True)
            if not err.transient or attempt == attempts:
                raise err
            wait = 5 * attempt
            print(f"[ig] 一時エラーのため {wait}秒後に再試行 ({attempt}/{attempts}): {err}")
            self._sleep(wait)
        raise IGError("到達しない")

    @staticmethod
    def _parse_error(e: urllib.error.HTTPError) -> IGError:
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            pass
        err = body.get("error") or {}
        code, sub = err.get("code"), err.get("error_subcode")
        msg = err.get("error_user_msg") or err.get("message") or f"HTTP {e.code}"
        transient = bool(err.get("is_transient")) or code in TRANSIENT_CODES or e.code >= 500
        return IGError(f"Instagram APIエラー (HTTP {e.code}, code={code}, subcode={sub}): {str(msg)[:300]}",
                       code=code, subcode=sub, transient=transient)

    # ------------------------------------------------------------ publish
    def create_image_item(self, image_url: str) -> str:
        r = self._request("POST", f"{self.ig_user_id}/media",
                          {"image_url": image_url, "is_carousel_item": "true"})
        return r["id"]

    def create_carousel(self, children: list[str], caption: str) -> str:
        r = self._request("POST", f"{self.ig_user_id}/media",
                          {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption})
        return r["id"]

    def container_status(self, container_id: str) -> str:
        r = self._request("GET", container_id, {"fields": "status_code,status"})
        return str(r.get("status_code") or "")

    def wait_ready(self, container_id: str, poll_seconds: int, max_polls: int) -> None:
        for _ in range(max_polls):
            st = self.container_status(container_id)
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise IGError(f"メディアの準備に失敗しました（status={st}）")
            self._sleep(poll_seconds)
        raise IGError("メディアの準備がタイムアウトしました")

    def publish(self, creation_id: str) -> str:
        r = self._request("POST", f"{self.ig_user_id}/media_publish", {"creation_id": creation_id}, retry=False)
        return r["id"]

    # ------------------------------------------------------------ read
    def get_media(self, media_id: str, fields: str = "id,permalink,timestamp,media_type") -> dict:
        return self._request("GET", media_id, {"fields": fields})

    def recent_media(self, limit: int = 10) -> list[dict]:
        r = self._request("GET", f"{self.ig_user_id}/media",
                          {"fields": "id,caption,timestamp,permalink,media_type", "limit": str(limit)})
        return r.get("data") or []

    def publishing_limit(self) -> dict:
        r = self._request("GET", f"{self.ig_user_id}/content_publishing_limit", {"fields": "quota_usage,config"})
        data = r.get("data") or [{}]
        return data[0]

    def media_insights(self, media_id: str, metrics: list[str]) -> dict:
        """指標をまとめて取得。未対応の指標が混ざって失敗したら1つずつ取り直す。"""
        def parse(r: dict) -> dict:
            out = {}
            for item in r.get("data") or []:
                vals = item.get("values") or []
                v = vals[0].get("value") if vals else item.get("total_value", {}).get("value")
                if isinstance(v, (int, float)):
                    out[item.get("name")] = v
            return out
        try:
            return parse(self._request("GET", f"{media_id}/insights", {"metric": ",".join(metrics)}))
        except IGError as e:
            if e.token_invalid:
                raise
        out = {}
        for m in metrics:
            try:
                out.update(parse(self._request("GET", f"{media_id}/insights", {"metric": m})))
            except IGError as e:
                if e.token_invalid:
                    raise
        return out

    def debug_token(self) -> dict:
        r = self._request("GET", "debug_token", {"input_token": self._token})
        return r.get("data") or {}


def url_reachable(url: str, timeout: int = 20) -> bool:
    """画像URLが外部から取得できるか（公開リポジトリのraw URLの反映待ちに使う）。"""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("content-type", "")
            return resp.status == 200 and "image" in ctype
    except Exception:
        return False
