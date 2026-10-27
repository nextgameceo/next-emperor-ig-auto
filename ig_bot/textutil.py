"""文字数計算・正規化・類似度などのテキスト処理ユーティリティ（外部依存なし）。"""
from __future__ import annotations

import re
import unicodedata

# X (twitter-text v3) の重み付き文字数ルール
# 下記範囲の文字は1カウント、それ以外（日本語など）は2カウント。URLは長さに関係なく23。
_LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
X_MAX_WEIGHTED = 280
X_URL_WEIGHT = 23

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
# ドメインだけの記載（例: next-emperor.com）も検出する
BARE_DOMAIN_RE = re.compile(r"\b[a-z0-9-]+\.(?:com|jp|net|org|co\.jp|io|me|info)\b", re.IGNORECASE)
HASHTAG_RE = re.compile(r"[#＃][^\s#＃]+")


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    for lo, hi in _LIGHT_RANGES:
        if lo <= cp <= hi:
            return 1
    return 2


def weighted_length(text: str) -> int:
    """Xの投稿上限(280)と比較するための重み付き文字数。"""
    text = unicodedata.normalize("NFC", text)
    total = 0
    pos = 0
    for m in URL_RE.finditer(text):
        total += sum(_char_weight(c) for c in text[pos:m.start()])
        total += X_URL_WEIGHT
        pos = m.end()
    total += sum(_char_weight(c) for c in text[pos:])
    return total


def normalize(text: str) -> str:
    """重複判定用の正規化: URL・ハッシュタグ・記号・空白を除去し、NFKCで統一。"""
    text = unicodedata.normalize("NFKC", text)
    text = URL_RE.sub("", text)
    text = HASHTAG_RE.sub("", text)
    text = re.sub(r"[\s、。，．,.!！?？「」『』（）()・…\-ー〜~:：;；/]", "", text)
    return text.lower()


def ngrams(text: str, n: int = 3) -> set[str]:
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def similarity(a: str, b: str) -> float:
    """正規化後の文字3-gram Jaccard係数（0〜1）。"""
    ga, gb = ngrams(normalize(a)), ngrams(normalize(b))
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def extract_hashtags(text: str) -> list[str]:
    return [h.replace("＃", "#") for h in HASHTAG_RE.findall(text)]
