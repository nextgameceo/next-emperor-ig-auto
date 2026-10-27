"""投稿前の品質チェック（ルールベース・決定的）。1つでも問題があれば投稿しない。"""
from __future__ import annotations

import re

from . import textutil


def allowed_hashtags(config: dict) -> set[str]:
    tags = {"#NEXTEMPEROR", "#ネクストエンペラー"}
    for a in config["audiences"].values():
        tags.update(a["hashtags"])
    return tags


def all_text(post: dict) -> str:
    parts = [post.get("title", ""), post.get("subtitle", "")]
    for s in post.get("slides", []):
        parts += [s.get("heading", ""), s.get("body", "")]
    parts.append(post.get("caption", ""))
    return "\n".join(parts)


def compose_caption(post: dict) -> str:
    """Instagramのキャプション（本文＋空行＋ハッシュタグ）。"""
    cap = post["caption"].strip()
    tags = post.get("hashtags") or []
    return cap + ("\n\n" + " ".join(tags) if tags else "")


def _plain(s) -> str:
    return str(s or "").replace("\n", "")


def validate(post: dict, history: list[str], config: dict) -> list[str]:
    pc = config["post"]
    issues: list[str] = []

    title, subtitle, caption = post.get("title", ""), post.get("subtitle", ""), post.get("caption", "")
    slides = post.get("slides") or []
    if not _plain(title).strip():
        issues.append("表紙タイトルが空です")
    if len(_plain(title)) > pc["max_title_chars"]:
        issues.append(f"表紙タイトルが長すぎます（{len(_plain(title))}文字 > {pc['max_title_chars']}）")
    if len(_plain(subtitle)) > pc["max_subtitle_chars"]:
        issues.append(f"サブタイトルが長すぎます（{len(_plain(subtitle))}文字 > {pc['max_subtitle_chars']}）")
    if not (pc["content_slides_min"] <= len(slides) <= pc["content_slides_max"]):
        issues.append(f"本文スライドは{pc['content_slides_min']}〜{pc['content_slides_max']}枚にしてください（{len(slides)}枚）")
    for i, s in enumerate(slides, start=1):
        h, b = _plain(s.get("heading")), _plain(s.get("body"))
        if not h.strip() or not b.strip():
            issues.append(f"スライド{i}の見出しか本文が空です")
        if len(h) > pc["max_heading_chars"]:
            issues.append(f"スライド{i}の見出しが長すぎます（{len(h)}文字 > {pc['max_heading_chars']}）")
        if len(b) > pc["max_slide_body_chars"]:
            issues.append(f"スライド{i}の本文が長すぎます（{len(b)}文字 > {pc['max_slide_body_chars']}）")
    if not caption.strip():
        issues.append("キャプションが空です")
    if len(caption) > pc["max_caption_chars"]:
        issues.append(f"キャプションが長すぎます（{len(caption)}文字 > {pc['max_caption_chars']}）")

    text = all_text(post)
    if textutil.URL_RE.search(text) or textutil.BARE_DOMAIN_RE.search(text):
        issues.append("URL/ドメインが含まれています（Instagramではリンクが機能しないため「プロフィールのリンク」と書く）")
    if "@" in text or "＠" in text:
        issues.append("メンション記号が含まれています")
    if textutil.extract_hashtags(text):
        issues.append("本文やキャプション中にハッシュタグがあります（hashtags欄にだけ書く）")

    tags = list(post.get("hashtags") or [])
    if not (pc["min_hashtags"] <= len(tags) <= pc["max_hashtags"]):
        issues.append(f"ハッシュタグは{pc['min_hashtags']}〜{pc['max_hashtags']}個にしてください（{len(tags)}個）")
    if len(set(tags)) != len(tags):
        issues.append("ハッシュタグが重複しています")
    bad = [t for t in tags if t not in allowed_hashtags(config)]
    if bad:
        issues.append(f"許可リスト外のハッシュタグ: {', '.join(bad)}")

    for pat in config.get("banned_patterns", []):
        m = re.search(pat, text)
        if m:
            issues.append(f"禁止表現を含みます: 「{m.group(0)}」")

    # 重複・類似（表紙＋見出しで比較）
    key = _plain(title) + " " + " ".join(_plain(s.get("heading")) for s in slides)
    nk = textutil.normalize(key)
    for h in history[: pc["history_compare_count"]]:
        if textutil.normalize(h) == nk:
            issues.append("過去の投稿と同じ内容です")
            break
        sim = textutil.similarity(key, h)
        if sim > pc["max_similarity"]:
            issues.append(f"過去の投稿と似すぎています（類似度{sim:.2f}）: {textutil.normalize(h)[:30]}…")
            break
    return issues


def history_key(post: dict) -> str:
    return _plain(post.get("title")) + " " + " ".join(_plain(s.get("heading")) for s in post.get("slides", []))
