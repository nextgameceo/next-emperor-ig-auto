"""テスト用のフェイク（外部APIを呼ばない）。"""
from __future__ import annotations

import json

GOOD_POST = {
    "title": "夜職で磨いた力は\n昼職でも武器になる",
    "subtitle": "接客経験を強みに変える4つの視点",
    "slides": [
        {"heading": "相手の気持ちを読む力", "body": "表情や声のトーンから、相手が本当に求めていることを察する力は、営業や事務の調整役でも重宝されます。"},
        {"heading": "初対面でも話せる力", "body": "毎晩たくさんの人と話してきた経験は、初めてのお客様と関係をつくる土台になります。"},
        {"heading": "場の空気を整える力", "body": "その場に合わせて動く状況判断の速さは、どの職場でも評価されやすいポイントです。"},
        {"heading": "まずは書き出してみよう", "body": "自分の得意なことを、具体的な場面と一緒に書き出すところから始めてみましょう。"},
    ],
    "caption": "夜職の経験、昼職でどう活きるか考えたことはありますか？\n\n接客で磨いた力は、新しい職場でも強みになります。\n\n保存して見返してください。\n詳しくはプロフィールのリンクから。",
    "hashtags": ["#夜職から昼職", "#転職", "#キャリアチェンジ", "#NEXTEMPEROR"],
}


class FakeClaude:
    """complete() の呼び出しごとに responses を順に返す。"""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def complete(self, model, prompt, max_tokens, system=None, effort="low", disable_thinking=True):
        self.calls.append({"model": model, "prompt": prompt, "system": system})
        self.usage["calls"] += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


REVIEW_OK = {"ok": True, "issues": []}


class FakeIG:
    def __init__(self, recent=None, fail_publish=False, insights=None, debug=None):
        self.items: list[str] = []
        self.carousels: list[tuple] = []
        self.published: list[str] = []
        self.recent = recent or []
        self.fail_publish = fail_publish
        self._insights = insights or {}
        self._debug = debug or {"is_valid": True, "expires_at": 0, "data_access_expires_at": 0}

    def create_image_item(self, url):
        self.items.append(url)
        return f"item{len(self.items)}"

    def create_carousel(self, children, caption):
        self.carousels.append((children, caption))
        return "carousel1"

    def wait_ready(self, cid, poll, mx):
        return None

    def publish(self, cid):
        if self.fail_publish:
            from ig_bot.ig_client import IGError
            raise IGError("publish failed")
        self.published.append(cid)
        return "media123"

    def get_media(self, mid, fields=""):
        return {"id": mid, "permalink": "https://www.instagram.com/p/abc/", "timestamp": "2026-10-29T12:01:00+0000"}

    def recent_media(self, limit=10):
        return self.recent

    def media_insights(self, mid, metrics):
        return self._insights.get(mid, {})

    def debug_token(self):
        return self._debug
