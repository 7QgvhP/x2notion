# x2notion

共通ルール（コミットの書式、バージョン、タグ、CHANGELOG）は、ここには書きません。このファイルは、このリポジトリ固有の事実だけを書きます。

## 概要
X（旧 Twitter）のブックマークを取得し、Notion のデータベースへ一括で登録する CLI ツールです。Playwright で、自分のログイン状態のままブックマーク画面を読み込みます。

## 構成と技術
- Python / Playwright / Notion API
- `src/`: 本体
- `tools/`: 補助ツール（`rebuild_titles.py` など）
- `tests/test_core.py`: 中核ロジックの回帰テスト
- `scripts/scheduled_run.bat`: 自動実行用のバッチファイル
- ルート直下の日本語名の `.bat`（3つ）: ダブルクリックで実行するためのバッチファイル（CRLF）
- `properties.json`: Notion のプロパティ名の対応表
- `.env`、アカウントの設定: 秘密情報（雛形は `.env.example`、`accounts.example.json`）

## ビルド・テストの手順
```bash
pip install -r requirements.txt   # 依存パッケージ（初回）
python -m tests.test_core         # 中核ロジックの回帰テスト
```
- テストは、外部への通信を行わないので、いつでも実行できます。
- ※ 自動対応でこれらを実行するには、dev-hub の `tools\claude-auto\config.ps1` の `$ExtraTools` で許可が必要です。

## バージョンの記録場所
リリースのときに、次をすべて同じコミットで更新します。
- `src/__init__.py` の `__version__`
- `README.md` の冒頭の「**バージョン: vX.Y.Z**」
- `CHANGELOG.md` の見出し（`## [X.Y.Z] - YYYY-MM-DD`）

## 注意事項
- 認証情報（`.env`、X の Cookie を含むアカウントの設定）は、Git 管理外です。コミット、issue のコメント、ログに出さないでください。
- `.bat` は CRLF のまま保ちます（`.gitattributes` で指定済み）。
- X と Notion への実際の通信を伴う動作は、認証情報のある手元の環境でしか確認できません。
- `CHANGELOG.md` の箇条書きは `-` で、文体は「だ・である」です。
