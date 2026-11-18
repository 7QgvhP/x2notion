# 0003. ブラウザで画面を開き、X の内部 API の応答を取り込む

- ステータス: Accepted
- 日付: 2026-09-20 以前（v1.0.0）

## 背景（Context）

X のブックマークを取得する手段が必要だった。

## 決定内容（Decision）

Playwright でヘッドレスの Chromium を起動し、ブックマーク画面（`https://x.com/i/bookmarks`）を開いてスクロールする。そのときに画面が呼ぶ内部 API（GraphQL の `Bookmarks`）の応答を横取りし、本文・画像・投稿日時などを取り出す。削除も、ページ内から内部 API（`DeleteBookmark`）を呼ぶ。

## 理由と検討した代替案（Consequences / Alternatives）

- HTML の解析ではなく、構造化された応答から値を取り出せる。
- 内部 API は非公開の仕様で、構造が変わると取得できなくなる。また、X の利用規約は自動化されたアクセスを制限している。
- 検討した代替案: X の公式 API。
