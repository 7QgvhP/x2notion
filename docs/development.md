# 開発者向けガイド

## 開発環境の構築

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## テスト

```bash
.\.venv\Scripts\python.exe -m tests.test_core
```

中核ロジック（タイトルの要約、内部 API の応答の解析、重複判定、取り込み計画、設定の検証など）の回帰テスト。外部との通信はしないので、いつでも実行できる。

X と Notion への実際の通信を伴う動作は、認証情報のある手元の環境で、`run --dry-run` などで確認する。

## lint / フォーマット

なし。

## ブランチ運用とコミット

全プロジェクト共通の Git 運用ルールに従う。このリポジトリ固有の事情はない。

## リリース手順

1. 更新するファイルは、[CLAUDE.md](../CLAUDE.md) の「バージョンの記録場所」を参照する。
2. GitHub Actions はない。タグを push しても、ビルドや配布は行われない（GitHub Releases は作らない）。
