r"""既存のNotion行のタイトルを、現在の要約規則で作り直す。

ページ本文に全文が残っているため、Xへ再アクセスせずに再生成できる。
タイトル以外のプロパティと本文には一切触れない。

    .\.venv\Scripts\python.exe -m tools.rebuild_titles --dry-run
    .\.venv\Scripts\python.exe -m tools.rebuild_titles
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROPERTIES_FILE, Config  # noqa: E402
from src.notion_writer import NotionError, NotionWriter  # noqa: E402
from src.properties import PropertyMap  # noqa: E402
from src.x_bookmarks import summarize_for_title  # noqa: E402


def fetch_all_rows(writer: NotionWriter, database_id: str) -> list[dict]:
    """データベースの全行を取得する。"""
    rows: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = writer._request("POST", f"/databases/{database_id}/query", body)
        rows.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return rows


def read_body_text(writer: NotionWriter, page_id: str) -> str:
    """ページ本文の段落ブロックを連結して全文を復元する。

    取り込み時に2000文字ごとへ分割しているため、区切り文字を挟まずに繋げる。
    """
    blocks = writer._request("GET", f"/blocks/{page_id}/children?page_size=50")
    return "".join(
        "".join(part["plain_text"] for part in block["paragraph"]["rich_text"])
        for block in blocks.get("results", [])
        if block["type"] == "paragraph"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="既存行のタイトルを現在の要約規則で作り直す"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="書き込まず変更内容だけ表示する"
    )
    args = parser.parse_args()

    # 絵文字を含むタイトルを表示してもコンソールで落ちないようにする
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

    config = Config.load()
    writer = NotionWriter(
        config.notion_token, config.database_id, PropertyMap.load(PROPERTIES_FILE)
    )
    title_name = writer.properties.name("title")
    if not title_name:
        print("タイトルのプロパティが対応付けられていません。", file=sys.stderr)
        return 2

    print("行を取得しています...", flush=True)
    rows = fetch_all_rows(writer, config.database_id)
    print(f"  総行数: {len(rows)} 件")

    changes: list[tuple[str, str, str]] = []   # (page_id, 現在, 変更後)
    skipped_empty = 0

    for position, page in enumerate(rows, start=1):
        current = "".join(
            part["plain_text"] for part in page["properties"][title_name]["title"]
        )
        body = read_body_text(writer, page["id"])
        if not body.strip():
            # 本文が無い行はタイトルの根拠が無いため触らない
            skipped_empty += 1
            continue

        new_title = summarize_for_title(body)
        if new_title != current:
            changes.append((page["id"], current, new_title))

        if position % 20 == 0 or position == len(rows):
            print(f"  確認中... {position}/{len(rows)}", end="\r", flush=True)

    print(f"\n  変更が必要: {len(changes)} 件")
    print(f"  変更不要  : {len(rows) - len(changes) - skipped_empty} 件")
    print(f"  本文が無く対象外: {skipped_empty} 件")

    if not changes:
        print("\n更新の必要はありません。")
        return 0

    print("\n--- 変更内容（先頭10件） ---")
    for _, before, after in changes[:10]:
        print(f"  変更前: {before}")
        print(f"  変更後: {after}\n")

    if args.dry_run:
        print("[dry-run] 書き込みは行いませんでした。")
        return 0

    print(f"{len(changes)} 件を更新します。")
    failures: list[tuple[str, str]] = []
    for position, (page_id, _, new_title) in enumerate(changes, start=1):
        try:
            writer._request(
                "PATCH",
                f"/pages/{page_id}",
                {
                    "properties": {
                        title_name: {
                            "title": [
                                {"type": "text", "text": {"content": new_title}}
                            ]
                        }
                    }
                },
            )
        except NotionError as error:
            failures.append((page_id, str(error)[:120]))
        if position % 20 == 0 or position == len(changes):
            print(f"  {position}/{len(changes)} 件完了", end="\r", flush=True)

    print(f"\n\n完了: 成功 {len(changes) - len(failures)} 件 / 失敗 {len(failures)} 件")
    for page_id, message in failures:
        print(f"  {page_id}: {message}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
