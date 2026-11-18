# 使用方法

コマンドはすべて、リポジトリのフォルダで、仮想環境の Python（`.\.venv\Scripts\python.exe`）を使う。

## 前提条件

- Windows（自動実行とバッチファイルは Windows 専用）。
- Python と、Notion のワークスペース（コネクトの作成にはオーナー権限が必要）。
- 取り込むアカウントで X にログインしたブラウザ（Cookie を取り出すため）。

## インストール

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

すでに `.venv` フォルダがあれば不要。

## 初回の設定

### 1. Notion のコネクトを作る（トークンの取得）
1. Notion の左サイドバー下部から「設定」を開き、「コネクト」タブを選ぶ（旧称は「インテグレーション」）。
2. 「自分のコネクトを開発する」→「+ 新しいコネクト」で、名前（例: `X2Notion`）とワークスペースを指定して作る。
3. コネクト名の横の「•••」から、トークン（`ntn_` または `secret_` で始まる）をコピーする。

直接開く場合の URL: https://www.notion.so/my-integrations

### 2. 親ページにコネクトを招待する
トークンを持っているだけでは、コネクトはページを参照できない。データベースを作りたいページの右上の「•••」→「コネクトを追加」で、作ったコネクトを選ぶ。

### 3. `.env` を作る
```bash
copy .env.example .env
```
`NOTION_TOKEN` を記入する。項目の一覧は [configuration.md](configuration.md#環境変数)。

### 4. X の Cookie を設定する
X は自動操作ブラウザでのログインを制限しているため、普段のブラウザのログイン済みセッションを借りる（[ADR 0002](adr/0002-cookie-auth-only.md)）。

1. 普段のブラウザで X にログインした状態で F12（開発者ツール）を開く。
2. 「アプリケーション」タブ →「Cookie」→ `https://x.com` を開く。
3. `auth_token` と `ct0` の値をコピーし、`.env` の `X_AUTH_TOKEN` と `X_CT0` に貼り付ける。

この2つはログイン情報そのもの。他人に共有しない。X からログアウトすると無効になる。

### 5. データベースを作る
```bash
.\.venv\Scripts\python.exe -m src.main setup --parent-page <親ページのURL>
```
出力された `NOTION_DATABASE_ID=...` の行を `.env` に貼り付ける。

## 基本的な使い方

### 試しに取り込む
```bash
.\.venv\Scripts\python.exe -m src.main run --limit 10 --dry-run
```
Notion には書き込まず、登録対象の一覧だけを表示する。

### 全件を取り込む
```bash
.\.venv\Scripts\python.exe -m src.main run
```

### ダブルクリックで取り込む
エクスプローラーで `取り込み実行.bat` をダブルクリックする。ブラウザは表示されない。取り込みが終わると、X のブックマークから削除するか確認を求める（`y` で削除、Enter でしない）。

## ユースケース別の手順

### 毎日自動で取り込む
`自動実行を設定.bat` をダブルクリックする（既定は毎日 21:00）。取り込んだブックマークは、確認なしで X 側から削除される。

```bash
.\.venv\Scripts\python.exe -m src.main schedule --at 08:30   # 時刻を指定して登録
.\.venv\Scripts\python.exe -m src.main schedule --status     # 登録状況の確認
```

解除は `自動実行を解除.bat`。結果は `logs/<日付>.log` に記録される。

### 取り込んだ後に X のブックマークを空にする
```bash
.\.venv\Scripts\python.exe -m src.main run --dry-run --remove-from-x   # 削除する件数だけを確認
.\.venv\Scripts\python.exe -m src.main run --remove-from-x             # 取り込んで削除（取り消せない）
```

### 複数のアカウントを取り込む
1. `accounts.example.json` を `accounts.json` にコピーし、アカウントごとに `label`、`auth_token`、`ct0` を記入する（[configuration.md](configuration.md#accountsjson)）。各アカウントの Cookie は、そのアカウントでログインしたブラウザから取る（プロフィールを分けるか、シークレットウィンドウを使う）。
2. 既存のデータベースに「アカウント」プロパティがなければ、追加する。

```bash
.\.venv\Scripts\python.exe -m src.main migrate
```

### 新しい分だけを取り込む
```bash
.\.venv\Scripts\python.exe -m src.main run --limit 50
```
各アカウントから新しい50件ずつを取得し、未登録のものだけを追加する（3アカウントなら最大150件）。

### Notion でプロパティの名前を変えた・削除した
```bash
.\.venv\Scripts\python.exe -m src.main properties --detect
```
現在のデータベースの構成から対応表を作り直して、`properties.json` に保存する。推定が誤っている場合は、ファイルを直接編集する。削除したプロパティを戻すときは、`properties.json` に名前を書いてから `migrate` を実行する。

### タイトルの文字数を変えて、既存の行にも反映する
`src/x_bookmarks.py` の `TITLE_MAX_CHARS` を変えてから、次を実行する。
```bash
.\.venv\Scripts\python.exe -m tools.rebuild_titles --dry-run
```

### Notion 側で操作してよいこと・いけないこと

| 操作 | 結果 |
|---|---|
| ビューの追加・フィルタ・並べ替え・グループ化、表示順・列幅の変更 | 問題ない（API に影響しない） |
| プロパティの追加、各ページの値の編集・本文の追記 | 問題ない（未知のプロパティは無視し、次回の実行で上書きしない） |
| データベース名の変更 | 問題ない（ID で参照する） |
| プロパティの名前変更・削除 | `properties --detect` を実行すれば追従する（忘れても、実行前の検証で中断して案内する） |
| プロパティの型の変更 | 壊れる。自動修復できないので、Notion 側で型を戻す |
| URL のプロパティの削除 | 重複判定ができないため、実行を中断する |
| データベースの削除・ゴミ箱への移動、コネクトの接続解除 | `object_not_found` エラー |
| 接続していない場所へのデータベースの移動 | アクセス権を失い、エラーになる場合がある |
| 行の削除 | 次回の実行で再取得される。Notion 上だけで整理したい場合は、削除ではなく、チェックボックスなどのプロパティを追加してフィルタで隠す |

## よくあるエラーと対処

| 症状 | 対処 |
|---|---|
| `設定エラー: NOTION_TOKEN が設定されていません` | `.env` を作り、トークンを記入する |
| `setup` で `Could not find page with ID` | 親ページの「•••」→「コネクトを追加」で、コネクトを招待する |
| 設定に「コネクト」タブがない | ワークスペースのオーナー権限が必要。オーナーに作成を依頼する |
| `Executable doesn't exist` / `BrowserType.launch_persistent_context` | `.\.venv\Scripts\python.exe -m playwright install chromium` を実行する |
| ログイン画面で「続ける」が反応しない | X の自動操作の制限。Cookie 方式（[初回の設定の 4.](#4-x-の-cookie-を設定する)）を使う |
| `Xのログイン状態を確立できませんでした`、`ブックマークが1件も取得できませんでした` | Cookie の期限切れの可能性。`auth_token` と `ct0` を取り直す |
| 途中で取得が止まる | `src/x_bookmarks.py` の `fetch_bookmarks` の `scroll_pause_ms`（既定 1800 ミリ秒）を大きくする |
| 自動実行がうまく動かない | `logs/<日付>.log` を確認する |
