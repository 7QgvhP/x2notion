# 設定リファレンス

秘密情報の実際の値は書かない。雛形は `.env.example` と `accounts.example.json` を参照する。

## 環境変数

`.env` に書く。

`.env` をリポジトリの直下に置く（`python-dotenv` で読み込む）。

| 名前 | 説明 | 型 | デフォルト値 | 必須 |
|---|---|---|---|---|
| `NOTION_TOKEN` | Notion のコネクトのトークン（`ntn_` または `secret_` で始まる） | 文字列 | なし | はい |
| `NOTION_DATABASE_ID` | 取り込み先のデータベースの ID（32桁の16進数。URL でも可） | 文字列 | なし | `setup` 以外では、はい |
| `X_AUTH_TOKEN` | X の Cookie `auth_token` の値（`accounts.json` がないときに使う） | 文字列 | なし | `accounts.json` がなければ、はい |
| `X_CT0` | X の Cookie `ct0` の値（同上） | 文字列 | なし | 同上 |
| `X_ACCOUNT_LABEL` | `.env` のアカウントの名前（Notion の「アカウント」に記録する） | 文字列 | `default` | いいえ |

`X_ACCOUNT_LABEL` は `.env.example` には書かれていない（`src/config.py` が読む）。

## 設定ファイル

### `accounts.json`
複数のアカウントを取り込むときに使う（Git 管理外）。あれば `.env` の `X_AUTH_TOKEN` / `X_CT0` より優先する。記載順に取り込む。

| 名前 | 説明 | 型 | デフォルト値 | 必須 |
|---|---|---|---|---|
| `accounts[].label` | Notion の「アカウント」に記録する名前。重複不可 | 文字列 | なし | はい |
| `accounts[].auth_token` | そのアカウントの Cookie `auth_token` | 文字列 | なし | はい |
| `accounts[].ct0` | そのアカウントの Cookie `ct0` | 文字列 | なし | はい |

### `properties.json`
ツール内部の項目と、Notion のプロパティ名の対応表（Git 管理対象）。`properties.properties.<項目>` にプロパティ名を書く。`null` にするか行を消すと、その項目は書き込まない。`properties --detect` で作り直せる。

| 項目 | 既定のプロパティ名 | 種類 |
|---|---|---|
| `title` | タイトル | タイトル |
| `url` | URL | URL（必須） |
| `body` | 本文 | テキスト |
| `author` | 投稿者 | テキスト |
| `handle` | ユーザーID | テキスト |
| `posted_at` | 投稿日 | 日付 |
| `likes` | いいね | 数値 |
| `reposts` | リポスト | 数値 |
| `media` | メディア | ファイル |
| `tags` | タグ | マルチセレクト |
| `imported_at` | 取込日時 | 日付 |
| `account` | アカウント | マルチセレクト |

「既定のプロパティ名」は `setup` で作るときの名前（`src/properties.py`）。リポジトリの `properties.json` には、実際に使っているデータベースに合わせた対応（一部が `null`）が入っている。

## コマンドラインオプション

`python -m src.main <コマンド> [オプション]`

| コマンド | オプション | 説明 | 型 | デフォルト値 | 必須 |
|---|---|---|---|---|---|
| （共通） | `--version` | バージョンを表示する | — | — | いいえ |
| `setup` | `--parent-page` | データベースを作る親ページの ID または URL | 文字列 | なし | はい |
| `setup` | `--title` | 作るデータベースの名前 | 文字列 | `Xブックマーク` | いいえ |
| `run` | `--limit N` | アカウントごとの取得件数の上限 | 整数 | なし（末尾まで） | いいえ |
| `run` | `--dry-run` | Notion にも X にも書き込まず、対象の一覧だけを表示する | フラグ | オフ | いいえ |
| `run` | `--log` | 実行内容を `logs/<日付>.log` に記録する | フラグ | オフ | いいえ |
| `run` | `--remove-from-x` | 保存を確認できた分を X のブックマークから削除する（取り消せない） | フラグ | オフ | いいえ |
| `migrate` | （なし） | 不足しているプロパティを追加する | — | — | — |
| `schedule` | `--at HH:MM` | 毎日の実行時刻 | 文字列 | `21:00` | いいえ |
| `schedule` | `--remove` | 自動実行を解除する | フラグ | オフ | いいえ |
| `schedule` | `--status` | 登録状況を表示する | フラグ | オフ | いいえ |
| `properties` | `--detect` | 現在のデータベースの構成から対応表を作り直す | フラグ | オフ | いいえ |

`python -m tools.rebuild_titles` は `--dry-run`（変更内容だけを表示）を取る。

## コード内の定数

| 名前 | 場所 | 説明 | 値 |
|---|---|---|---|
| `TITLE_MAX_CHARS` | `src/x_bookmarks.py` | タイトルの最大文字数 | `60` |
| `scroll_pause_ms` | `src/x_bookmarks.py` の `fetch_bookmarks` の引数 | スクロールの間の待ち時間（ミリ秒） | `1800` |
| `NOTION_VERSION` | `src/config.py` | Notion API のバージョン | `2022-06-28` |
