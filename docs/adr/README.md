# 設計判断記録（ADR）

重要な設計判断を、1判断1ファイルで記録する。

## ファイル命名規則

`NNNN-短い題名.md`（`NNNN` は4桁の連番。題名は英小文字とハイフン）

## ステータス

| ステータス | 意味 |
|---|---|
| Proposed | 提案中 |
| Accepted | 採用済み |
| Deprecated | 廃止（後継なし） |
| Superseded | 別の ADR で置き換えられた（置き換えた ADR へのリンクを書く） |

コードや履歴から逆算した判断は、ステータスに「推定（コードから逆算）」と付記する。

## 運用

- 既存の ADR は書き換えない。判断を変えるときは新しい ADR を追加し、古い ADR のステータスを Superseded にする。

## 一覧

| 番号 | 題名 | ステータス | 日付 |
|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | ADR で設計判断を記録する | Accepted | 2026-11-15 |
| [0002](0002-cookie-auth-only.md) | X の認証を Cookie 方式だけにする | Accepted | v1.1.0（日付不明） |
| [0003](0003-read-x-internal-api-via-browser.md) | ブラウザで画面を開き、X の内部 API の応答を取り込む | Accepted（推定：コードから逆算） | v1.0.0（日付不明） |
| [0004](0004-dedupe-by-tweet-id.md) | 重複判定の鍵をツイート ID にする | Accepted | v1.7.2（日付不明） |
| [0005](0005-property-mapping-file.md) | Notion のプロパティ名を対応表で管理する | Accepted | v1.5.0（日付不明） |
| [0006](0006-delete-only-confirmed-bookmarks.md) | X からの削除は、明示したとき、保存を確認できた分だけにする | Accepted | v1.6.0（日付不明） |

v1.8.2 より前は Git 管理の外で開発していたため、判断の日付は記録がない（バージョンは `CHANGELOG.md` の「git管理開始以前の経緯」による）。
