"""エントリポイント: python -m ig_bot {prepare|publish|collect|analyze} [--state DIR] [--scheduled]

モードは環境変数 IG_BOT_MODE で決まる（'live' 以外はすべて dry_run＝Instagramに投稿しない）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import analyzer
from .claude_client import ClaudeClient
from .config import bot_mode, load_config, now_jst, require_env, MODE_LIVE
from .ig_client import IGClient, IGError
from .pipeline import check_token, run_collect, run_prepare, run_publish, seconds_until_post_time
from .state import StateStore


def _summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


def _ig(config: dict) -> IGClient:
    env = require_env("IG_ACCESS_TOKEN")
    uid = os.environ.get("IG_USER_ID") or config["brand"]["instagram_user_id"]
    return IGClient(env["IG_ACCESS_TOKEN"], uid, config["instagram"]["graph_version"])


def _post_summary(title: str, rec: dict, urls: list[str] | None = None) -> str:
    lines = [f"## {title}", "", f"**表紙**: {rec.get('title', '').replace(chr(10), ' ')}",
             f"**対象/テーマ**: {rec.get('audience')} / {rec.get('theme')}", ""]
    for i, s in enumerate(rec.get("slides") or [], start=1):
        lines.append(f"{i}. **{s['heading']}** — {s['body']}")
    lines += ["", "**キャプション**", "", "```", rec.get("caption_full", ""), "```"]
    if rec.get("permalink"):
        lines.append(f"\n投稿: {rec['permalink']}")
    if urls:
        lines += ["", "**画像**", ""] + [f"![slide{i}]({u})" for i, u in enumerate(urls, start=1)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_bot")
    ap.add_argument("command", choices=["prepare", "publish", "collect", "analyze"])
    ap.add_argument("--state", default=os.environ.get("IG_BOT_STATE_DIR", "state"))
    ap.add_argument("--scheduled", action="store_true", help="スケジュール起動（曜日・時間帯チェックを有効化）")
    ap.add_argument("--force", action="store_true", help="prepare: 本日の下書きがあっても作り直す")
    ap.add_argument("--sha", default="", help="publish: 画像を含む状態ブランチのコミットSHA")
    args = ap.parse_args(argv)

    config = load_config()
    store = StateStore(args.state)
    mode = bot_mode()
    now = now_jst()
    print(f"[ig_bot] command={args.command} mode={mode} now={now.isoformat()}")

    try:
        if args.command == "prepare":
            claude = ClaudeClient(require_env("ANTHROPIC_API_KEY")["ANTHROPIC_API_KEY"])
            r = run_prepare(store=store, config=config, claude=claude, now=now,
                            scheduled=args.scheduled, force=args.force)
            u = claude.usage
            if u["calls"]:
                print(f"[cost] Claude API: {u['calls']}回 入力{u['input_tokens']}tok / 出力{u['output_tokens']}tok")
            print(f"[prepare] {r['status']} {r.get('reason', '')}")
            if r["status"] == "failed":
                for a in r["record"]["attempts"]:
                    print(f"  試行{a['attempt']}: {a.get('error') or a.get('issues')}")
                _summary("## 投稿の生成に失敗しました（今回は投稿しません）\n\n" +
                         "\n".join(f"- 試行{a['attempt']}: {a.get('error') or a.get('issues')}"
                                   for a in r["record"]["attempts"]))
                return 1
            return 0

        if args.command == "publish":
            if not args.sha:
                print("::error::--sha が必要です")
                return 1
            if args.scheduled:
                rec = store.get_post(now.strftime("%Y-%m-%d"))
                if rec and rec.get("status") == "draft":
                    wait = seconds_until_post_time(now, config)
                    if wait < 0:
                        print("[publish] 投稿時刻まで長すぎるため待機しません（後の定期実行に任せます）")
                    elif wait > 0:
                        print(f"[publish] 投稿時刻（{config['schedule']['post_hour_jst']}:00 JST）まで "
                              f"{int(wait // 60)}分 待機します")
                        sys.stdout.flush()
                        time.sleep(wait + 5)
                        now = now_jst()
            ig = _ig(config) if mode == MODE_LIVE else None
            if mode != MODE_LIVE:
                print("[ig_bot] dry_run: Instagramへの投稿は行いません（画像と文章の確認のみ）")
            r = run_publish(mode=mode, store=store, config=config, ig=ig, now=now, sha=args.sha,
                            scheduled=args.scheduled)
            print(f"[publish] {r['status']} {r.get('reason', '')}")
            if r["status"] == "dry_run":
                _summary(_post_summary("下書き（dry_run：Instagramには未投稿）", r["record"], r["urls"]))
            elif r["status"] == "published":
                print(f"[publish] 投稿しました: {r['record'].get('permalink')}")
                _summary(_post_summary("Instagramに投稿しました", r["record"], r["record"].get("image_urls")))
            else:
                _summary(f"## 投稿なし\n\n{r.get('reason', '')}")
            return 0

        if args.command == "collect":
            if not os.environ.get("IG_ACCESS_TOKEN"):
                print("[collect] IG_ACCESS_TOKEN 未設定のためスキップ")
                return 0
            r = run_collect(store=store, config=config, ig=_ig(config), now=now)
            print(f"[collect] 指標を更新: {r['updated']}件")
            return 0

        if args.command == "analyze":
            key = os.environ.get("ANTHROPIC_API_KEY")
            claude = ClaudeClient(key) if key else None
            insights, report = analyzer.run_analysis(posts=store.posts_sorted(), config=config,
                                                     claude=claude, now=now)
            store.save_insights(insights)
            store.write_report(f"{now:%Y-%m-%d}.md", report)
            _summary(report)
            print(report)
            rc = 0
            if os.environ.get("IG_ACCESS_TOKEN"):
                try:
                    t = check_token(_ig(config), store, config, now)
                except IGError as e:
                    if e.token_invalid:
                        raise
                    print(f"[analyze] トークン期限の確認はスキップしました: {e}")
                    t = {"warnings": []}
                for w in t["warnings"]:
                    print(f"::error::{w}（docs/OPERATIONS.md の「トークンの再発行」を参照）")
                    _summary(f"\n**⚠️ {w}**")
                    rc = 1  # 失敗扱いにしてGitHubからメール通知させる
            return rc
    except IGError as e:
        msg = str(e)
        if e.token_invalid:
            msg = "Instagramのアクセストークンが無効です。再発行してSecretsの IG_ACCESS_TOKEN を更新してください"
        print(f"::error::{msg}")
        _summary(f"## エラー\n\n{msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
