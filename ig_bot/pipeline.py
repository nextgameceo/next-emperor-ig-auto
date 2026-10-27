"""投稿の準備（生成＋画像化）・公開・指標収集の処理本体。

ワークフローは prepare → 状態ブランチへ保存（画像がGitHubに上がる）→ publish の順に実行する。
publish は保存後のコミットSHAを使った raw URL を Instagram に渡す。
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta

from . import generator, renderer, validator
from .config import MODE_LIVE
from .ig_client import IGClient, IGError, url_reachable
from .state import StateStore


def today(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def in_post_window(now: datetime, config: dict, check_weekday: bool = True) -> tuple[bool, str]:
    sc = config["schedule"]
    if check_weekday and now.weekday() not in sc["post_weekdays"]:
        return False, f"本日（{'月火水木金土日'[now.weekday()]}曜）は投稿日ではありません"
    if not (sc["earliest_post_hour_jst"] <= now.hour <= sc["latest_post_hour_jst"]):
        return False, f"投稿時間帯外です（{now:%H:%M} JST）"
    return True, ""


def history_for_generation(store: StateStore, date: str) -> list[dict]:
    """重複回避・対象バランスに使う履歴（公開済み＋他日の下書き）。"""
    posts = store.posts_sorted(("published", "draft", "publishing"))
    return [p for p in posts if p.get("date") != date]


# ------------------------------------------------------------------ prepare
def run_prepare(*, store: StateStore, config: dict, claude, now: datetime, scheduled: bool,
                force: bool) -> dict:
    date = today(now)
    if scheduled:
        ok, why = in_post_window(now, config)
        if not ok:
            return {"status": "skipped", "reason": why, "date": date}
    existing = store.get_post(date)
    if existing and existing.get("status") in ("published", "publishing"):
        return {"status": "skipped", "reason": "本日は投稿済みです", "date": date, "record": existing}
    if existing and existing.get("status") == "draft" and not force:
        return {"status": "exists", "reason": "本日の下書きを再利用します", "date": date, "record": existing}

    result = generator.generate_post(claude, config, history_for_generation(store, date),
                                     store.load_insights(), date)
    if result["status"] != "ok":
        rec = {"date": date, "status": "failed", "audience": result["audience"], "theme": result["theme"],
               "attempts": result["attempts"], "created_at": now.isoformat()}
        store.put_post(date, rec)
        return {"status": "failed", "date": date, "record": rec}

    post = result["post"]
    img_dir = store.images_dir(date)
    if img_dir.exists():
        shutil.rmtree(img_dir)
    paths = renderer.render_post(post, config, img_dir, seed=date)
    rec = {
        "date": date, "status": "draft", "audience": post["audience"], "theme": post["theme"],
        "title": post["title"], "subtitle": post["subtitle"], "slides": post["slides"],
        "caption": post["caption"], "hashtags": post["hashtags"],
        "caption_full": validator.compose_caption(post),
        "history_key": validator.history_key(post),
        "images": [str(p.relative_to(store.root)) for p in paths],
        "attempts": result["attempts"], "created_at": now.isoformat(),
    }
    store.put_post(date, rec)
    return {"status": "ok", "date": date, "record": rec}


# ------------------------------------------------------------------ publish
def raw_url(config: dict, sha: str, rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{config['brand']['github_repo']}/{sha}/{rel_path}"


def _already_posted(ig: IGClient, rec: dict, now: datetime) -> dict | None:
    """Instagram上で本日すでに同じキャプションの投稿があれば返す（二重投稿防止）。"""
    head = (rec.get("caption") or "")[:40]
    for m in ig.recent_media(limit=10):
        ts = m.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("+0000", "+00:00").replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - dt < timedelta(hours=20) and head and (m.get("caption") or "").startswith(head):
            return m
    return None


def run_publish(*, mode: str, store: StateStore, config: dict, ig: IGClient | None, now: datetime,
                sha: str, scheduled: bool, sleep=time.sleep, reachable=url_reachable) -> dict:
    date = today(now)
    rec = store.get_post(date)
    if not rec or rec.get("status") not in ("draft", "publishing"):
        status = (rec or {}).get("status", "なし")
        return {"status": "skipped", "reason": f"公開できる本日の下書きがありません（状態: {status}）", "date": date}
    urls = [raw_url(config, sha, p) for p in rec["images"]]
    if mode != MODE_LIVE or ig is None:
        return {"status": "dry_run", "date": date, "record": rec, "urls": urls}
    if scheduled:
        ok, why = in_post_window(now, config)
        if not ok:
            return {"status": "skipped", "reason": why, "date": date}

    ic = config["instagram"]
    dup = _already_posted(ig, rec, now)
    if dup:
        rec.update(status="published", media_id=dup["id"], permalink=dup.get("permalink"),
                   published_at=dup.get("timestamp"), note="Instagram上で投稿済みを確認")
        store.put_post(date, rec)
        return {"status": "skipped", "reason": "Instagram上で本日の投稿を確認済み", "date": date, "record": rec}

    # 公開リポジトリのraw URLが取得できるまで待つ（非公開だと取得できない）
    for i in range(ic["image_url_wait_max"]):
        if all(reachable(u) for u in urls):
            break
        sleep(ic["image_url_wait_seconds"])
    else:
        raise IGError("画像URLを外部から取得できません。GitHubリポジトリが公開(Public)になっているか確認してください")

    rec["status"] = "publishing"
    store.put_post(date, rec)
    children = [ig.create_image_item(u) for u in urls]
    for c in children:
        ig.wait_ready(c, ic["container_poll_seconds"], ic["container_poll_max"])
    carousel = ig.create_carousel(children, rec["caption_full"])
    ig.wait_ready(carousel, ic["container_poll_seconds"], ic["container_poll_max"])
    media_id = ig.publish(carousel)
    info = {}
    try:
        info = ig.get_media(media_id)
    except IGError as e:
        print(f"[ig] 投稿は完了しましたが、URLの取得に失敗しました: {e}")
    rec.update(status="published", media_id=media_id, permalink=info.get("permalink"),
               published_at=info.get("timestamp") or now.isoformat(), image_urls=urls)
    store.put_post(date, rec)
    return {"status": "published", "date": date, "record": rec}


# ------------------------------------------------------------------ collect
def run_collect(*, store: StateStore, config: dict, ig: IGClient, now: datetime) -> dict:
    cc = config["collect"]
    data = store.load_posts()
    updated = 0
    for date, rec in data["posts"].items():
        if rec.get("status") != "published" or not rec.get("media_id"):
            continue
        try:
            d = datetime.fromisoformat(date).replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
        if now - d > timedelta(days=cc["lookback_days"]):
            continue
        try:
            m = ig.media_insights(rec["media_id"], cc["metrics"])
        except IGError as e:
            if e.token_invalid:
                raise
            print(f"[collect] {date}: 指標を取得できませんでした: {e}")
            continue
        if m:
            rec["metrics"] = m
            rec["metrics_updated_at"] = now.isoformat()
            updated += 1
    store.save_posts(data)
    return {"status": "ok", "updated": updated}


# ------------------------------------------------------------------ token
def check_token(ig: IGClient, store: StateStore, config: dict, now: datetime) -> dict:
    """トークンの有効期限・データアクセス期限を確認し meta に記録する。"""
    info = ig.debug_token()
    meta = store.load_meta()
    res = {"valid": bool(info.get("is_valid")), "expires_at": info.get("expires_at"),
           "data_access_expires_at": info.get("data_access_expires_at"), "checked_at": now.isoformat()}
    meta["token"] = res
    store.save_meta(meta)
    warn_days = config["token"]["warn_days_before_expiry"]
    res["warnings"] = []
    if not res["valid"]:
        res["warnings"].append("Instagramのアクセストークンが無効です。再発行が必要です")
    for key, label in (("expires_at", "トークン"), ("data_access_expires_at", "データアクセス")):
        ts = res.get(key)
        if ts:  # 0 は「期限なし」
            left = datetime.fromtimestamp(int(ts), tz=now.tzinfo) - now
            if left < timedelta(days=warn_days):
                res["warnings"].append(f"{label}の期限まで残り{max(left.days, 0)}日です。トークンの再発行が必要です")
    return res
