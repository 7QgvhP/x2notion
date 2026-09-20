"""環境変数の読み込みと設定値の集約。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# プロジェクトルート（src/ の1つ上）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 複数アカウントの認証情報を書くファイル
ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"

# Notionのプロパティ名との対応表
PROPERTIES_FILE = PROJECT_ROOT / "properties.json"

# ブックマーク一覧のURL
BOOKMARKS_URL = "https://x.com/i/bookmarks"

# Notion APIのバージョン。
# 2022-06-28 は database_id を親に指定できる安定版で、
# NotionのURLからコピーしたIDをそのまま使えるため本ツールではこれを採用する。
NOTION_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"


class ConfigError(RuntimeError):
    """設定不備を表す例外。"""


@dataclass
class XAccount:
    """取り込み対象のXアカウント1件分。"""

    label: str
    auth_token: str | None = None
    ct0: str | None = None

    @property
    def use_cookie_auth(self) -> bool:
        """Cookie方式でアクセスできるかどうか。"""
        return bool(self.auth_token and self.ct0)


def _load_accounts_file() -> list[XAccount]:
    """accounts.json からアカウント一覧を読み込む。

    ファイルが無ければ空リストを返す（.env での単一アカウント指定へ委ねる）。
    """
    if not ACCOUNTS_FILE.exists():
        return []

    try:
        raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"accounts.json の書式が不正です（{error}）。JSONとして読み込めません。"
        ) from error

    entries = raw.get("accounts") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ConfigError(
            "accounts.json は {\"accounts\": [...]} の形式で記述してください。"
        )

    accounts: list[XAccount] = []
    seen_labels: set[str] = set()

    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"accounts.json の {index} 件目がオブジェクトではありません。")

        label = str(entry.get("label", "")).strip()
        if not label:
            raise ConfigError(f"accounts.json の {index} 件目に label がありません。")
        if label in seen_labels:
            raise ConfigError(f"accounts.json の label が重複しています: {label}")
        seen_labels.add(label)

        auth_token = str(entry.get("auth_token", "")).strip() or None
        ct0 = str(entry.get("ct0", "")).strip() or None
        if not (auth_token and ct0):
            raise ConfigError(
                f"accounts.json の「{label}」に auth_token / ct0 が揃っていません。"
            )

        accounts.append(XAccount(label=label, auth_token=auth_token, ct0=ct0))

    if not accounts:
        raise ConfigError("accounts.json にアカウントが1件も記載されていません。")

    return accounts


@dataclass
class Config:
    """実行時設定。"""

    notion_token: str
    database_id: str | None
    accounts: list[XAccount]

    @classmethod
    def load(cls, require_database: bool = True) -> "Config":
        """.env を読み込んで設定を組み立てる。

        Args:
            require_database: NOTION_DATABASE_ID が必須かどうか。
                              setup コマンド実行時は不要なので False を渡す。
        """
        load_dotenv(PROJECT_ROOT / ".env")

        token = os.getenv("NOTION_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "NOTION_TOKEN が設定されていません。.env.example をコピーして .env を作成してください。"
            )

        database_id = os.getenv("NOTION_DATABASE_ID", "").strip() or None
        if require_database and not database_id:
            raise ConfigError(
                "NOTION_DATABASE_ID が設定されていません。\n"
                "  データベース未作成の場合: python -m src.main setup --parent-page <ページID>\n"
                "  作成済みの場合: NotionのDB URLからIDをコピーして .env に記入してください。"
            )

        # accounts.json があればそれを優先し、無ければ .env の単一アカウント設定を使う
        accounts = _load_accounts_file()
        if not accounts:
            accounts = [
                XAccount(
                    label=os.getenv("X_ACCOUNT_LABEL", "").strip() or "default",
                    auth_token=os.getenv("X_AUTH_TOKEN", "").strip() or None,
                    ct0=os.getenv("X_CT0", "").strip() or None,
                )
            ]

        return cls(
            notion_token=token,
            database_id=database_id,
            accounts=accounts,
        )


def normalize_notion_id(raw: str) -> str:
    """NotionのIDやURLからハイフンなし32桁のIDを取り出す。

    ユーザーがURLをそのまま貼り付けても動くようにする。
    """
    value = raw.strip()

    # URL形式の場合はパス末尾を取り出す
    if value.startswith("http"):
        value = value.split("?")[0].rstrip("/").split("/")[-1]
        # "ページタイトル-<32桁ID>" 形式に対応
        if "-" in value:
            value = value.split("-")[-1]

    value = value.replace("-", "")

    if len(value) != 32:
        raise ConfigError(f"Notion IDの形式が不正です: {raw!r}（32桁の16進数が必要）")

    return value
