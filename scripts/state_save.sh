#!/usr/bin/env bash
# 状態ディレクトリの変更をコミットし、状態ブランチへ push する。
# 使い方: scripts/state_save.sh <状態ディレクトリ> <コミットメッセージ> [ブランチ名]
#
# 安全策:
# - 保存前にすべての JSON を検証し、壊れていれば保存しない
# - push はリトライ上限つき（既定3回）。リモートが先に進んでいた場合だけ rebase する
# - rebase が競合したら中止して失敗（同じ競合で無駄に再試行しない）
# - rebase 後の JSON が壊れていたら rebase 前に戻して失敗
# - 強制 push（--force）は一切使わない（他の実行の記録を消さない）
set -uo pipefail

DIR="${1:?状態ディレクトリを指定してください}"
MSG="${2:?コミットメッセージを指定してください}"
BRANCH="${3:-ig-data}"
REMOTE="${STATE_REMOTE:-origin}"
RETRIES="${STATE_PUSH_RETRIES:-3}"
SLEEP_UNIT="${STATE_RETRY_SLEEP:-5}"

if [ ! -d "$DIR" ]; then
  echo "状態ディレクトリがないため保存しません"
  exit 0
fi
cd "$DIR" || exit 1

validate_json() {
  python3 - <<'PY'
import json, pathlib, sys
bad = []
for p in pathlib.Path(".").rglob("*.json"):
    if ".git" in p.parts:
        continue
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        bad.append(f"{p}: {e}")
if bad:
    print("JSONが不正です: " + " / ".join(bad))
    sys.exit(1)
PY
}

if ! validate_json; then
  echo "::error::状態ファイルが壊れているため保存を中止しました"
  exit 1
fi

git add -A
if git diff --cached --quiet; then
  echo "状態の変更なし"
  exit 0
fi
if ! git commit -q -m "$MSG"; then
  echo "::error::状態のコミットに失敗しました"
  exit 1
fi

for i in $(seq 1 "$RETRIES"); do
  if git push -q "$REMOTE" "HEAD:refs/heads/$BRANCH"; then
    echo "状態を保存しました（$(git rev-parse --short HEAD)）"
    exit 0
  fi
  echo "push失敗（${i}/${RETRIES}）"
  [ "$i" -eq "$RETRIES" ] && break
  sleep $((i * SLEEP_UNIT))
  # リモートが先に進んでいれば取り込む。ブランチ未作成（初回）や通信エラーなら、そのまま push を再試行
  if git fetch -q --depth=50 "$REMOTE" "refs/heads/$BRANCH" 2>/dev/null; then
    before=$(git rev-parse HEAD)
    if ! git rebase -q FETCH_HEAD >/dev/null 2>&1; then
      git rebase --abort >/dev/null 2>&1
      git reset -q --hard "$before"
      echo "::error::状態ブランチとの競合のため保存できませんでした（強制上書きはしていません）"
      exit 1
    fi
    if ! validate_json; then
      git reset -q --hard "$before"
      echo "::error::取り込み後の状態ファイルが不正になるため保存を中止しました"
      exit 1
    fi
  fi
done

echo "::error::状態ブランチ $BRANCH への保存に失敗しました（次回の投稿はX上の投稿確認で二重投稿を防止します）"
exit 1
