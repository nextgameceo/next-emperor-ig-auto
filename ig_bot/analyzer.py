"""週次分析: 集計はPythonで決定的に行い、Claudeには集計結果の解釈と改善案だけを依頼する。

- 保存数・シェア数を重視したスコア（保存されるカルーセルほど伸びやすいため）
- サンプル数が基準未満なら「データ不足」とし、Claudeを呼ばず、生成への反映も行わない
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta

from .claude_client import parse_json_block

WEIGHTS = {"saved": 3, "shares": 3, "comments": 2, "likes": 1}


def score(m: dict) -> float | None:
    reach = m.get("reach") or 0
    if not reach:
        return None
    return sum(m.get(k, 0) * w for k, w in WEIGHTS.items()) / reach


def eligible_posts(posts: list[dict], now: datetime, lookback_days: int, min_age_hours: int) -> list[dict]:
    out = []
    for p in posts:
        if p.get("status") != "published" or "reach" not in (p.get("metrics") or {}):
            continue
        try:
            dt = datetime.fromisoformat(p["date"]).replace(hour=21, tzinfo=now.tzinfo)
        except (KeyError, ValueError):
            continue
        if now - timedelta(days=lookback_days) <= dt <= now - timedelta(hours=min_age_hours):
            out.append(p)
    return out


def _stats(group: list[dict]) -> dict:
    reach = [p["metrics"].get("reach", 0) for p in group]
    s = {"posts": len(group), "reach_total": sum(reach),
         "reach_median": statistics.median(reach) if reach else 0}
    for k in ("saved", "shares", "comments", "likes", "total_interactions"):
        s[f"{k}_total"] = sum(p["metrics"].get(k, 0) for p in group)
    sc = [x for x in (score(p["metrics"]) for p in group) if x is not None]
    s["score_median"] = round(statistics.median(sc), 4) if sc else None
    return s


def compute_stats(posts: list[dict], ca: dict) -> dict:
    by_aud: dict[str, list] = {"seeker": [], "company": []}
    themes: dict[str, list] = {}
    for p in posts:
        if p.get("audience") in by_aud:
            by_aud[p["audience"]].append(p)
        themes.setdefault(f"{p.get('audience')}:{p.get('theme')}", []).append(p)
    res = {"total_posts": len(posts),
           "audiences": {a: (_stats(g) if g else {"posts": 0}) for a, g in by_aud.items()},
           "themes": {k: _stats(g) for k, g in themes.items() if len(g) >= ca["min_posts_per_bucket"]}}
    ranked = sorted([p for p in posts if score(p["metrics"]) is not None],
                    key=lambda p: score(p["metrics"]), reverse=True)
    pick = lambda ps: [{"audience": p.get("audience"), "theme": p.get("theme"), "title": p.get("title"),
                        "score": round(score(p["metrics"]), 4), "reach": p["metrics"].get("reach"),
                        "saved": p["metrics"].get("saved"), "shares": p["metrics"].get("shares")} for p in ps]
    res["top_posts"] = pick(ranked[:3])
    res["bottom_posts"] = pick(ranked[-3:][::-1]) if len(ranked) > 3 else []
    enough = all(res["audiences"][a].get("posts", 0) >= ca["min_posts_per_group"] for a in by_aud)
    res["data_sufficiency"] = "sufficient" if enough else "insufficient"
    return res


ANALYSIS_PROMPT = """あなたはSNS運用アナリストです。NEXT EMPEROR公式Instagram（カルーセル投稿）の直近{days}日の集計結果を分析してください。

# 重要なルール
- 集計値に書かれていないことを事実として述べない。推測は「仮説」と明記する
- 投稿数が少ない項目は偶然の可能性が高いので、強い結論を出さない
- score = (保存×3 + シェア×3 + コメント×2 + いいね) ÷ リーチ。Instagram上の反応であり、応募・問い合わせの成果ではない
- 推奨テーマは次のIDからのみ選ぶ: seeker={seeker_themes} / company={company_themes}

