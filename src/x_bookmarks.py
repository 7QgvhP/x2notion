"""PlaywrightでXのブックマークを取得・削除するモジュール。

Xのブックマーク画面は内部GraphQL API（.../graphql/<hash>/Bookmarks）を
呼び出してデータを取得しているため、そのレスポンスを横取りして解析する。
DOMのスクレイピングよりもUI変更に強く、本文が省略されずに取れる。

認証はCookie方式のみ。Xはログイン画面での自動操作を制限しているため、
ブラウザ上での手動ログインは成立しない。
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Response, sync_playwright

from .config import BOOKMARKS_URL


class BrowserNotInstalledError(RuntimeError):
    """Playwright用のChromiumが利用できない場合の例外。"""


class AuthenticationError(RuntimeError):
    """Xのログイン状態を確立できない場合の例外。"""


class DeletionError(RuntimeError):
    """ブックマーク削除の準備に失敗した場合の例外。"""


# XのWebアプリはJSバンドル内に「操作名 → queryId」の対応表を持つ。
# queryIdはXの更新で変わるため、毎回バンドルから抽出して追従する。
_DELETE_QUERY_PATTERN = re.compile(
    r'\{queryId:"([\w-]+)",operationName:"DeleteBookmark"'
)

# X内部APIのうちブックマーク取得に該当するURLを判定する正規表現。
# 画面のURLが変わっても、この操作名は変わっていない。
_BOOKMARKS_ENDPOINT = re.compile(r"/i/api/graphql/[^/]+/Bookmarks")

# 未ログイン時に飛ばされる先。ここに該当したときだけ認証失敗と判定する。
_LOGIN_URL_MARKERS = ("/login", "/i/flow/", "/account/access")

# Xのcreated_atフォーマット 例: "Wed Oct 10 20:19:24 +0000 2018"
_X_DATE_FORMAT = "%a %b %d %H:%M:%S %z %Y"

# 本文末尾に付く画像・動画へのt.coリンクを除去するための正規表現
_TRAILING_TCO = re.compile(r"\s*https://t\.co/\w+\s*$")

# タイトルに載せる最大文字数。これを超えた分は省略記号に置き換える。
TITLE_MAX_CHARS = 60

# 本文が空のときにタイトルへ入れる文言
_EMPTY_TITLE = "(本文なし)"


@dataclass
class Bookmark:
    """1件のブックマーク（ツイート）を表す。"""

    tweet_id: str
    url: str
    text: str
    author_name: str
    author_handle: str
    created_at: datetime | None
    like_count: int = 0
    repost_count: int = 0
    media_urls: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        """Notionのタイトル用に本文を要約する。"""
        return summarize_for_title(self.text, self.author_handle)


def _is_ascii_alnum(char: str) -> bool:
    """半角の英数字かどうか。"""
    return char.isascii() and char.isalnum()


def _join_lines(text: str) -> str:
    """改行を取り除いて1行に繋げる。

    改行の前後がどちらも半角英数字のときだけ、区切りの空白を残す。
    """
    joined = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if joined and _is_ascii_alnum(joined[-1]) and _is_ascii_alnum(stripped[0]):
            joined += " "
        joined += stripped
    return joined.strip()


def summarize_for_title(text: str, fallback_handle: str = "") -> str:
    """本文をNotionのタイトル用に要約する。

    行単位では切らない。Xのポストは見栄えのために文の途中で改行することが多く、
    1行目だけを採ると意味の通らない断片になるため、
    改行を取り除いて1行に繋げてから長さで切る。

    改行は原則として削除する。日本語では空白に置換すると不自然になるため。
    ただし改行の前後がどちらも半角英数字の場合だけは空白を残す。
    英単語どうしを直結すると読めなくなるため（例: using + Nape → usingNape）。

    Args:
        text: ポストの本文。
        fallback_handle: 本文が空のときに使う@ハンドル。

    Returns:
        TITLE_MAX_CHARS 以内の1行。超える場合のみ末尾に「...」を付ける。
    """
    condensed = _join_lines(text)

    if not condensed:
        return f"@{fallback_handle} のツイート" if fallback_handle else _EMPTY_TITLE

    if len(condensed) <= TITLE_MAX_CHARS:
        return condensed

    return condensed[:TITLE_MAX_CHARS] + "..."


# --------------------------------------------------------------------------
# レスポンス解析
# --------------------------------------------------------------------------


def _iter_tweet_results(payload: dict) -> Iterator[dict]:
    """GraphQLレスポンスからツイートオブジェクトを順に取り出す。"""
    timeline = (
        payload.get("data", {})
        .get("bookmark_timeline_v2", {})
        .get("timeline", {})
    )

    for instruction in timeline.get("instructions", []):
        for entry in instruction.get("entries", []):
            entry_id = entry.get("entryId", "")
            if not entry_id.startswith("tweet-"):
                # カーソル行などはスキップ
                continue

            result = (
                entry.get("content", {})
                .get("itemContent", {})
                .get("tweet_results", {})
                .get("result")
            )
            if not isinstance(result, dict):
                continue

            # 表示制限付きツイートは1階層ラップされている
            if result.get("__typename") == "TweetWithVisibilityResults":
                result = result.get("tweet", {})

            if result:
                yield result


def _extract_user(result: dict) -> tuple[str, str]:
    """ツイートオブジェクトから（表示名, @ハンドル）を取り出す。

    Xのスキーマは時期によって legacy 配下と core 配下のどちらにも
    ユーザー情報が入るため、両方を見る。
    """
    user = (
        result.get("core", {})
        .get("user_results", {})
        .get("result", {})
    )
    legacy = user.get("legacy", {})
    core = user.get("core", {})

    name = legacy.get("name") or core.get("name") or ""
    handle = legacy.get("screen_name") or core.get("screen_name") or ""

    return name, handle


def _extract_text(result: dict, legacy: dict) -> str:
    """本文を取り出し、t.coリンクを展開して読みやすく整形する。"""
    # 長文ポスト（note tweet）は別の場所に全文が入る
    note = (
        result.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
    )
    text = note.get("text") or legacy.get("full_text") or ""

    # t.co の短縮URLを元のURLに戻す
    for url_entity in legacy.get("entities", {}).get("urls", []):
        short = url_entity.get("url")
        expanded = url_entity.get("expanded_url")
        if short and expanded:
            text = text.replace(short, expanded)

    # 末尾に残る画像・動画へのt.coリンクは不要なので削除
    if legacy.get("extended_entities", {}).get("media"):
        text = _TRAILING_TCO.sub("", text)

    return text.strip()


def _extract_media(legacy: dict) -> list[str]:
    """添付メディアのURLを取り出す。動画はサムネイル画像を採用する。"""
    urls: list[str] = []
    media_list = legacy.get("extended_entities", {}).get("media", [])

    for media in media_list:
        media_url = media.get("media_url_https")
        if media_url and media_url not in urls:
            urls.append(media_url)

    return urls


def _parse_created_at(raw: str | None) -> datetime | None:
    """Xの日時文字列をdatetimeに変換する。"""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _X_DATE_FORMAT).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_payload(payload: dict) -> list[Bookmark]:
    """GraphQLレスポンス1件分をBookmarkのリストに変換する。"""
    bookmarks: list[Bookmark] = []

    for result in _iter_tweet_results(payload):
        legacy = result.get("legacy", {})
        tweet_id = result.get("rest_id") or legacy.get("id_str")
        if not tweet_id:
            continue

        name, handle = _extract_user(result)
        hashtags = [
            tag.get("text", "")
            for tag in legacy.get("entities", {}).get("hashtags", [])
            if tag.get("text")
        ]

        bookmarks.append(
            Bookmark(
                tweet_id=str(tweet_id),
                url=f"https://x.com/{handle or 'i'}/status/{tweet_id}",
                text=_extract_text(result, legacy),
                author_name=name,
                author_handle=handle,
                created_at=_parse_created_at(legacy.get("created_at")),
                like_count=int(legacy.get("favorite_count") or 0),
                repost_count=int(legacy.get("retweet_count") or 0),
                media_urls=_extract_media(legacy),
                hashtags=hashtags,
            )
        )

    return bookmarks


# --------------------------------------------------------------------------
# ブラウザ操作
# --------------------------------------------------------------------------


def _session_cookies(auth_token: str, ct0: str) -> list[dict]:
    """ログイン済みセッションを再現するCookieを組み立てる。

    auth_token がログインの実体、ct0 はCSRF対策トークン。
    この2つがあればXはログイン済みとして扱う。
    """
    cookies: list[dict] = []
    for domain in (".x.com", ".twitter.com"):
        cookies.append(
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": domain,
                "path": "/",
                "httpOnly": True,
                "secure": True,
            }
        )
        cookies.append(
            {
                "name": "ct0",
                "value": ct0,
                "domain": domain,
                "path": "/",
                "httpOnly": False,
                "secure": True,
            }
        )
    return cookies


@contextmanager
def _bookmarks_page(auth_token: str, ct0: str) -> Iterator[Page]:
    """Cookieを注入したブラウザでブックマーク画面を開き、ページを返す。

    取得・削除のどちらも「Chromium起動 → Cookie注入 → ブックマーク画面へ移動」
    という同じ準備を必要とするため、ここに集約している。
    呼び出し側はページのイベントハンドラを登録してから本処理に入る。
    """
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            # ブラウザ本体が未取得の場合は、原因が分かる形に置き換える
            if "Executable doesn't exist" in str(error):
                raise BrowserNotInstalledError(str(error)) from error
            raise

        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="ja-JP"
        )
        context.add_cookies(_session_cookies(auth_token, ct0))
        page = context.new_page()

        try:
            yield page
        finally:
            context.close()
            browser.close()


def _goto_bookmarks(page: Page, wait_ms: int = 3000) -> None:
    """ブックマーク画面へ移動する。ログインできていなければ例外を投げる。

    Xはブックマーク画面のURLを予告なく変更する（例: /i/bookmarks → /i/history）。
    そのため到達したURL名では成否を判定せず、
    「ログインフローへ飛ばされたか」だけを失敗の条件にする。
    """
    page.goto(BOOKMARKS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(wait_ms)

    if any(marker in page.url for marker in _LOGIN_URL_MARKERS):
        raise AuthenticationError(
            f"Cookieでのログインに失敗しました（遷移先: {page.url}）。\n"
            "  auth_token / ct0 の期限切れが考えられます。ブラウザから取り直してください。"
        )


def fetch_bookmarks(
    auth_token: str,
    ct0: str,
    limit: int | None = None,
    scroll_pause_ms: int = 1800,
    max_stagnant_rounds: int = 4,
) -> list[Bookmark]:
    """ブックマークを新しい順に取得する。

    Args:
        auth_token: XのCookie auth_token の値。
        ct0: XのCookie ct0 の値。
        limit: 取得件数の上限。Noneなら最後まで取得する。
        scroll_pause_ms: 1回スクロールしてから次のスクロールまでの待機時間。
        max_stagnant_rounds: 新規件数が増えないまま何回スクロールしたら終了するか。

    Returns:
        重複を除いたBookmarkのリスト（画面表示順＝ブックマーク追加が新しい順）。
    """
    collected: dict[str, Bookmark] = {}

    def handle_response(response: Response) -> None:
        """ブックマークAPIのレスポンスを捕捉して解析する。"""
        if not _BOOKMARKS_ENDPOINT.search(response.url):
            return
        try:
            payload = response.json()
        except Exception:
            # 画像などJSON以外が混ざった場合は無視する
            return

        for bookmark in parse_payload(payload):
            collected.setdefault(bookmark.tweet_id, bookmark)

    with _bookmarks_page(auth_token, ct0) as page:
        page.on("response", handle_response)
        _goto_bookmarks(page)

        # 最初のレスポンスが届くまで少し待つ
        page.wait_for_timeout(2500)

        previous_count = len(collected)
        stagnant_rounds = 0

        while True:
            if limit is not None and len(collected) >= limit:
                break

            if stagnant_rounds >= max_stagnant_rounds:
                # これ以上読み込まれない＝末尾に到達したとみなす
                break

            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(scroll_pause_ms)

            current_count = len(collected)
            if current_count == previous_count:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0
                print(f"  取得中... {current_count} 件", end="\r", flush=True)

            previous_count = current_count

        print(f"  取得完了: {len(collected)} 件           ")

    bookmarks = list(collected.values())
    if limit is not None:
        bookmarks = bookmarks[:limit]

    return bookmarks


def _find_delete_query_id(page: Page, script_urls: list[str]) -> str:
    """読み込まれたJSバンドルから DeleteBookmark の queryId を取り出す。"""
    for url in list(dict.fromkeys(script_urls)):
        try:
            text = page.evaluate(
                "async (u) => { const r = await fetch(u); return await r.text(); }",
                url,
            )
        except Exception:
            continue

        match = _DELETE_QUERY_PATTERN.search(text)
        if match:
            return match.group(1)

    raise DeletionError(
        "削除操作の識別子（queryId）を特定できませんでした。"
        "Xの内部構造が変わった可能性があります。"
    )


# ページ内から呼び出すことでCookieとオリジンをそのまま利用する
_DELETE_SCRIPT = """
    async ({ queryId, tweetId, headers }) => {
        const response = await fetch(
            `https://x.com/i/api/graphql/${queryId}/DeleteBookmark`,
            {
                method: "POST",
                headers: headers,
                credentials: "include",
                body: JSON.stringify({
                    variables: { tweet_id: tweetId },
                    queryId: queryId,
                }),
            }
        );
        return { status: response.status, body: await response.text() };
    }
