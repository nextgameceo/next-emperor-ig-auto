#!/usr/bin/env bash
# 状態ブランチ（既定: ig-data）を作業ディレクトリに展開する。
# 使い方: scripts/state_checkout.sh <展開先ディレクトリ> [ブランチ名]
#
# - リモートにブランチがあれば、その最新コミットを detached HEAD で展開
# - 「ブランチが存在しない」と確認できた場合だけ、空の orphan ブランチを新規作成
# - 通信・認証エラー等で存在確認できない場合は失敗させる
#   （誤って空の状態から始めて既存の履歴を上書きするのを防ぐ）
set -euo pipefail

DIR="${1:?展開先ディレクトリを指定してください}"
BRANCH="${2:-ig-data}"
REMOTE="${STATE_REMOTE:-origin}"

if [ -e "$DIR" ]; then
  echo "::error::展開先 $DIR が既に存在します"
  exit 1
fi

set +e
git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
  git fetch -q --depth=1 "$REMOTE" "refs/heads/$BRANCH"
  git worktree add -q --detach "$DIR" FETCH_HEAD
  echo "状態ブランチ $BRANCH を展開しました（$(git -C "$DIR" rev-parse --short HEAD)）"
elif [ "$rc" -eq 2 ]; then
  echo "状態ブランチ $BRANCH が存在しないため、新規作成します"
  if [ "${STATE_FORCE_LEGACY_ORPHAN:-}" = "1" ] || ! git worktree add -q --orphan -b "$BRANCH" "$DIR" 2>/dev/null; then
    # 古いgit（2.42未満）向けの代替手順
    git worktree add -q --detach "$DIR" HEAD
    git -C "$DIR" checkout -q --orphan "$BRANCH"
    git -C "$DIR" rm -rfq --cached . >/dev/null
    find "$DIR" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  fi
else
  echo "::error::状態ブランチ $BRANCH の存在確認に失敗しました（git ls-remote 終了コード $rc）"
  exit 1
fi
