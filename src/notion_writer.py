"""Notion APIへブックマークを登録するモジュール。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from .config import NOTION_API_BASE, NOTION_VERSION
from .properties import PropertyMap
from .x_bookmarks import Bookmark

# Notion APIのレート制限は平均3リクエスト/秒。余裕をみて間隔を空ける。
_REQUEST_INTERVAL_SEC = 0.35

# rich_text 1要素あたりの文字数上限
_RICH_TEXT_LIMIT = 2000

# ツイートURLからIDを取り出す正規表現（重複判定の鍵）
_TWEET_ID_PATTERN = re.compile(r"/status/(\d+)")

# Notionの型ごとの新規作成用スキーマ
_SCHEMA_BY_TYPE: dict[str, Any] = {
    "title": {"title": {}},
    "url": {"url": {}},
    "rich_text": {"rich_text": {}},
    "date": {"date": {}},
    "number": {"number": {"format": "number"}},
    "files": {"files": {}},
    "multi_select": {"multi_select": {"options": []}},
}


class NotionError(RuntimeError):
    """Notion APIのエラーを表す例外。"""


@dataclass
class SchemaReport:
    """データベースのプロパティ構成の検証結果。

    対応表で「使う」設定になっている項目だけを検証対象とする。
    """

    # 対応付けたのにDBに存在しないプロパティ: (内部キー, プロパティ名, 型)
    missing: list[tuple[str, str, str]]
    # 型が異なるプロパティの説明文。自動では直せない
    mismatched: list[str]

    @property
    def is_valid(self) -> bool:
        """構成に問題が無いかどうか。"""
        return not self.missing and not self.mismatched

    @property
    def problems(self) -> list[str]:
        """問題点を説明文のリストとして返す。"""
        messages = [
            f"プロパティ「{name}」（型: {expected_type}）が見つかりません"
            for _, name, expected_type in self.missing
        ]
        messages.extend(self.mismatched)
        return messages


@dataclass
class ExistingPage:
    """Notionに登録済みの1行を表す。"""

    page_id: str
    # この行に記録済みのアカウント名
    accounts: set[str]


def _select_option(name: str) -> dict[str, str]:
    """multi_select のオプション名を組み立てる。

    Notionはオプション名にカンマを含められないため、読点に置き換える。
    長さの上限（100文字）も併せて丸める。
    """
    return {"name": name.replace(",", "、")[:100]}


def _chunk(text: str, size: int = _RICH_TEXT_LIMIT) -> list[str]:
    """文字列をNotionの上限に収まるサイズへ分割する。"""
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def _rich_text(text: str) -> list[dict[str, Any]]:
    """プレーンテキストをrich_text配列に変換する。"""
    return [{"type": "text", "text": {"content": part}} for part in _chunk(text)]


class NotionWriter:
    """Notionデータベースへの書き込みを担当する。"""

    def __init__(
        self,
        token: str,
        database_id: str | None = None,
        property_map: PropertyMap | None = None,
    ) -> None:
        self.database_id = database_id
        self.properties = property_map or PropertyMap.default()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # 低レベルHTTP
    # ------------------------------------------------------------------

    def _request(
        self, method: str, path: str, json_body: dict | None = None, retries: int = 3
    ) -> dict:
        """Notion APIを呼び出す。429の場合はRetry-Afterに従って再試行する。"""
        url = f"{NOTION_API_BASE}{path}"

        for attempt in range(retries + 1):
            response = self._session.request(method, url, json=json_body, timeout=30)

            if response.status_code == 429 and attempt < retries:
                wait = float(response.headers.get("Retry-After", 1))
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                raise NotionError(
                    f"Notion API エラー [{response.status_code}] {method} {path}\n"
                    f"{response.text}"
                )

            time.sleep(_REQUEST_INTERVAL_SEC)
            return response.json()

        raise NotionError("Notion APIへのリクエストが規定回数を超えて失敗しました。")

    # ------------------------------------------------------------------
    # データベース作成
    # ------------------------------------------------------------------

    def create_database(self, parent_page_id: str, title: str) -> dict:
        """ブックマーク取り込み用のデータベースを新規作成する。

        Args:
            parent_page_id: データベースを配置する親ページのID。
            title: データベースのタイトル。

        Returns:
            作成されたデータベースのAPIレスポンス。
        """
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": {
                name: _SCHEMA_BY_TYPE[expected_type]
                for _, name, expected_type in self.properties.active_fields()
            },
        }
        return self._request("POST", "/databases", body)

    def add_missing_properties(
        self, missing: Iterable[tuple[str, str, str]]
    ) -> list[str]:
        """既存データベースに不足しているプロパティを追加する。

        既存の行やプロパティには影響しない追加操作のみを行う。

        Args:
            missing: (内部キー, プロパティ名, 型) の並び。

        Returns:
            実際に追加したプロパティ名のリスト。
        """
        if not self.database_id:
            raise NotionError("database_id が設定されていません。")

        additions: dict[str, Any] = {
            name: _SCHEMA_BY_TYPE[expected_type]
            for _, name, expected_type in missing
            if expected_type in _SCHEMA_BY_TYPE
        }

        if not additions:
            return []

        self._request(
            "PATCH", f"/databases/{self.database_id}", {"properties": additions}
        )
        return list(additions)

    # ------------------------------------------------------------------
    # 既存データの取得（重複判定用）
    # ------------------------------------------------------------------

    def fetch_database(self) -> dict:
        """データベースの定義を取得する。"""
        if not self.database_id:
            raise NotionError("database_id が設定されていません。")
        return self._request("GET", f"/databases/{self.database_id}")

    def fetch_database_title(self) -> str:
        """データベース名を取得する。"""
        return "".join(
            part.get("plain_text", "") for part in self.fetch_database().get("title", [])
        )

    def rename_database(self, title: str) -> None:
        """データベース名を変更する。"""
        if not self.database_id:
            raise NotionError("database_id が設定されていません。")
        self._request(
            "PATCH",
            f"/databases/{self.database_id}",
            {"title": [{"type": "text", "text": {"content": title}}]},
        )

    def verify_schema(self) -> "SchemaReport":
        """データベースのプロパティ構成を検証する。

        検証対象は対応表で「使う」設定になっている項目のみ。
        特にURLは重複判定の要で、名前や型が食い違うとエラーにならないまま
        全件が新規扱いになり二重登録を招くため、登録前に必ず確認する。

        Returns:
            不足プロパティと型不一致を分けて保持したレポート。
        """
        actual = self.fetch_database().get("properties", {})

        missing: list[tuple[str, str, str]] = []
        mismatched: list[str] = []

        for key, name, expected_type in self.properties.active_fields():
            prop = actual.get(name)
            if prop is None:
                missing.append((key, name, expected_type))
            elif prop.get("type") != expected_type:
                mismatched.append(
                    f"プロパティ「{name}」の型が {prop.get('type')} です"
                    f"（本来は {expected_type}）"
                )

        return SchemaReport(missing=missing, mismatched=mismatched)

    def fetch_existing_index(self) -> dict[str, ExistingPage]:
        """登録済みの行を「正規化URL → 行情報」の索引として取得する。

        1件ずつ問い合わせるとリクエスト数が膨らむため、
        起動時にまとめて取得してメモリ上で重複判定する。
        アカウント名も保持し、既存行への追記が必要か判断できるようにする。
        """
        if not self.database_id:
            raise NotionError("database_id が設定されていません。")

        index: dict[str, ExistingPage] = {}
        cursor: str | None = None

        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor

            data = self._request(
                "POST", f"/databases/{self.database_id}/query", body
            )

            url_name = self.properties.name("url")
            account_name = self.properties.name("account")

            for page in data.get("results", []):
                properties = page.get("properties", {})
                url = properties.get(url_name, {}).get("url")
                if not url:
                    continue

                # アカウント欄を使わない設定の場合は空集合のままにする
                accounts: set[str] = set()
                if account_name:
                    accounts = {
                        option.get("name", "")
                        for option in properties.get(account_name, {}).get(
                            "multi_select", []
                        )
                        if option.get("name")
                    }

                index[normalize_tweet_url(url)] = ExistingPage(
                    page_id=page["id"], accounts=accounts
                )

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        return index

    def add_accounts_to_page(self, page: ExistingPage, labels: Iterable[str]) -> None:
        """既存行の「アカウント」欄にアカウント名を追記する。

        別のアカウントが同じツイートをブックマークしていた場合に使う。
        既存の値は残したまま和集合で更新する。
        """
        account_name = self.properties.name("account")
        if not account_name:
            # アカウント欄を使わない設定なら何もしない
            return

        merged = sorted(page.accounts | set(labels))
        self._request(
            "PATCH",
            f"/pages/{page.page_id}",
            {
                "properties": {
                    account_name: {
                        "multi_select": [_select_option(name) for name in merged]
                    }
                }
            },
        )
        page.accounts = set(merged)

    # ------------------------------------------------------------------
    # ページ作成
    # ------------------------------------------------------------------

    def _build_properties(
        self, bookmark: Bookmark, account_labels: Iterable[str] = ()
    ) -> dict[str, Any]:
        """Bookmarkをページのプロパティ辞書に変換する。

        対応表で「使わない」設定の項目は書き込まない。
        Notion側で削除されたプロパティがあっても、そのまま動作する。
        """
        labels = sorted(set(account_labels))

        # 内部キー → 書き込む値。値が None の項目は出力しない
        values: dict[str, Any] = {
            "title": {"title": _rich_text(bookmark.title)},
            "url": {"url": bookmark.url},
            "body": {"rich_text": _rich_text(bookmark.text)},
            "author": {"rich_text": _rich_text(bookmark.author_name)},
            "handle": {"rich_text": _rich_text(f"@{bookmark.author_handle}")},
            "likes": {"number": bookmark.like_count},
            "reposts": {"number": bookmark.repost_count},
            "imported_at": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "posted_at": (
                {"date": {"start": bookmark.created_at.isoformat()}}
                if bookmark.created_at
                else None
            ),
            "media": (
                {
                    "files": [
                        {
                            "type": "external",
                            "name": f"media_{index + 1}",
                            "external": {"url": url},
                        }
                        # filesプロパティは最大100件だが実用上は先頭4件で十分
                        for index, url in enumerate(bookmark.media_urls[:4])
                    ]
                }
                if bookmark.media_urls
                else None
            ),
            "tags": (
                {
                    "multi_select": [
                        _select_option(tag) for tag in bookmark.hashtags[:10]
                    ]
                }
                if bookmark.hashtags
                else None
            ),
            "account": (
                {"multi_select": [_select_option(label) for label in labels]}
                if labels
                else None
            ),
        }

        properties: dict[str, Any] = {}
        for key, value in values.items():
            name = self.properties.name(key)
            if name and value is not None:
                properties[name] = value

        return properties

    def _build_children(self, bookmark: Bookmark) -> list[dict[str, Any]]:
        """ページ本文のブロックを組み立てる。"""
        children: list[dict[str, Any]] = [
            {
                "object": "block",
                "type": "bookmark",
                "bookmark": {"url": bookmark.url},
            }
        ]

        for part in _chunk(bookmark.text):
            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": part}}]
                    },
                }
            )

        for url in bookmark.media_urls[:4]:
            children.append(
                {
                    "object": "block",
                    "type": "image",
                    "image": {"type": "external", "external": {"url": url}},
                }
            )

        return children

    def create_page(
        self, bookmark: Bookmark, account_labels: Iterable[str] = ()
    ) -> dict:
        """1件のブックマークをページとして登録する。"""
        if not self.database_id:
            raise NotionError("database_id が設定されていません。")

        body = {
            "parent": {"database_id": self.database_id},
            "properties": self._build_properties(bookmark, account_labels),
            "children": self._build_children(bookmark),
        }
        return self._request("POST", "/pages", body)


def normalize_tweet_url(url: str) -> str:
    """重複判定用にツイートURLを正規化する。

    鍵にするのはツイートIDのみ。URLに含まれる@ハンドルは
    ユーザーがいつでも変更できるため、URL文字列で比較すると
    改名後に同じポストが二重登録されてしまう。

    status/<数字> の形式でない場合のみ、従来どおり
    twitter.com / x.com の表記ゆれとクエリ文字列を吸収して比較する。
    """
    match = _TWEET_ID_PATTERN.search(url)
    if match:
        return f"tweet:{match.group(1)}"

    normalized = url.split("?")[0].rstrip("/")
    normalized = normalized.replace("//twitter.com/", "//x.com/")
    normalized = normalized.replace("//www.x.com/", "//x.com/")
    return normalized.lower()


def merge_account_bookmarks(
    per_account: Iterable[tuple[str, Iterable[Bookmark]]],
) -> dict[str, tuple[Bookmark, set[str]]]:
    """アカウントごとの取得結果を、URL単位に統合する。

    同じツイートを複数アカウントがブックマークしていた場合は1件にまとめ、
    そのツイートを保存していたアカウント名を集合として保持する。

    Args:
        per_account: (アカウント名, そのアカウントのブックマーク) の並び。

    Returns:
        正規化URL → (Bookmark, アカウント名の集合) の辞書。
    """
    merged: dict[str, tuple[Bookmark, set[str]]] = {}

    for label, bookmarks in per_account:
        for bookmark in bookmarks:
            key = normalize_tweet_url(bookmark.url)
            if key in merged:
                merged[key][1].add(label)
            else:
                merged[key] = (bookmark, {label})

    return merged


@dataclass
class ImportPlan:
    """取り込み内容の内訳。"""

    # 新規に作成する行: (ブックマーク, アカウント名の集合)
    to_create: list[tuple[Bookmark, set[str]]]
    # アカウント欄の追記が必要な既存行: (既存行, 追加するアカウント名, ブックマーク)
    to_update: list[tuple[ExistingPage, set[str], Bookmark]]
    # 変更不要だった件数
    unchanged: int


def build_import_plan(
    merged: dict[str, tuple[Bookmark, set[str]]],
    existing: dict[str, ExistingPage],
    track_accounts: bool = True,
) -> ImportPlan:
    """統合済みブックマークと既存データを突き合わせ、実行内容を決める。

    Args:
        merged: 正規化URL → (Bookmark, アカウント名の集合)。
        existing: 正規化URL → 登録済みの行。
        track_accounts: アカウント欄を使う設定かどうか。
                        Falseの場合、既存行への追記は行わない
                        （書き込み先が無く、毎回同じ差分を検出し続けるため）。
    """
    to_create: list[tuple[Bookmark, set[str]]] = []
    to_update: list[tuple[ExistingPage, set[str], Bookmark]] = []
    unchanged = 0

    for key, (bookmark, labels) in merged.items():
        page = existing.get(key)
        if page is None:
            to_create.append((bookmark, labels))
            continue

        if not track_accounts:
            unchanged += 1
            continue

        # 既に登録済みでも、別アカウントの記録が抜けていれば追記する
        new_labels = labels - page.accounts
        if new_labels:
            to_update.append((page, new_labels, bookmark))
        else:
            unchanged += 1

    return ImportPlan(to_create=to_create, to_update=to_update, unchanged=unchanged)