"""


def delete_bookmarks(
    auth_token: str,
    ct0: str,
    tweet_ids: list[str],
    pause_ms: int = 400,
) -> tuple[list[str], list[tuple[str, str]]]:
    """指定したツイートをXのブックマークから削除する。

    ブックマーク画面のリクエストから認証ヘッダを借り、
    Webアプリと同じ削除操作をページ内から呼び出す。

    Args:
        auth_token: XのCookie auth_token。
        ct0: XのCookie ct0。
        tweet_ids: 削除対象のツイートID。
        pause_ms: 1件ごとの待機時間（連続実行を避けるため）。

    Returns:
        (削除に成功したツイートIDの一覧, (ツイートID, エラー内容) の失敗一覧)。
    """
    if not tweet_ids:
        return [], []

    captured_headers: dict[str, str] = {}
    script_urls: list[str] = []

    def on_request(request) -> None:
        """ブックマーク取得リクエストの認証ヘッダを控える。"""
        if _BOOKMARKS_ENDPOINT.search(request.url) and not captured_headers:
            captured_headers.update(request.headers)

    def on_response(response) -> None:
        """読み込まれたJSファイルのURLを控える。"""
        if response.url.endswith(".js") and "abs.twimg.com" in response.url:
            script_urls.append(response.url)

    succeeded: list[str] = []
    failures: list[tuple[str, str]] = []

    with _bookmarks_page(auth_token, ct0) as page:
        page.on("request", on_request)
        page.on("response", on_response)

        # JSバンドルの読み込みを待つため、取得時より長めに待機する
        _goto_bookmarks(page, wait_ms=6000)

        if not captured_headers:
            raise DeletionError(
                "認証ヘッダを取得できませんでした。"
                "ブックマーク画面の読み込みに失敗しています。"
            )

        query_id = _find_delete_query_id(page, script_urls)
        headers = {
            "authorization": captured_headers.get("authorization", ""),
            "x-csrf-token": captured_headers.get("x-csrf-token", ct0),
            "content-type": "application/json",
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
        }

        for position, tweet_id in enumerate(tweet_ids, start=1):
            try:
                result = page.evaluate(
                    _DELETE_SCRIPT,
                    {"queryId": query_id, "tweetId": tweet_id, "headers": headers},
                )
            except Exception as error:
                failures.append((tweet_id, str(error)))
                continue

            body = result.get("body", "")
            if result.get("status") == 200 and "tweet_bookmark_delete" in body:
                succeeded.append(tweet_id)
                print(f"    削除 {position}/{len(tweet_ids)}", end="\r", flush=True)
            else:
                failures.append(
                    (tweet_id, f"HTTP {result.get('status')}: {body[:150]}")
                )

            page.wait_for_timeout(pause_ms)

        print(f"    削除完了: {len(succeeded)}/{len(tweet_ids)} 件      ")

    return succeeded, failures
