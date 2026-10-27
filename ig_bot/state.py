"""実行間で保持する状態（投稿履歴・下書き・指標・分析結果・画像）。

GitHub Actions では専用ブランチ `ig-data` を ./state に展開し、処理後にコミット＆プッシュする。
画像は state/images/<日付>/ に置き、公開リポジトリの raw URL として Instagram に渡す。
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

POSTS_FILE = "posts.json"
META_FILE = "meta.json"
INSIGHTS_FILE = "insights.json"
REPORTS_DIR = "reports"
IMAGES_DIR = "images"


class StateStore:
    def __init__(self, root: str | os.PathLike):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _read(self, name: str, default):
        p = self.root / name
        if not p.exists():
            return default
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _write(self, name: str, data) -> None:
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)

    # ---------- posts ----------
    # posts.json = {"posts": {date: record}}  record.status: draft | publishing | published | failed
    def load_posts(self) -> dict:
        data = self._read(POSTS_FILE, {"posts": {}})
        data.setdefault("posts", {})
        return data

    def save_posts(self, data: dict) -> None:
        self._write(POSTS_FILE, data)

    def get_post(self, date: str) -> dict | None:
        return self.load_posts()["posts"].get(date)

    def put_post(self, date: str, record: dict) -> None:
        data = self.load_posts()
        data["posts"][date] = record
        self.save_posts(data)

    def posts_sorted(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        posts = list(self.load_posts()["posts"].values())
        if statuses:
            posts = [p for p in posts if p.get("status") in statuses]
        return sorted(posts, key=lambda p: p.get("date") or "", reverse=True)

    def images_dir(self, date: str) -> pathlib.Path:
        return self.root / IMAGES_DIR / date

    # ---------- meta / insights / reports ----------
    def load_meta(self) -> dict:
        return self._read(META_FILE, {})

    def save_meta(self, meta: dict) -> None:
        self._write(META_FILE, meta)

    def load_insights(self) -> dict | None:
        return self._read(INSIGHTS_FILE, None)

    def save_insights(self, insights: dict) -> None:
        self._write(INSIGHTS_FILE, insights)

    def write_report(self, name: str, markdown: str) -> pathlib.Path:
        p = self.root / REPORTS_DIR / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(markdown, encoding="utf-8")
        return p
