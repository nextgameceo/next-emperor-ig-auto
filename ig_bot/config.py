"""設定と実行環境の読み込み。"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "bot_config.json"
JST = timezone(timedelta(hours=9))

MODE_DRY_RUN = "dry_run"
MODE_LIVE = "live"


def load_config(path: str | os.PathLike | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def now_jst() -> datetime:
    return datetime.now(JST)


def bot_mode() -> str:
    """環境変数 IG_BOT_MODE が厳密に 'live' のときだけ本番投稿する。それ以外はすべて dry_run。"""
    return MODE_LIVE if os.environ.get("IG_BOT_MODE") == MODE_LIVE else MODE_DRY_RUN


def require_env(*names: str) -> dict[str, str]:
    """必須の環境変数を取得。値は返すが、ログには名前しか出さない。"""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"必須の環境変数が未設定です: {', '.join(missing)}")
    return {n: os.environ[n] for n in names}
