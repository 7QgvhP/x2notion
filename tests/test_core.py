"""中核ロジックの回帰テスト。

外部への通信は行わないため、いつでも実行できる。

    .\\.venv\\Scripts\\python.exe -m tests.test_core
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ConfigError, XAccount  # noqa: E402
import src.config as config_module  # noqa: E402
from src.notion_writer import (  # noqa: E402
    ExistingPage,
    NotionWriter,
    _select_option,
    build_import_plan,
    merge_account_bookmarks,
    normalize_tweet_url,
)
from src.properties import (  # noqa: E402
    PropertyMap,
    PropertyMapError,
    detect_from_database,
)
from src.x_bookmarks import (  # noqa: E402
    TITLE_MAX_CHARS,
    Bookmark,
    parse_payload,
    summarize_for_title,
)

passed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """1件の検証結果を記録する。失敗したら即座に終了する。"""
    if condition:
        passed.append(label)
        print(f"[OK] {label}")
    else:
        print(f"[NG] {label}  {detail}")
        sys.exit(1)


def make_bookmark(tweet_id: str, handle: str = "someone") -> Bookmark:
    return Bookmark(
        tweet_id=tweet_id,
        url=f"https://x.com/{handle}/status/{tweet_id}",
        text=f"本文{tweet_id}",
        author_name="投稿者",
        author_handle=handle,
        created_at=None,
    )


# ---------------------------------------------------------------
# 1. GraphQLレスポンスの解析
# ---------------------------------------------------------------
SAMPLE = {
    "data": {
        "bookmark_timeline_v2": {
            "timeline": {
                "instructions": [
                    {
                        "entries": [
                            {
                                "entryId": "tweet-1001",
                                "content": {
                                    "itemContent": {
                                        "tweet_results": {
                                            "result": {
                                                "__typename": "Tweet",
                                                "rest_id": "1001",
                                                "core": {
                                                    "user_results": {
                                                        "result": {
                                                            "legacy": {
                                                                "name": "テスト太郎",
                                                                "screen_name": "test_taro",
                                                            }
                                                        }
                                                    }
                                                },
                                                "legacy": {
                                                    "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                                                    "full_text": "テスト投稿 https://t.co/SHORT1 #Python https://t.co/MEDIA1",
                                                    "favorite_count": 42,
                                                    "retweet_count": 7,
                                                    "entities": {
                                                        "hashtags": [{"text": "Python"}],
                                                        "urls": [
                                                            {
                                                                "url": "https://t.co/SHORT1",
                                                                "expanded_url": "https://example.com/a",
                                                            }
                                                        ],
                                                    },
                                                    "extended_entities": {
                                                        "media": [
                                                            {
                                                                "media_url_https": "https://pbs.twimg.com/media/A.jpg",
                                                                "type": "photo",
                                                            }
                                                        ]
                                                    },
                                                },
                                            }
                                        }
                                    }
                                },
                            },
                            {
                                # 表示制限付き + 新スキーマ(core) + 長文ポスト
                                "entryId": "tweet-1002",
                                "content": {
                                    "itemContent": {
                                        "tweet_results": {
                                            "result": {
                                                "__typename": "TweetWithVisibilityResults",
                                                "tweet": {
                                                    "rest_id": "1002",
                                                    "core": {
                                                        "user_results": {
                                                            "result": {
                                                                "core": {
                                                                    "name": "新スキーマ",
                                                                    "screen_name": "new_schema",
                                                                }
                                                            }
                                                        }
                                                    },
                                                    "note_tweet": {
                                                        "note_tweet_results": {
                                                            "result": {"text": "長文ポストの全文です。"}
                                                        }
                                                    },
                                                    "legacy": {
                                                        "created_at": "Mon Jan 06 09:00:00 +0000 2025",
                                                        "full_text": "長文ポストの全文で…",
                                                        "entities": {},
                                                    },
                                                },
                                            }
                                        }
                                    }
                                },
                            },
                            {"entryId": "cursor-bottom-0", "content": {}},
                        ]
                    }
                ]
            }
        }
    }
}

results = parse_payload(SAMPLE)
check("解析: カーソル行を除いた2件を取得", len(results) == 2, str(len(results)))

first, second = results
check("解析: 旧スキーマ(legacy)から投稿者を取得", first.author_name == "テスト太郎")
check("解析: t.coリンクを展開", "https://example.com/a" in first.text, first.text)
check("解析: 末尾のメディアリンクを除去", "t.co/MEDIA1" not in first.text, first.text)
check("解析: いいね・画像・ハッシュタグ", first.like_count == 42
      and first.media_urls == ["https://pbs.twimg.com/media/A.jpg"]
      and first.hashtags == ["Python"])
check("解析: 投稿日時", first.created_at is not None and first.created_at.year == 2018)
check("解析: 新スキーマ(core)から投稿者を取得", second.author_name == "新スキーマ")
check("解析: 長文ポストの全文を採用", second.text == "長文ポストの全文です。", second.text)

# ---------------------------------------------------------------
# 1.5 タイトルの要約
# ---------------------------------------------------------------
check(
    "タイトル: 60文字以内はそのまま全文",
    summarize_for_title("短い本文です") == "短い本文です",
)
MULTILINE = "1行目です" + chr(10) + "2行目です"
check(
    "タイトル: 日本語の改行は空白にせず削除して繋げる",
    summarize_for_title(MULTILINE) == "1行目です2行目です",
    summarize_for_title(MULTILINE),
)
ENGLISH = "how are people actually using" + chr(10) + "Nape Pro?"
check(
    "タイトル: 英数字どうしの改行は空白を残す",
    summarize_for_title(ENGLISH) == "how are people actually using Nape Pro?",
    summarize_for_title(ENGLISH),
)
MIXED = "日本語です" + chr(10) + "English here"
check(
    "タイトル: 日本語と英字の境目は空白を入れない",
    summarize_for_title(MIXED) == "日本語ですEnglish here",
    summarize_for_title(MIXED),
)
NUMERIC = "税抜1000" + chr(10) + "2000円"
check(
    "タイトル: 数字どうしの改行も空白を残す",
    summarize_for_title(NUMERIC) == "税抜1000 2000円",
    summarize_for_title(NUMERIC),
)
check(
    "タイトル: ちょうど60文字は省略記号を付けない",
    summarize_for_title("あ" * TITLE_MAX_CHARS) == "あ" * TITLE_MAX_CHARS,
)
check(
    "タイトル: 60文字超は末尾に...を付ける",
    summarize_for_title("あ" * 61) == "あ" * TITLE_MAX_CHARS + "...",
)
check(
    "タイトル: 行内の空白はそのまま残す",
    summarize_for_title("語句 と 語句") == "語句 と 語句",
)
check(
    "タイトル: 本文が空なら@ハンドルで代替",
    summarize_for_title("", "someone") == "@someone のツイート",
)
check(
    "タイトル: Bookmarkのtitleが同じ規則を使う",
    Bookmark(
        tweet_id="1", url="u", text="A" * 61, author_name="n",
        author_handle="h", created_at=None,
    ).title == "A" * TITLE_MAX_CHARS + "...",
)


# ---------------------------------------------------------------
# 2. URL正規化と重複判定
# ---------------------------------------------------------------
check(
    "重複判定: x.com / twitter.com の表記ゆれを吸収",
    normalize_tweet_url("https://twitter.com/u/status/9?s=20")
    == normalize_tweet_url("https://x.com/u/status/9"),
)

check(
    "重複判定: @ハンドルが変わっても同一と判定（改名対策）",
    normalize_tweet_url("https://x.com/oldname/status/123")
    == normalize_tweet_url("https://x.com/newname/status/123"),
)
check(
    "重複判定: ハンドル欠落URLも同一と判定",
    normalize_tweet_url("https://x.com/i/status/123")
    == normalize_tweet_url("https://x.com/someone/status/123"),
)
check(
    "重複判定: 別のポストは区別する",
    normalize_tweet_url("https://x.com/u/status/123")
    != normalize_tweet_url("https://x.com/u/status/124"),
)
check(
    "書き込み: multi_selectのカンマを読点に置換",
    _select_option("メイン,サブ")["name"] == "メイン、サブ",
    _select_option("メイン,サブ")["name"],
)

merged = merge_account_bookmarks(
    [
        ("メイン", [make_bookmark("1"), make_bookmark("2")]),
        ("サブ", [make_bookmark("2"), make_bookmark("3")]),
    ]
)
check("統合: 重複ツイートは1件にまとまる", len(merged) == 3, str(len(merged)))
check(
    "統合: 保存元アカウントを両方保持",
    merged[normalize_tweet_url("https://x.com/someone/status/2")][1] == {"メイン", "サブ"},
)

existing = {
    normalize_tweet_url("https://x.com/someone/status/1"): ExistingPage("p1", {"メイン"}),
    # 保存時と@ハンドルが変わっていても同一と判定できること
    normalize_tweet_url("https://x.com/renamed/status/2"): ExistingPage("p2", {"メイン"}),
}
plan = build_import_plan(merged, existing)
check(
    "取り込み計画: 新規1 / 追記1 / 変更なし1",
    len(plan.to_create) == 1 and len(plan.to_update) == 1 and plan.unchanged == 1,
)
check(
    "取り込み計画: アカウント欄が未使用なら追記を作らない",
    build_import_plan(merged, existing, track_accounts=False).to_update == [],
)

# ---------------------------------------------------------------
# 3. プロパティ対応表
# ---------------------------------------------------------------
# 実DBと同じ構成（プロパティが5つに削られ、titleが改名されている）
actual = {
    "取込日時": {"type": "date"},
    "投稿日": {"type": "date"},
    "投稿者": {"type": "rich_text"},
    "URL": {"type": "url"},
    "ポスト": {"type": "title"},
}
detected, _ = detect_from_database(actual)
check("対応表: 改名されたtitleを型から特定", detected.name("title") == "ポスト")
check("対応表: 名前が一致する項目を対応付け", detected.name("url") == "URL"
      and detected.name("author") == "投稿者")
check(
    "対応表: DBに無い項目は未使用のまま",
    all(detected.name(k) is None for k in ("body", "likes", "media", "account")),
)

ambiguous = {
    "見出し": {"type": "title"},
    "URL": {"type": "url"},
    "メモA": {"type": "rich_text"},
    "メモB": {"type": "rich_text"},
}
amb, _ = detect_from_database(ambiguous)
check(
    "対応表: 候補が複数なら推定しない",
    amb.name("body") is None and amb.name("author") is None,
)

writer = NotionWriter("dummy", "0" * 32, detected)
props = writer._build_properties(make_bookmark("5"), {"メイン"})
check(
    "書き込み: 未使用項目は出力しない",
    set(props) == {"ポスト", "URL", "投稿者", "取込日時"},
    str(set(props)),
)
children = writer._build_children(make_bookmark("5"))
check(
    "書き込み: 本文はページ本文に残る",
    any(c["type"] == "paragraph" for c in children)
    and children[0]["type"] == "bookmark",
)

# ---------------------------------------------------------------
# 4. 設定ファイルの検証
# ---------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "properties.json"
    detected.save(path)
    check("設定: properties.json の保存と読み込み",
          PropertyMap.load(path).mapping == detected.mapping)

    path.write_text(json.dumps({"properties": {"title": "ポスト"}}), encoding="utf-8")
    try:
        PropertyMap.load(path)
    except PropertyMapError:
        check("設定: 必須キーの欠落を検出", True)
    else:
        check("設定: 必須キーの欠落を検出", False)

    accounts_path = Path(tmp) / "accounts.json"
    config_module.ACCOUNTS_FILE = accounts_path

    accounts_path.write_text(
        json.dumps({"accounts": [
            {"label": "メイン", "auth_token": "a", "ct0": "c"},
            {"label": "サブ", "auth_token": "b", "ct0": "d"},
        ]}, ensure_ascii=False), encoding="utf-8")
    loaded = config_module._load_accounts_file()
    check("設定: accounts.json の読み込み",
          [a.label for a in loaded] == ["メイン", "サブ"]
          and all(a.use_cookie_auth for a in loaded))

    accounts_path.write_text(
        json.dumps({"accounts": [
            {"label": "同じ", "auth_token": "a", "ct0": "c"},
            {"label": "同じ", "auth_token": "b", "ct0": "d"},
        ]}, ensure_ascii=False), encoding="utf-8")
    try:
        config_module._load_accounts_file()
    except ConfigError:
        check("設定: label重複を検出", True)
    else:
        check("設定: label重複を検出", False)

    accounts_path.write_text(
        json.dumps({"accounts": [{"label": "欠落", "auth_token": "a"}]},
                   ensure_ascii=False), encoding="utf-8")
    try:
        config_module._load_accounts_file()
    except ConfigError:
        check("設定: Cookie欠落を検出", True)
    else:
        check("設定: Cookie欠落を検出", False)

print(f"\nすべて通過しました（{len(passed)} 件）。")
