# 運用マニュアル（スマホでOK）

## 必要な設定（済）

Settings → Secrets and variables → Actions → **Secrets**

| 名前 | 内容 |
|---|---|
| `IG_ACCESS_TOKEN` | Facebookページ「Next Emperor」の期限なしページトークン |
| `IG_USER_ID` | `17841468360923129`（@nextemperor.llc） |
| `ANTHROPIC_API_KEY` | Claude APIキー |

リポジトリは **Public（公開）** にしておくこと（画像URLをInstagramが取りに来るため）。

## 1. まずは下書きテスト（投稿されない）

1. リポジトリの **Actions** タブ →「NEXT EMPEROR Instagram Bot」→ **Run workflow**
2. task は `post` のまま、チェックは何も付けずに実行
3. 終わったら実行結果を開くと、**Summary** に文章と6〜7枚の画像が表示される

もう一度作り直したいときは `force_new_draft` にチェックして実行。

## 2. 本番に切り替える

Settings → Secrets and variables → Actions → **Variables** タブ → New repository variable

- Name: `IG_BOT_MODE`
- Value: `live`

（Secretsタブに同じ名前・値で登録してもOK）

以降、**毎日21:00**に自動で投稿されます。止めたいときは値を `off` に変えるだけ（下書き作成のみになる）。

すぐに1本投稿したいときは、Run workflow で `allow_live_post` にチェックして実行（時間帯の制限なし）。

## 3. 反応の確認

- 毎週月曜の「analyze」実行結果の Summary に週次レポート
- `ig-data` ブランチの `reports/` にも保存
- 投稿が4本×2対象ほど溜まると、反応の良いテーマを優先するようになる

## 4. 失敗したとき

失敗するとGitHubからメールが届きます。Actionsの実行ログ（赤い×）を開いて、表示されたエラー文をClaudeに見せればOK。

よくある原因:
- 「画像URLを外部から取得できません」→ リポジトリが非公開になっている
- 「アクセストークンが無効」→ 下のトークン再発行
- 「投稿の生成に失敗」→ 禁止表現などで3回とも不合格。その回は投稿されず、次回は普通に動く

## 5. トークンの再発行（数か月に1回の可能性あり）

Metaの仕様で「データアクセス期限」（約3か月）があります。期限の3週間前になると、月曜の analyze がエラーになってメールで知らせます。

1. developers.facebook.com/tools/explorer/ を開く（Safariは「デスクトップ用Webサイトを表示」）
2. アプリ「NEXT EMPEROR Auto」を選び **Generate Access Token** → 許可
3. 右のトークン欄に入ったトークンをコピー → developers.facebook.com/tools/debug/accesstoken/ で「Depurar」→「Estender token de acesso」→ 出てきた長期トークンをコピー
4. エクスプローラに戻り、トークン欄に長期トークンを貼り、上の欄に `1313500291856049?fields=access_token` → Enviar
5. 結果の access_token をデバッガーで確認（Tipo: Page / Expira: Nunca）→ Secretsの `IG_ACCESS_TOKEN` を更新

## 6. 内容の調整

`config/bot_config.json` を編集（Claudeに頼めばOK）
- `brand.facts`: AIが使ってよい事実。**公式サイトに書いてあることだけ**
- `audiences.*.themes`: 投稿テーマ
- `audiences.*.hashtags`: 使ってよいハッシュタグ
- `post.audience_ratio`: 求職者向けと企業向けの比率（既定 2:1）
- `banned_patterns`: 禁止表現