# 集計結果(JSON)
{stats}

次のJSONだけを出力:
{{"summary": "3文以内の要約",
  "findings": [{{"claim": "発見", "evidence": "根拠となる数値", "confidence": "low|medium|high"}}],
  "recommendations": {{
    "seeker": {{"prefer_themes": [], "avoid_themes": [], "style_notes": ["表紙タイトルや構成の改善方針"]}},
    "company": {{"prefer_themes": [], "avoid_themes": [], "style_notes": []}}
  }}}}"""


def _sanitize(recs: dict, config: dict) -> dict:
    out = {}
    for a in ("seeker", "company"):
        valid = set(config["audiences"][a]["themes"])
        r = (recs or {}).get(a) or {}
        out[a] = {"prefer_themes": [t for t in r.get("prefer_themes", []) if t in valid][:3],
                  "avoid_themes": [t for t in r.get("avoid_themes", []) if t in valid][:3],
                  "style_notes": [str(s)[:100] for s in r.get("style_notes", [])][:5]}
    return out


def run_analysis(*, posts: list[dict], config: dict, claude, now: datetime) -> tuple[dict, str]:
    ca = config["analysis"]
    eligible = eligible_posts(posts, now, ca["lookback_days"], ca["min_post_age_hours"])
    stats = compute_stats(eligible, ca)
    insights = {"generated_at": now.isoformat(), "period_days": ca["lookback_days"],
                "data_sufficiency": stats["data_sufficiency"], "stats": stats,
                "recommendations": None, "summary": None, "findings": []}
    if stats["data_sufficiency"] == "sufficient" and claude is not None:
        prompt = ANALYSIS_PROMPT.format(
            days=ca["lookback_days"],
            seeker_themes=list(config["audiences"]["seeker"]["themes"]),
            company_themes=list(config["audiences"]["company"]["themes"]),
            stats=json.dumps(stats, ensure_ascii=False, indent=1))
        raw = claude.complete(config["claude"]["analysis_model"], prompt,
                              config["claude"]["max_tokens_analysis"], effort="medium", disable_thinking=False)
        try:
            r = parse_json_block(raw)
            insights["summary"] = str(r.get("summary", ""))[:500]
            insights["findings"] = [f for f in r.get("findings", []) if isinstance(f, dict)][:8]
            insights["recommendations"] = _sanitize(r.get("recommendations"), config)
        except (ValueError, json.JSONDecodeError):
            insights["data_sufficiency"] = "analysis_failed"
    return insights, render_report(insights)


def render_report(ins: dict) -> str:
    st = ins["stats"]
    lines = [f"# Instagram週次レポート（{ins['generated_at'][:10]}）", "",
             f"- 対象期間: 直近{ins['period_days']}日 / 分析対象 {st['total_posts']}件",
             f"- データ: {ins['data_sufficiency']}", ""]
    for a, label in (("seeker", "求職者向け"), ("company", "企業向け")):
        s = st["audiences"].get(a, {})
        if s.get("posts"):
            lines.append(f"- {label}: {s['posts']}件 / リーチ中央値 {s['reach_median']} / 保存 {s['saved_total']} / "
                         f"シェア {s['shares_total']} / スコア中央値 {s['score_median']}")
        else:
            lines.append(f"- {label}: 0件")
    if st.get("top_posts"):
        lines += ["", "## 反応が良かった投稿"]
        lines += [f"- {p['title']}（{p['theme']}）score={p['score']} 保存{p['saved']}" for p in st["top_posts"]]
    if ins.get("summary"):
        lines += ["", "## 要約", ins["summary"]]
    for f in ins.get("findings") or []:
        lines.append(f"- {f.get('claim')}（根拠: {f.get('evidence')} / 確度: {f.get('confidence')}）")
    if ins["data_sufficiency"] != "sufficient":
        lines += ["", "※ 投稿数がまだ少ないため、投稿内容への自動反映は行っていません。"]
    return "\n".join(lines) + "\n"
