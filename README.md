# X2Notion

X（旧 Twitter）のブックマークを取得し、Notion のデータベースへ一括で登録する CLI ツール。

- バージョン: 1.8.7

## 概要

X のブックマークを、検索・整理しやすい Notion のデータベースに移す。
Playwright で自分のログイン状態（Cookie）のままブックマーク画面を読み込み、X の内部 API の応答から本文・画像・投稿日時などを取り出す。
ツイート ID で重複を判定するため、何度実行しても新しい分だけが追加される。

## 主な機能

- X のブックマークを Notion のデータベースに登録する（データベースの自動作成を含む）
- 複数の X アカウントを、記載順に続けて取り込む
- 取り込み後に、X のブックマークから削除する（明示したときだけ）
- Windows のタスクスケジューラで、毎日自動で取り込む
- Notion 側のプロパティ名の変更・削除に、対応表で追従する

## クイックスタート

前提：Windows、Python、Notion のコネクトのトークン、X の Cookie（`auth_token` と `ct0`）。

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
.\.venv\Scripts\python.exe -m src.main setup --parent-page <親ページのURL>
.\.venv\Scripts\python.exe -m src.main run --limit 10 --dry-run
```

`.env` の記入と Notion 側の準備は [docs/usage.md](docs/usage.md) を参照。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [docs/spec.md](docs/spec.md) | 仕様（何をするか。データベースの構成を含む） |
| [docs/usage.md](docs/usage.md) | 使用方法（セットアップ、取り込み、自動実行、トラブル対処） |
| [docs/architecture.md](docs/architecture.md) | 設計（どう作っているか） |
| [docs/development.md](docs/development.md) | 開発者向けガイド |
| [docs/configuration.md](docs/configuration.md) | 設定リファレンス（`.env`、`accounts.json`、`properties.json`、コマンドラインオプション） |
| [docs/adr/](docs/adr/README.md) | 設計判断記録（ADR） |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |

## ライセンス

ライセンスは定めていない（LICENSE ファイルはない）。
