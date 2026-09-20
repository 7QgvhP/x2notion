"""Notionのプロパティ名との対応付けを扱うモジュール。

Notion側でプロパティ名を変えたり削除したりしても動くよう、
ツール内部のキー（英語・固定）と実際のプロパティ名を対応付ける。
対応付けは properties.json に保存し、未対応（None）の項目は書き込みを省略する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# 内部キー → (既定のプロパティ名, Notionでの型, 説明)
FIELD_DEFINITIONS: dict[str, tuple[str, str, str]] = {
    "title": ("タイトル", "title", "本文の1行目"),
    "url": ("URL", "url", "ツイートURL（重複判定のキー）"),
    "body": ("本文", "rich_text", "全文"),
    "author": ("投稿者", "rich_text", "表示名"),
    "handle": ("ユーザーID", "rich_text", "@ハンドル"),
    "posted_at": ("投稿日", "date", "ツイートの投稿日時"),
    "likes": ("いいね", "number", "いいね数"),
    "reposts": ("リポスト", "number", "リポスト数"),
    "media": ("メディア", "files", "添付画像"),
    "tags": ("タグ", "multi_select", "ハッシュタグ"),
    "imported_at": ("取込日時", "date", "このツールが登録した日時"),
    "account": ("アカウント", "multi_select", "取り込み元のXアカウント"),
}

# これが無いとツールが成立しないキー
REQUIRED_FIELDS = ("title", "url")


class PropertyMapError(RuntimeError):
    """対応付けの不備を表す例外。"""


@dataclass
class PropertyMap:
    """内部キーと実際のNotionプロパティ名の対応表。"""

    # 内部キー → プロパティ名。None は「このDBでは使わない」を意味する
    mapping: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "PropertyMap":
        """既定のプロパティ名による対応表を返す。"""
        return cls({key: name for key, (name, _, _) in FIELD_DEFINITIONS.items()})

    @classmethod
    def load(cls, path: Path) -> "PropertyMap":
        """properties.json を読み込む。無ければ既定の対応表を返す。"""
        if not path.exists():
            return cls.default()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise PropertyMapError(
                f"properties.json の書式が不正です（{error}）。"
            ) from error

        entries = raw.get("properties") if isinstance(raw, dict) else raw
        if not isinstance(entries, dict):
            raise PropertyMapError(
                'properties.json は {"properties": {...}} の形式で記述してください。'
            )

        unknown = set(entries) - set(FIELD_DEFINITIONS)
        if unknown:
            raise PropertyMapError(
                f"properties.json に未知のキーがあります: {'、'.join(sorted(unknown))}\n"
                f"使えるキー: {'、'.join(FIELD_DEFINITIONS)}"
            )

        mapping: dict[str, str | None] = {}
        for key in FIELD_DEFINITIONS:
            if key not in entries:
                # 記載が無いキーは「使わない」扱いにする
                mapping[key] = None
                continue
            value = entries[key]
            if value is None:
                mapping[key] = None
            else:
                name = str(value).strip()
                mapping[key] = name or None

        missing_required = [key for key in REQUIRED_FIELDS if not mapping.get(key)]
        if missing_required:
            raise PropertyMapError(
                f"properties.json に必須キーの対応付けがありません: "
                f"{'、'.join(missing_required)}"
            )

        return cls(mapping)

    def save(self, path: Path) -> None:
        """properties.json として保存する。"""
        body = {
            "_comment": [
                "Notionのプロパティ名との対応表です。",
                "値を null にするか行を削除すると、その項目は書き込みません。",
                "Notion側でプロパティ名を変えたら、ここも合わせて変更してください。",
                "自動で作り直す場合: python -m src.main properties --detect",
            ],
            "properties": {key: self.mapping.get(key) for key in FIELD_DEFINITIONS},
        }
        path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def name(self, key: str) -> str | None:
        """内部キーに対応するプロパティ名を返す。未対応なら None。"""
        return self.mapping.get(key)

    def is_active(self, key: str) -> bool:
        """その項目を書き込む設定になっているか。"""
        return bool(self.mapping.get(key))

    def active_fields(self) -> list[tuple[str, str, str]]:
        """有効な項目を (内部キー, プロパティ名, 型) の形で返す。"""
        result: list[tuple[str, str, str]] = []
        for key, (_, expected_type, _) in FIELD_DEFINITIONS.items():
            name = self.mapping.get(key)
            if name:
                result.append((key, name, expected_type))
        return result

    def inactive_keys(self) -> list[str]:
        """使わない設定になっている項目のキー一覧。"""
        return [key for key in FIELD_DEFINITIONS if not self.mapping.get(key)]


def detect_from_database(
    actual: dict[str, dict],
) -> tuple["PropertyMap", list[str]]:
    """実際のDB構成から対応付けを推定する。

    1. 既定名と型が一致するものを先に確定させる
    2. 残りは「その型で未対応のキーが1つ、空きプロパティも1つ」のときだけ推定する
       （候補が複数ある場合は誤対応を避けるため未対応のままにする）

    Args:
        actual: NotionのAPIが返す properties 辞書。

    Returns:
        推定した対応表と、推定内容の説明リスト。
    """
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    notes: list[str] = []

    # 1. 既定名で一致するもの
    for key, (default_name, expected_type, _) in FIELD_DEFINITIONS.items():
        prop = actual.get(default_name)
        if prop is not None and prop.get("type") == expected_type:
            mapping[key] = default_name
            used.add(default_name)

    # 2. 型が一意に決まる場合のみ推定
    remaining_keys = [key for key in FIELD_DEFINITIONS if key not in mapping]
    for expected_type in {FIELD_DEFINITIONS[k][1] for k in remaining_keys}:
        keys_of_type = [
            key for key in remaining_keys if FIELD_DEFINITIONS[key][1] == expected_type
        ]
        free_props = [
            name
            for name, prop in actual.items()
            if prop.get("type") == expected_type and name not in used
        ]

        if len(keys_of_type) == 1 and len(free_props) == 1:
            key = keys_of_type[0]
            mapping[key] = free_props[0]
            used.add(free_props[0])
            notes.append(
                f"{key}: 型 {expected_type} が一意だったため「{free_props[0]}」に対応付け"
            )

    # 3. 残りは未対応
    for key in FIELD_DEFINITIONS:
        mapping.setdefault(key, None)

    return PropertyMap(mapping), notes
