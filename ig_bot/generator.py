"""カルーセル投稿の生成（Claude）→ ルール検証 → 描画可否チェック → AIレビュー。

Instagramでは誤った内容を出すより「投稿しない」ほうが安全なので、
すべての試行が不合格なら固定文へのフォールバックはせず status=failed を返す。
"""
from __future__ import annotations

import json
import random

from . import renderer, validator
from .claude_client import ClaudeClient, parse_json_block


# ---------------------------------------------------------------- 選定ロジック
def choose_audience(history: list[dict], config: dict) -> str:
    """直近の投稿の対象比率を、設定の比率（既定 求職者2：企業1）に近づける。"""
    ratio = config["post"]["audience_ratio"]
    window = config["post"]["audience_balance_window"]
    recent = [h.get("audience") for h in history[:window] if h.get("audience") in ratio]
    if not recent:
        return "seeker"
    total = sum(ratio.values())
    # 目標比率からの不足が最も大きい対象を選ぶ
    deficits = {a: ratio[a] / total - recent.count(a) / len(recent) for a in ratio}
    best = max(deficits.values())
    tied = [a for a in ratio if abs(deficits[a] - best) < 1e-9]
    if len(tied) == 1:
        return tied[0]
    return next((a for a in tied if a != recent[0]), tied[0])


def usable_insights(insights: dict | None) -> dict | None:
    if not insights or insights.get("data_sufficiency") != "sufficient":
        return None
    return insights


def choose_theme(config: dict, audience: str, history: list[dict], insights: dict | None, seed: str) -> str:
    themes = list(config["audiences"][audience]["themes"].keys())
    window = config["post"]["recent_theme_window"]
    recent = [h.get("theme") for h in history if h.get("audience") == audience][:window]
    candidates = [t for t in themes if t not in recent] or themes
    rec = ((usable_insights(insights) or {}).get("recommendations") or {}).get(audience, {})
    prefer, avoid = set(rec.get("prefer_themes", [])), set(rec.get("avoid_themes", []))
    weights = [(3.0 if t in prefer else 1.0) * (0.3 if t in avoid else 1.0) for t in candidates]
    rng = random.Random(seed)  # 日付をシードにして再実行時も同じテーマを選ぶ
    return rng.choices(candidates, weights=weights, k=1)[0]


# ---------------------------------------------------------------- プロンプト
SYSTEM = (
    "あなたはNEXT EMPERORの公式Instagramアカウントの投稿担当です。"
    "保存したくなる、読み手の役に立つ具体的なカルーセル投稿を作ります。"
    "与えられた事実だけを使い、架空の求人・提携企業・採用実績・利用者の声・数値は絶対に書きません。"
    "誇大表現や断定的な約束をせず、夜職の経験を見下さず、敬意をもって前向きに書きます。"
)


def build_prompt(config: dict, audience: str, theme: str, history_titles: list[str],
                 insights: dict | None, feedback: list[str]) -> str:
    aud = config["audiences"][audience]
    pc = config["post"]
    facts = "\n".join(f"- {f}" for f in config["brand"]["facts"])
    recent = "\n".join(f"- {t[:60]}" for t in history_titles[:20]) or "- （なし）"
    style = ""
    ins = usable_insights(insights)
    if ins:
        notes = ((ins.get("recommendations") or {}).get(audience) or {}).get("style_notes", [])
        if notes:
            style = "\n# 過去の反応分析から得た改善方針\n" + "\n".join(f"- {n}" for n in notes[:5])
    fb = ""
    if feedback:
        fb = "\n# 前回案の不合格理由（必ず解消すること）\n" + "\n".join(f"- {x}" for x in feedback)
    return f"""# ブランド
名称: {config['brand']['name']}
メッセージ: {config['brand']['tagline']}

# 使ってよい事実（これ以外の事実・数値・事例は書かない）
{facts}

# 今回の投稿
対象: {aud['label']}
テーマ: {aud['themes'][theme]}
形式: Instagramのカルーセル（表紙1枚 → 本文スライド{pc['content_slides_min']}〜{pc['content_slides_max']}枚 → 最後に固定の案内スライド）
{style}
# 最近の投稿のタイトル（これらと切り口・表現を重複させない）
{recent}
{fb}
# 各パーツの条件
- title: 表紙の大見出し。{pc['max_title_chars']}文字以内。思わず開きたくなる一文。意味の切れ目で改行(\\n)を1つ入れて2行にし、各行9文字前後に
- subtitle: 表紙の補足。{pc['max_subtitle_chars']}文字以内（例:「〇〇する4つのコツ」のように中身の予告）
- slides: 本文スライド{pc['content_slides_min']}〜{pc['content_slides_max']}枚。1枚1メッセージ
  - heading: {pc['max_heading_chars']}文字以内の見出し
  - body: {pc['max_slide_body_chars']}文字以内。具体的な行動や考え方を、やさしい言葉で2〜3文
  - 最後の本文スライドは、内容のまとめか「まず何をすればいいか」で締める
- caption: キャプション。{pc['max_caption_chars']}文字以内。1行目で読む理由を示し、要点を短く振り返り、最後は「保存して見返してください」と「詳しくはプロフィールのリンクから」で締める。改行を使って読みやすく。絵文字は0〜3個まで
- hashtags: 次の候補から内容に合うものを{pc['min_hashtags']}〜{pc['max_hashtags']}個: {' '.join(aud['hashtags'])} #NEXTEMPEROR

# 禁止
- URL・ドメイン名・@メンション（Instagramではリンクが押せないため「プロフィールのリンクから」と書く）
- 実績・人数・社数・割合・給与などの数字、「必ず」「絶対」「保証」「無料」「稼げる」「高収入」「DMください」などの表現
- 本文・キャプション中のハッシュタグ（hashtags欄にだけ書く）

次のJSONだけを出力してください:
{{"title": "1行目\\n2行目", "subtitle": "...", "slides": [{{"heading": "...", "body": "..."}}], "caption": "...", "hashtags": ["#..."]}}"""


