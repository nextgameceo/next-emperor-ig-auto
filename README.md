# next-emperor-ig-auto

NEXT EMPEROR 公式Instagram（@nextemperor.llc）のカルーセル自動投稿システム。

- **月・木 21:00 JST** に、Claudeが文章を作り、画像（1080×1350・6〜7枚）を描画してカルーセル投稿
- 毎日 03:30 に反応（リーチ・保存・シェアなど）を収集、毎週月曜 09:30 に分析してテーマ選びに反映
- 禁止表現・架空の実績・URLなどはルールとAIレビューの二重チェックで止める。不合格なら**投稿しない**

| ファイル | 役割 |
|---|---|
| `ig_bot/` | 生成・描画・投稿・収集・分析の本体 |
| `config/bot_config.json` | ブランドの事実・テーマ・ハッシュタグ・禁止表現・モデル設定 |
| `.github/workflows/ig-bot.yml` | スケジュール実行（投稿・収集・分析） |
| `assets/logo.png` | （任意）置くと最終スライドにロゴが入る。透過PNG推奨 |

画像と投稿記録は `ig-data` ブランチに保存されます（Instagramは公開URLの画像しか受け付けないため、このリポジトリは公開にしています。トークン類はSecretsに暗号化されており公開されません）。

運用方法は [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## テスト

```
pip install -r requirements.txt pytest
python -m pytest -q
```
（日本語フォント fonts-noto-cjk が必要）
