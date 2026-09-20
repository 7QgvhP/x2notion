"""X2Notion のCLIエントリポイント。

使い方:
    python -m src.main setup --parent-page <NotionページIDまたはURL>
    python -m src.main run [--limit 100] [--dry-run]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime

from . import APP_NAME, __version__
from .config import (
    PROJECT_ROOT,
    PROPERTIES_FILE,
    Config,
    ConfigError,
    XAccount,
    normalize_notion_id,
)
from .notion_writer import (
    NotionError,
    NotionWriter,
    SchemaReport,
    build_import_plan,
    merge_account_bookmarks,
    normalize_tweet_url,
)
from .properties import (
    FIELD_DEFINITIONS,
    PropertyMap,
    PropertyMapError,
    detect_from_database,
)
from .x_bookmarks import (
    AuthenticationError,
    BrowserNotInstalledError,
    DeletionError,
    delete_bookmarks,
    fetch_bookmarks,
)


def _command_setup(args: argparse.Namespace) -> int:
    """Notionにブックマーク用データベースを新規作成する。"""
    config = Config.load(require_database=False)
    parent_page_id = normalize_notion_id(args.parent_page)

    writer = NotionWriter(
        config.notion_token, property_map=PropertyMap.load(PROPERTIES_FILE)
    )
    # 標準エラーへの案内文と表示順が入れ替わらないよう即座に書き出す
    print(f"データベースを作成しています... （親ページ: {parent_page_id}）", flush=True)

    try:
        database = writer.create_database(parent_page_id, args.title)
    except NotionError as error:
        # ページ未共有は最も多いつまずきなので、原因と対処を明示する
        if "object_not_found" in str(error):
            print(_page_not_shared_hint(args.parent_page), file=sys.stderr)
            return 4
        raise

    database_id = database["id"].replace("-", "")
    print("\n作成しました。")
    print(f"  データベースURL: {database.get('url', '(不明)')}")
    print(f"  データベースID : {database_id}")
    print("\n次の行を .env に追記してください:")
    print(f"NOTION_DATABASE_ID={database_id}")
    return 0


# Windowsタスクスケジューラに登録する際のタスク名
TASK_NAME = "X2Notion_Bookmarks"

# 自動実行時に呼び出されるバッチファイル
SCHEDULED_RUNNER = PROJECT_ROOT / "scripts" / "scheduled_run.bat"


class _Tee:
    """標準出力をコンソールとログファイルの両方へ書き出す。

    pythonw.exe から起動された場合は元のストリームが None になるため、
    その場合でもログだけは残るようにしている。
    """

    def __init__(self, stream, log_file) -> None:
        self._stream = stream
        self._log = log_file

    def write(self, text: str) -> int:
        if self._stream is not None:
            try:
                self._stream.write(text)
            except Exception:
                # コンソールが無い状況でも処理を止めない
                pass
        self._log.write(text)
        self._log.flush()
        return len(text)

    def flush(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass
        self._log.flush()

    # 標準ライブラリや外部ライブラリがファイル互換の属性を参照することがあるため、
    # 元のストリームへ委譲する。コンソールが無い場合は安全な既定値を返す。
    def isatty(self) -> bool:
        return bool(self._stream is not None and self._stream.isatty())

    def fileno(self) -> int:
        if self._stream is None:
            raise OSError("コンソールが接続されていません。")
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")


def _start_logging() -> None:
    """logs/ 配下に日付ごとのログを追記し、標準出力を二重化する。"""
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)

    log_path = logs_dir / f"{datetime.now():%Y-%m-%d}.log"
    log_file = log_path.open("a", encoding="utf-8")
    log_file.write(
        f"\n{'=' * 60}\n{datetime.now():%Y-%m-%d %H:%M:%S} 実行開始\n{'=' * 60}\n"
    )

    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)


def _page_not_shared_hint(target: str) -> str:
    """対象がコネクトに共有されていない場合の案内文を作る。"""
    return (
        "\n"
        "─────────────────────────────────────────────\n"
        "対象のページ／データベースがコネクトに共有されていません。\n"
        "─────────────────────────────────────────────\n"
        "トークン自体は有効です。Notion側での招待操作が未実施です。\n"
        "\n"
        "Notionで対象を開き、次の操作を行ってください:\n"
        "  1. ページ右上の「•••」をクリック\n"
        "  2. メニューを一番下までスクロール\n"
        "  3. 「コネクト」→「コネクトを追加」を選択\n"
        "  4. 作成したコネクト名を検索して選択し、承認する\n"
        "\n"
        f"対象: {target}\n"
        "\n"
        "完了後、同じコマンドを再実行してください。\n"
        "─────────────────────────────────────────────"
    )


def _schema_broken_hint(report: SchemaReport) -> str:
    """データベースの構成が想定と異なる場合の案内文を作る。"""
    lines = [
        "",
        "─────────────────────────────────────────────",
        "Notionデータベースの構成が想定と異なります。",
        "─────────────────────────────────────────────",
        "このまま実行すると重複判定が働かず、二重登録になるため中断しました。",
        "",
        "検出した問題:",
    ]
    lines.extend(f"  - {problem}" for problem in report.problems)

    if report.missing and not report.mismatched:
        # 不足しているだけなら、名前の対応付けか追加で解決できる
        lines.extend(
            [
                "",
                "Notion側でプロパティ名を変更した場合は、対応表を作り直してください:",
                "  .\\.venv\\Scripts\\python.exe -m src.main properties --detect",
                "",
                "プロパティ自体を追加したい場合はこちら",
                "（既存の行やプロパティには影響しません）:",
                "  .\\.venv\\Scripts\\python.exe -m src.main migrate",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "型が変わったプロパティは自動修復できません。",
                "Notion側で型を戻すか、新しいデータベースを作り直してください:",
                "  .\\.venv\\Scripts\\python.exe -m src.main setup --parent-page <ページURL>",
            ]
        )

    lines.append("─────────────────────────────────────────────")
    return "\n".join(lines)


def _command_migrate(args: argparse.Namespace) -> int:
    """既存データベースに不足しているプロパティを追加する。"""
    config = Config.load()
    writer = NotionWriter(
        config.notion_token, config.database_id, PropertyMap.load(PROPERTIES_FILE)
    )

    report = writer.verify_schema()

    if report.mismatched:
        print("型が変わっているプロパティがあり、自動修復できません:", file=sys.stderr)
        for problem in report.mismatched:
            print(f"  - {problem}", file=sys.stderr)
        print("\nNotion側で型を元に戻してから再実行してください。", file=sys.stderr)
        return 7

    if not report.missing:
        print("データベースの構成は最新です。追加は不要でした。")
        return 0

    print("次のプロパティを追加します:")
    for _, name, expected_type in report.missing:
        print(f"  - {name}（型: {expected_type}）")

    # プロパティ追加でDB名が変わらないことを前後で照合する
    title_before = writer.fetch_database_title()
    added = writer.add_missing_properties(report.missing)
    title_after = writer.fetch_database_title()

    print(f"\n完了: {len(added)} 件のプロパティを追加しました。")

    if title_before != title_after:
        print(
            f"\n【警告】データベース名が変わりました: "
            f"{title_before!r} → {title_after!r}\n"
            f"        元に戻します。",
            file=sys.stderr,
        )
        writer.rename_database(title_before)
        print(f"        {title_before!r} に戻しました。", file=sys.stderr)

    return 0


def _command_properties(args: argparse.Namespace) -> int:
    """Notionのプロパティ名との対応表を確認・生成する。"""
    config = Config.load()

    if args.detect:
        # 実際のDB構成から対応付けを推定して保存する
        writer = NotionWriter(config.notion_token, config.database_id)
        actual = writer.fetch_database().get("properties", {})

        detected, notes = detect_from_database(actual)

        print("現在のデータベースのプロパティ:")
        for name, prop in actual.items():
            print(f"  {name}（{prop['type']}）")

        print("\n推定した対応付け:")
        for key, (_, _, description) in FIELD_DEFINITIONS.items():
            name = detected.name(key)
            status = f"→ 「{name}」" if name else "→ 使用しない"
            print(f"  {key:<12} {status:<20} {description}")

        if notes:
            print("\n名前が一致せず、型から推定した項目:")
            for note in notes:
                print(f"  - {note}")

        detected.save(PROPERTIES_FILE)
        print(f"\n{PROPERTIES_FILE.name} に保存しました。")
        print("対応付けが誤っている場合は、このファイルを直接編集してください。")

        inactive = detected.inactive_keys()
        if inactive:
            print(f"\n未使用の項目: {'、'.join(inactive)}")
            print("Notion側に項目を追加したい場合は、properties.json に名前を書いてから:")
            print("  .\\.venv\\Scripts\\python.exe -m src.main migrate")
        return 0

    # 既定動作: 現在の対応表を表示する
    property_map = PropertyMap.load(PROPERTIES_FILE)
    source = PROPERTIES_FILE.name if PROPERTIES_FILE.exists() else "既定値（ファイル未作成）"
    print(f"対応表の読み込み元: {source}\n")

    for key, (_, expected_type, description) in FIELD_DEFINITIONS.items():
        name = property_map.name(key)
        status = f"「{name}」" if name else "（使用しない）"
        print(f"  {key:<12} {status:<18} {expected_type:<13} {description}")

    return 0


def _fetch_all_accounts(
    accounts: list[XAccount], limit: int | None
) -> tuple[list[tuple[str, list]], list[tuple[str, str]]]:
    """全アカウントを順に処理し、取得結果と失敗一覧を返す。

    1つのアカウントで失敗しても、残りのアカウントの処理は継続する。
    """
    results: list[tuple[str, list]] = []
    failures: list[tuple[str, str]] = []

    for position, account in enumerate(accounts, start=1):
        print(
            f"\n  ({position}/{len(accounts)}) アカウント「{account.label}」",
            flush=True,
        )

        if not account.use_cookie_auth:
            message = (
                "Cookieが設定されていません。"
                "accounts.json または .env に auth_token / ct0 を設定してください。"
            )
            failures.append((account.label, message))
            print(f"    {message}", file=sys.stderr)
            continue

        try:
            bookmarks = fetch_bookmarks(
                auth_token=account.auth_token,
                ct0=account.ct0,
                limit=limit,
            )
        except AuthenticationError as error:
            # 1アカウントの認証切れで全体を止めない
            failures.append((account.label, str(error)))
            print(f"    認証に失敗しました: {error}", file=sys.stderr)
            continue

        results.append((account.label, bookmarks))

    return results, failures


def _confirm_deletion(count: int) -> bool:
    """削除前に確認を取る。対話的でない場合（自動実行）は確認を省く。"""
    if not sys.stdin or not sys.stdin.isatty():
        print(f"  自動実行のため確認を省略します（{count} 件）。")
        return True

    print(f"\n  {count} 件をXのブックマークから削除します。この操作は取り消せません。")
    answer = input("  実行しますか？ [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        return True

    print("  削除を中止しました。")
    return False


def _delete_from_x(
    accounts: list[XAccount],
    results: list[tuple[str, list]],
    saved_urls: set[str],
) -> list[tuple[str, str]]:
    """Notionへの保存が確認できたブックマークをX側から削除する。

    Args:
        accounts: 対象アカウント。
        results: (アカウント名, 取得したブックマーク) の一覧。
        saved_urls: Notionに存在することが確認できた正規化URLの集合。

    Returns:
        失敗した項目の一覧。
    """
    by_label = {account.label: account for account in accounts}

    # アカウントごとに、そのアカウントがブックマークしていて
    # かつNotionに保存済みのものだけを削除対象にする
    targets: list[tuple[str, list[str]]] = []
    for label, bookmarks in results:
        tweet_ids = [
            bookmark.tweet_id
            for bookmark in bookmarks
            if normalize_tweet_url(bookmark.url) in saved_urls
        ]
        if tweet_ids:
            targets.append((label, tweet_ids))

    total = sum(len(ids) for _, ids in targets)
    if total == 0:
        print("\n[4/4] X側から削除する対象はありません。")
        return []

    print("\n[4/4] Xのブックマークから削除します。")
    if not _confirm_deletion(total):
        return []

    failures: list[tuple[str, str]] = []
    for label, tweet_ids in targets:
        account = by_label[label]
        print(f"\n  アカウント「{label}」: {len(tweet_ids)} 件")
        try:
            _, account_failures = delete_bookmarks(
                auth_token=account.auth_token,
                ct0=account.ct0,
                tweet_ids=tweet_ids,
            )
        except (AuthenticationError, DeletionError) as error:
            failures.append((label, str(error)))
            print(f"    削除できませんでした: {error}", file=sys.stderr)
            continue

        for tweet_id, message in account_failures:
            failures.append((f"{label}/{tweet_id}", message))

    return failures


def _command_run(args: argparse.Namespace) -> int:
    """Xのブックマークを取得してNotionへ登録する。"""
    config = Config.load()
    accounts = config.accounts

    writer = NotionWriter(
        config.notion_token, config.database_id, PropertyMap.load(PROPERTIES_FILE)
    )

    # Xの取得には時間がかかるため、先にNotion側の疎通と登録済みデータを確認する
    print("[1/4] Notionの登録済みデータを確認します。", flush=True)
    try:
        # プロパティ構成が崩れていると重複判定が働かず二重登録になるため、
        # 書き込みを始める前に検証して中断する
        report = writer.verify_schema()
        if not report.is_valid:
            print(_schema_broken_hint(report), file=sys.stderr)
            return 7
        existing = writer.fetch_existing_index()
    except NotionError as error:
        if "object_not_found" in str(error):
            print(_page_not_shared_hint(config.database_id or ""), file=sys.stderr)
            return 4
        raise
    print(f"  登録済み: {len(existing)} 件")

    if not writer.properties.is_active("account") and len(accounts) > 1:
        # 区別が付かないまま取り込むと後から追跡できないため注意を促す
        print(
            "  【注意】「アカウント」プロパティが未使用のため、"
            "どのアカウント由来かは記録されません。",
            file=sys.stderr,
        )
        print(
            "         記録したい場合: properties.json の account に名前を書いて "
            "migrate を実行してください。",
            file=sys.stderr,
        )

    labels = "、".join(account.label for account in accounts)
    print(f"[2/4] Xのブックマークを取得します。（対象 {len(accounts)} アカウント: {labels}）")

    results, auth_failures = _fetch_all_accounts(accounts, args.limit)

    if not results:
        print("\nどのアカウントからもブックマークを取得できませんでした。", file=sys.stderr)
        return 6 if auth_failures else 1

    merged = merge_account_bookmarks(results)
    plan = build_import_plan(
        merged, existing, track_accounts=writer.properties.is_active("account")
    )

    print(f"\n  取得（重複統合後）: {len(merged)} 件")
    print(f"    新規登録: {len(plan.to_create)} 件")
    print(f"    アカウント欄の追記: {len(plan.to_update)} 件")
    print(f"    変更なし: {plan.unchanged} 件")

    # Notionに存在することが確認できたURL。X側から削除してよい判断材料になる。
    # 既にNotionにある分（追記・変更なし）は保存済みなので最初から含める。
    saved_urls: set[str] = {
        key for key in merged if key in existing
    }

    if args.dry_run:
        print("\n[dry-run] 以下を対象として検出しました（実際には書き込みません）:")
        for bookmark, account_labels in plan.to_create:
            print(f"  [新規] @{bookmark.author_handle}: {bookmark.title}")
            print(f"         アカウント: {'、'.join(sorted(account_labels))}")
        for _, new_labels, bookmark in plan.to_update:
            print(f"  [追記] {bookmark.title}")
            print(f"         追加: {'、'.join(sorted(new_labels))}")

        if args.remove_from_x:
            # dry-runでは登録が未実施なので、削除対象も「登録済みの分」だけを示す
            deletable = len(saved_urls)
            print(
                f"\n  [dry-run] X側の削除対象: {deletable} 件"
                "（実行時は新規登録に成功した分も加わります）"
            )
        return 0

    failures: list[tuple[str, str]] = []
    succeeded = 0

    if plan.to_create or plan.to_update:
        print("\n[3/4] Notionへ反映します。")

        # 古いものから登録すると、Notion側の作成順とブックマーク順が揃う
        total = len(plan.to_create) + len(plan.to_update)
        for index, (bookmark, account_labels) in enumerate(
            reversed(plan.to_create), start=1
        ):
            try:
                writer.create_page(bookmark, account_labels)
                succeeded += 1
                # 保存が確認できたものだけ削除対象に加える
                saved_urls.add(normalize_tweet_url(bookmark.url))
                print(f"  [{index}/{total}] 新規: {bookmark.title}")
            except NotionError as error:
                failures.append((bookmark.url, str(error)))
                print(f"  [{index}/{total}] 失敗: {bookmark.url}", file=sys.stderr)

        for offset, (page, new_labels, bookmark) in enumerate(plan.to_update, start=1):
            index = len(plan.to_create) + offset
            try:
                writer.add_accounts_to_page(page, new_labels)
                succeeded += 1
                print(
                    f"  [{index}/{total}] 追記: {bookmark.title}"
                    f"（+{'、'.join(sorted(new_labels))}）"
                )
            except NotionError as error:
                failures.append((bookmark.url, str(error)))
                print(f"  [{index}/{total}] 失敗: {bookmark.url}", file=sys.stderr)

        print(f"\n完了: 成功 {succeeded} 件 / 失敗 {len(failures)} 件")
    else:
        print("\n[3/4] Notionへ反映する変更はありません。")

    delete_failures: list[tuple[str, str]] = []
    if args.remove_from_x:
        delete_failures = _delete_from_x(accounts, results, saved_urls)

    if auth_failures:
        print("\n認証に失敗したアカウント:", file=sys.stderr)
        for label, message in auth_failures:
            print(f"  {label}: {message}", file=sys.stderr)

    if failures:
        print("\n失敗した項目:", file=sys.stderr)
        for url, message in failures:
            print(f"  {url}\n    {message}", file=sys.stderr)

    if delete_failures:
        print("\nX側の削除に失敗した項目:", file=sys.stderr)
        for target, message in delete_failures:
            print(f"  {target}\n    {message}", file=sys.stderr)

    return 1 if (failures or auth_failures or delete_failures) else 0


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess:
    """schtasks コマンドを実行する。"""
    return subprocess.run(
        ["schtasks", *arguments],
        capture_output=True,
        # schtasks の出力はコンソールのコードページ（日本語環境では cp932）
        encoding="cp932",
        errors="replace",
    )


def _command_schedule(args: argparse.Namespace) -> int:
    """Windowsタスクスケジューラへの自動実行登録を管理する。"""
    if args.remove:
        result = _run_schtasks(["/delete", "/tn", TASK_NAME, "/f"])
        if result.returncode != 0:
            print("自動実行の登録が見つかりませんでした。", file=sys.stderr)
            print((result.stderr or result.stdout).strip(), file=sys.stderr)
            return 8
        print("自動実行の登録を解除しました。")
        return 0

    if args.status:
        result = _run_schtasks(["/query", "/tn", TASK_NAME])
        if result.returncode != 0:
            print("自動実行は登録されていません。")
            return 0
        print(result.stdout.strip())
        return 0

    # 登録前に設定を検証しておく（実行時に無人で失敗するのを防ぐ）
    config = Config.load()
    if not any(account.use_cookie_auth for account in config.accounts):
        print(
            "Cookieが未設定のため自動実行を登録できません。\n"
            "accounts.json または .env に auth_token / ct0 を設定してください。",
            file=sys.stderr,
        )
        return 2

    if not SCHEDULED_RUNNER.exists():
        print(f"実行用ファイルが見つかりません: {SCHEDULED_RUNNER}", file=sys.stderr)
        return 2

    result = _run_schtasks(
        [
            "/create",
            "/tn",
            TASK_NAME,
            "/tr",
            f'"{SCHEDULED_RUNNER}"',
            "/sc",
            "DAILY",
            "/st",
            args.at,
            # 既存の同名タスクがあれば上書きする
            "/f",
        ]
    )

    if result.returncode != 0:
        print("自動実行の登録に失敗しました。", file=sys.stderr)
        print((result.stderr or result.stdout).strip(), file=sys.stderr)
        return 8

    labels = "、".join(account.label for account in config.accounts)
    print(f"自動実行を登録しました。（毎日 {args.at}）")
    print(f"  対象アカウント: {labels}")
    print(f"  実行結果は logs/ 配下に日付ごとに記録されます。")
    print("\n解除する場合:")
    print("  .\\.venv\\Scripts\\python.exe -m src.main schedule --remove")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数のパーサを構築する。"""
    parser = argparse.ArgumentParser(
        prog="x2notion",
        description=f"{APP_NAME} - Xのブックマークを一括でNotionに取り込みます。",
    )
    parser.add_argument(
        "--version", action="version", version=f"{APP_NAME} v{__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser(
        "setup", help="Notionに取り込み用データベースを作成する"
    )
    setup_parser.add_argument(
        "--parent-page",
        required=True,
        help="データベースを配置する親ページのIDまたはURL",
    )
    setup_parser.add_argument(
        "--title", default="Xブックマーク", help="作成するデータベースの名前"
    )
    setup_parser.set_defaults(func=_command_setup)

    run_parser = subparsers.add_parser("run", help="ブックマークを取得してNotionに登録する")
    run_parser.add_argument(
        "--limit", type=int, default=None, help="取得件数の上限（未指定なら全件）"
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Notionに書き込まず、登録対象の一覧だけを表示する",
    )
    run_parser.add_argument(
        "--log",
        action="store_true",
        help="実行内容を logs/ 配下に記録する（自動実行時に使用）",
    )
    run_parser.add_argument(
        "--remove-from-x",
        action="store_true",
        help="Notionへの保存を確認できた分をXのブックマークから削除する（取り消し不可）",
    )
    run_parser.set_defaults(func=_command_run)

    migrate_parser = subparsers.add_parser(
        "migrate", help="既存データベースに不足しているプロパティを追加する"
    )
    migrate_parser.set_defaults(func=_command_migrate)

    schedule_parser = subparsers.add_parser(
        "schedule", help="毎日の自動実行をWindowsタスクスケジューラに登録する"
    )
    schedule_parser.add_argument(
        "--at", default="21:00", metavar="HH:MM", help="実行時刻（既定: 21:00）"
    )
    schedule_parser.add_argument(
        "--remove", action="store_true", help="自動実行の登録を解除する"
    )
    schedule_parser.add_argument(
        "--status", action="store_true", help="現在の登録状況を表示する"
    )
    schedule_parser.set_defaults(func=_command_schedule)

    properties_parser = subparsers.add_parser(
        "properties", help="Notionのプロパティ名との対応表を確認・生成する"
    )
    properties_parser.add_argument(
        "--detect",
        action="store_true",
        help="現在のDB構成から対応付けを推定して properties.json に保存する",
    )
    properties_parser.set_defaults(func=_command_properties)

    return parser


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    # ログ設定は最初のprintより前に行う
    if getattr(args, "log", False):
        _start_logging()

    try:
        return args.func(args)
    except ConfigError as error:
        print(f"設定エラー: {error}", file=sys.stderr)
        return 2
    except PropertyMapError as error:
        print(f"プロパティ対応表のエラー: {error}", file=sys.stderr)
        return 2
    except AuthenticationError as error:
        print(
            "\n"
            "─────────────────────────────────────────────\n"
            "Xのログイン状態を確立できませんでした。\n"
            "─────────────────────────────────────────────\n"
            f"{error}\n"
            "\n"
            "Xは自動操作ブラウザでのログインを制限しているため、\n"
            "普段お使いのブラウザのCookieを .env に設定してください。\n"
            "\n"
            "  1. 普段のブラウザでXにログインした状態で F12 を押す\n"
            "  2. 「アプリケーション」タブ →「Cookie」→ https://x.com\n"
            "  3. auth_token と ct0 の値をコピー\n"
            "  4. .env に次の2行を記入する\n"
            "       X_AUTH_TOKEN=<auth_tokenの値>\n"
            "       X_CT0=<ct0の値>\n"
            "─────────────────────────────────────────────",
            file=sys.stderr,
        )
        return 6
    except BrowserNotInstalledError:
        print(
            "\n"
            "─────────────────────────────────────────────\n"
            "Playwright用のChromiumが見つかりません。\n"
            "─────────────────────────────────────────────\n"
            "次のコマンドを実行してから、再度お試しください:\n"
            "\n"
            "  .\\.venv\\Scripts\\python.exe -m playwright install chromium\n"
            "─────────────────────────────────────────────",
            file=sys.stderr,
        )
        return 5
    except NotionError as error:
        print(f"Notionエラー: {error}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