REVIEW_PROMPT = """次のInstagramカルーセル投稿案を審査してください。

# 使ってよい事実
{facts}

# 投稿案
{text}

# 審査基準（1つでも該当すれば不合格）
1. 上の事実にない求人・提携企業・採用実績・利用者の声・数値を述べている、または暗示している
2. 誇大・断定的な約束（必ず転職できる等）がある
3. 夜職や求職者・企業を見下す、差別的、不快な表現がある
4. 日本語として不自然、意味が通らない、スライド間で話がつながっていない
5. 対象（{audience}）に向けた内容になっていない

次のJSONだけを出力: {{"ok": true または false, "issues": ["不合格理由"]}}"""


def _review_text(post: dict) -> str:
    lines = [f"[表紙] {post['title']} / {post.get('subtitle', '')}"]
    for i, s in enumerate(post["slides"], start=1):
        lines.append(f"[{i}] {s['heading']}: {s['body']}")
    lines.append(f"[キャプション]\n{validator.compose_caption(post)}")
    return "\n".join(lines)


def ai_review(claude: ClaudeClient, config: dict, post: dict, audience_label: str) -> tuple[bool, list[str]]:
    facts = "\n".join(f"- {f}" for f in config["brand"]["facts"])
    raw = claude.complete(config["claude"]["review_model"],
                          REVIEW_PROMPT.format(facts=facts, text=_review_text(post), audience=audience_label),
                          config["claude"]["max_tokens_review"])
    try:
        r = parse_json_block(raw)
    except (ValueError, json.JSONDecodeError):
        return False, ["AIレビュー応答を解析できません"]
    ok = r.get("ok") is True
    return ok, ([str(i) for i in r.get("issues", [])][:5] if not ok else [])


def _normalize(data: dict) -> dict:
    slides = []
    for s in data.get("slides") or []:
        if isinstance(s, dict):
            slides.append({"heading": str(s.get("heading", "")).strip(), "body": str(s.get("body", "")).strip()})
    tags = []
    for t in data.get("hashtags") or []:
        t = str(t).strip().replace("＃", "#")
        if t and not t.startswith("#"):
            t = "#" + t
        if t:
            tags.append(t)
    return {"title": str(data.get("title", "")).strip().replace("\\n", "\n"),
            "subtitle": str(data.get("subtitle", "")).strip(),
            "slides": slides, "caption": str(data.get("caption", "")).strip(), "hashtags": tags}


# ---------------------------------------------------------------- メイン
def generate_post(claude: ClaudeClient, config: dict, history: list[dict], insights: dict | None,
                  date_str: str) -> dict:
    """戻り値: {status: ok|failed, post, audience, theme, attempts}"""
    pc = config["post"]
    history_titles = [h.get("history_key") or h.get("title", "") for h in history]
    audience = choose_audience(history, config)
    theme = choose_theme(config, audience, history, insights, seed=f"{date_str}-{audience}")
    label = config["audiences"][audience]["label"]

    attempts: list[dict] = []
    feedback: list[str] = []
    for i in range(pc["max_generation_attempts"]):
        try:
            raw = claude.complete(config["claude"]["generation_model"],
                                  build_prompt(config, audience, theme, history_titles, insights, feedback),
                                  config["claude"]["max_tokens_generation"], system=SYSTEM)
            post = _normalize(parse_json_block(raw))
        except Exception as e:  # 解析失敗・API失敗は不合格扱いで次へ
            attempts.append({"attempt": i + 1, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            feedback = ["出力が指定のJSON形式ではありませんでした"]
            continue
        post["audience"], post["theme"] = audience, theme
        issues = validator.validate(post, history_titles, config)
        if not issues:
            issues = renderer.text_fits(post)
        if not issues:
            ok, review_issues = ai_review(claude, config, post, label)
            issues = [f"AIレビュー: {x}" for x in review_issues] if not ok else []
        attempts.append({"attempt": i + 1, "title": post.get("title"), "issues": issues})
        if not issues:
            return {"status": "ok", "post": post, "audience": audience, "theme": theme, "attempts": attempts}
        feedback = issues
    return {"status": "failed", "audience": audience, "theme": theme, "attempts": attempts}
