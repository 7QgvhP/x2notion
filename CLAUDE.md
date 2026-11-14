# x2notion

コミットの書式、バージョン、タグ、CHANGELOG の規約（全プロジェクト共通）は、ここには書かない。このファイルは、このリポジトリ固有の事実だけを書く。

## 概要
X（旧 Twitter）のブックマークを取得し、Notion のデータベースへ一括で登録する CLI ツール。Playwright で、自分のログイン状態（Cookie）のままブックマーク画面を読み込む。

## 技術スタックと主要ディレクトリ
- Python / Playwright（Chromium）/ requests（Notion API）/ python-dotenv。仮想環境は `.venv`。
- `src/`: 本体（`main.py` CLI、`config.py` 設定、`x_bookmarks.py` X の取得・削除、`notion_writer.py` Notion への登録、`properties.py` プロパティの対応表）
- `tools/rebuild_titles.py`: 既存の行のタイトルを作り直す補助ツール
- `tests/test_core.py`: 中核ロジックの回帰テスト
- ルート直下の日本語名の `.bat`（3つ）と `scripts/scheduled_run.bat`: ダブルクリック用・自動実行用（CRLF）
- 詳細は [docs/architecture.md](docs/architecture.md)

## コマンド
```bash
.\.venv\Scripts\python.exe -m tests.test_core     # 中核ロジックの回帰テスト（外部通信なし）
.\.venv\Scripts\python.exe -m src.main --help     # CLI のヘルプ
.\.venv\Scripts\python.exe -m src.main --version
```
- ビルド: なし。lint: なし。
- `run`、`setup`、`migrate`、`properties --detect` は X と Notion に実際に接続する。認証情報のある手元の環境でしか確認できない。
- ※ 自動対応でコマンドを実行するには、dev-hub の `tools\claude-auto\config.ps1` の `$ExtraTools` で許可が必要。

## コーディング規約
- コメント・docstring・メッセージは日本語。
- 型ヒントを付け、`from __future__ import annotations` を使う。
- `CHANGELOG.md` の箇条書きは `-` で、文体は「だ・である」。

## バージョンの記録場所
リリースのときに、次をすべて同じコミットで更新する。
- `src/__init__.py` の `__version__`
- `README.md` の冒頭の「バージョン」の行
- `CHANGELOG.md` の見出し（`## [X.Y.Z] - YYYY-MM-DD`）

GitHub Releases は作らない（配布物がないため、タグだけでリリースとする）。

## ドキュメント更新ルール
今後の開発で、次の変更を行う際は、同じ作業の中で該当ドキュメントも更新すること。
- 機能の追加・変更・削除 → `docs/spec.md`、`docs/usage.md`、`CHANGELOG.md`
- 構成や設計の変更 → `docs/architecture.md`
- 設定項目の追加・変更 → `docs/configuration.md`
- 技術選定や設計方針を変える判断 → 新しい ADR を `docs/adr/` に追加する（既存の ADR は書き換えず、ステータスを Superseded にして、新しい ADR から参照する）
- バージョンを上げるとき → `CHANGELOG.md` に新しいバージョンの節（`## [X.Y.Z] - YYYY-MM-DD`）を追加し、「バージョンの記録場所」のファイルをすべて同じコミットで更新する

## 注意事項
- `.env`、`accounts.json`（X の Cookie と Notion のトークン）、`logs/` は Git 管理外。値をコミット、issue のコメント、ログに出さない。
- `.bat` は CRLF のまま保つ（`.gitattributes` で指定済み）。
- X の内部 API の応答の構造が変わると取得できなくなる。解析は `src/x_bookmarks.py`。
