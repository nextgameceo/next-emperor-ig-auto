"""Claude Messages API の最小クライアント（標準ライブラリのみ）。

- リトライは 429 / 5xx / 529 のみ、最大 max_attempts 回で打ち切る（無限リトライしない）
- APIキーはヘッダにのみ使い、ログ・例外メッセージには出さない
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Callable

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
RETRYABLE = {429, 500, 502, 503, 504, 529}


class ClaudeError(RuntimeError):
    pass


def _default_transport(req: urllib.request.Request, timeout: int) -> dict:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class ClaudeClient:
    def __init__(self, api_key: str, transport: Callable | None = None,
                 max_attempts: int = 3, timeout: int = 60, sleep: Callable = time.sleep):
        self._api_key = api_key
        self._transport = transport or _default_transport
        self.max_attempts = max_attempts
        self.timeout = timeout
        self._sleep = sleep
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self._thinking_off: dict[str, str] = {}

    def complete(self, model: str, prompt: str, max_tokens: int, system: str | None = None,
                 effort: str = "low", disable_thinking: bool = True) -> str:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": effort},
        }
        if system:
            payload["system"] = system
        if disable_thinking:
            # 投稿文生成など短い出力では思考トークンを使わず費用を抑える
            # （モデルによって「思考オフ」の指定方法が違うため、拒否されたら指示どおりの形式で送り直す）
            payload["thinking"] = {"type": self._thinking_off.get(model, "disabled")}
        try:
            return self._send(payload)
        except ClaudeError as e:
            alt = "between_tools"
            if disable_thinking and alt in str(e) and payload["thinking"]["type"] != alt:
                self._thinking_off[model] = alt
                payload["thinking"] = {"type": alt}
                return self._send(payload)
            raise

    def _send(self, payload: dict) -> str:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            method="POST",
        )
        last_err = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = self._transport(req, self.timeout)
                break
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {body}"
                if e.code not in RETRYABLE or attempt == self.max_attempts:
                    raise ClaudeError(f"Claude API呼び出し失敗 ({last_err})") from None
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt == self.max_attempts:
                    raise ClaudeError(f"Claude API接続失敗 ({last_err})") from None
            wait = 5 * attempt
            print(f"[claude] 一時エラーのため {wait}秒後に再試行 ({attempt}/{self.max_attempts}): {last_err}")
            self._sleep(wait)

        usage = result.get("usage") or {}
        self.usage["input_tokens"] += int(usage.get("input_tokens", 0))
        self.usage["output_tokens"] += int(usage.get("output_tokens", 0))
        self.usage["calls"] += 1
        text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text").strip()
        if not text:
            raise ClaudeError(f"Claudeの応答が空です (stop_reason={result.get('stop_reason')})")
        return text


def parse_json_block(text: str) -> dict:
    """応答からJSONオブジェクトを取り出す（```json ...``` 囲みにも対応）。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    if not candidate:
        raise ValueError("JSONが見つかりません")
    return json.loads(candidate)
