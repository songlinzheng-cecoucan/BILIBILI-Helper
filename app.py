from __future__ import annotations

import json
import os
import secrets
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from flask import Flask, redirect, render_template, request, session, url_for

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "data.db"

CATEGORY_SUGGESTIONS = {
    "科技": ["AI", "编程", "硬件", "数码", "开源"],
    "游戏": ["单机", "主机", "手游", "攻略", "测评"],
    "生活": ["美食", "旅行", "vlog", "家居", "健康"],
    "知识": ["科普", "历史", "财经", "教育", "语言"],
}


@dataclass
class Keyword:
    id: int
    term: str
    category: str
    enabled: bool


@dataclass
class UpCreator:
    id: int
    name: str
    mid: str
    tag: str
    enabled: bool


@dataclass
class ListEntry:
    id: int
    name: str
    mid: str
    list_type: str
    enabled: bool


@dataclass
class Settings:
    send_interval_hours: int
    aggregates_enabled: bool
    highlight_special: bool
    highlight_paid: bool
    email_recipients: str
    wechat_webhook: str


app = Flask(__name__)
app.secret_key = os.environ.get("BILIBILI_HELPER_SECRET") or secrets.token_hex(32)

BILI_SESSION_STORE: dict[str, dict[str, str]] = {}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                category TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS up_creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mid TEXT,
                tag TEXT NOT NULL DEFAULT 'special',
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS list_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mid TEXT,
                list_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                send_interval_hours INTEGER NOT NULL DEFAULT 2,
                aggregates_enabled INTEGER NOT NULL DEFAULT 1,
                highlight_special INTEGER NOT NULL DEFAULT 1,
                highlight_paid INTEGER NOT NULL DEFAULT 1,
                email_recipients TEXT NOT NULL DEFAULT '',
                wechat_webhook TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO settings (id)
            VALUES (1)
            """
        )


init_db()


def fetch_keywords(conn: sqlite3.Connection) -> list[Keyword]:
    rows = conn.execute("SELECT * FROM keywords ORDER BY category, term").fetchall()
    return [Keyword(**row) for row in rows]


def fetch_up_creators(conn: sqlite3.Connection) -> list[UpCreator]:
    rows = conn.execute("SELECT * FROM up_creators ORDER BY tag, name").fetchall()
    return [UpCreator(**row) for row in rows]


def fetch_list_entries(conn: sqlite3.Connection) -> list[ListEntry]:
    rows = conn.execute(
        "SELECT * FROM list_entries ORDER BY list_type, name"
    ).fetchall()
    return [ListEntry(**row) for row in rows]


def fetch_settings(conn: sqlite3.Connection) -> Settings:
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return Settings(
        send_interval_hours=row["send_interval_hours"],
        aggregates_enabled=bool(row["aggregates_enabled"]),
        highlight_special=bool(row["highlight_special"]),
        highlight_paid=bool(row["highlight_paid"]),
        email_recipients=row["email_recipients"],
        wechat_webhook=row["wechat_webhook"],
    )


def parse_bool(value: Optional[str]) -> bool:
    return value in {"1", "true", "on", "yes"}


def grouped_keywords(keywords: Iterable[Keyword]) -> dict[str, list[Keyword]]:
    grouped: dict[str, list[Keyword]] = {}
    for keyword in keywords:
        grouped.setdefault(keyword.category, []).append(keyword)
    return grouped


def build_feed_preview(
    keywords: list[Keyword],
    creators: list[UpCreator],
    settings: Settings,
) -> list[dict[str, str]]:
    preview = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for keyword in keywords[:8]:
        preview.append(
            {
                "title": f"[{keyword.category}] 与「{keyword.term}」相关的新视频",
                "author": "UP主示例",
                "tag": "",
                "time": now,
            }
        )
    if settings.highlight_special:
        for creator in creators:
            if creator.tag == "special" and creator.enabled:
                preview.append(
                    {
                        "title": "特别关注 UP 主更新",
                        "author": creator.name,
                        "tag": "特别关注",
                        "time": now,
                    }
                )
    if settings.highlight_paid:
        for creator in creators:
            if creator.tag == "paid" and creator.enabled:
                preview.append(
                    {
                        "title": "付费关注 UP 主更新",
                        "author": creator.name,
                        "tag": "付费",
                        "time": now,
                    }
                )
    return preview[:10]


def create_bili_session(
    display_name: str,
    mid: str,
    sessdata: str,
    face: str = "",
) -> str:
    session_id = secrets.token_urlsafe(32)
    BILI_SESSION_STORE[session_id] = {
        "display_name": display_name,
        "mid": mid,
        "sessdata": sessdata,
        "face": face,
    }
    return session_id


def get_bili_session() -> Optional[dict[str, str]]:
    session_id = session.get("bili_session_id")
    if not session_id:
        return None
    return BILI_SESSION_STORE.get(session_id)


def clear_bili_session() -> None:
    session_id = session.pop("bili_session_id", None)
    if session_id:
        BILI_SESSION_STORE.pop(session_id, None)


def fetch_bili_json(url: str, sessdata: Optional[str] = None) -> dict:
    headers = {
        "User-Agent": "BILIBILI-Helper/1.0",
        "Referer": "https://www.bilibili.com/",
    }
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    request_obj = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request_obj, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != 0:
        message = payload.get("message") or "请求失败"
        raise RuntimeError(message)
    return payload.get("data") or {}


def fetch_user_profile(sessdata: str) -> dict[str, str]:
    data = fetch_bili_json("https://api.bilibili.com/x/web-interface/nav", sessdata)
    return {
        "display_name": str(data.get("uname", "")).strip() or "哔哩哔哩用户",
        "mid": str(data.get("mid", "")).strip(),
        "face": str(data.get("face", "")).strip(),
    }


def fetch_followings_list(
    mid: str,
    sessdata: str,
    max_pages: int = 3,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode(
            {
                "vmid": mid,
                "pn": page,
                "ps": 50,
                "order": "desc",
                "order_type": "attention",
            }
        )
        url = f"https://api.bilibili.com/x/relation/followings?{query}"
        data = fetch_bili_json(url, sessdata)
        followings = data.get("list") or []
        if not followings:
            break
        for item in followings:
            name = str(item.get("uname", "")).strip()
            item_mid = str(item.get("mid", "")).strip()
            results.append(
                {
                    "name": name or "未知UP主",
                    "mid": item_mid or "未知",
                    "special": "1" if item.get("special") == 1 else "0",
                }
            )
    return results


def fetch_followings(
    mid: str,
    sessdata: str,
    keyword: str,
    max_pages: int = 3,
) -> list[dict[str, str]]:
    encoded_keyword = keyword.strip().lower()
    if not encoded_keyword:
        return []
    results: list[dict[str, str]] = []
    for item in fetch_followings_list(mid, sessdata, max_pages=max_pages):
        if encoded_keyword not in item["name"].lower():
            continue
        results.append(item)
    return results


def fetch_creator_updates(
    mid: str,
    cutoff_ts: int,
    sessdata: Optional[str] = None,
    max_pages: int = 3,
) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode(
            {
                "mid": mid,
                "pn": page,
                "ps": 30,
                "order": "pubdate",
            }
        )
        url = f"https://api.bilibili.com/x/space/arc/search?{query}"
        data = fetch_bili_json(url, sessdata)
        vlist = (data.get("list") or {}).get("vlist") or []
        if not vlist:
            break
        for item in vlist:
            created_ts = int(item.get("created") or 0)
            if created_ts < cutoff_ts:
                break
            updates.append(
                {
                    "title": str(item.get("title", "")).strip() or "未命名视频",
                    "created": datetime.fromtimestamp(created_ts).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "created_ts": str(created_ts),
                    "bvid": str(item.get("bvid", "")).strip(),
                    "author": str(item.get("author", "")).strip(),
                    "link": f"https://www.bilibili.com/video/{item.get('bvid')}"
                    if item.get("bvid")
                    else "",
                }
            )
        if int(vlist[-1].get("created") or 0) < cutoff_ts:
            break
    return updates


def fetch_following_updates(
    mid: str,
    sessdata: str,
    interval_hours: int,
    limit: Optional[int] = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    followings = fetch_followings_list(mid, sessdata, max_pages=2)
    updates: list[dict[str, str]] = []
    statuses: list[dict[str, str]] = []
    cutoff_ts = int(datetime.now().timestamp()) - max(interval_hours, 1) * 3600
    for item in followings:
        try:
            creator_updates = fetch_creator_updates(
                item["mid"],
                cutoff_ts,
                sessdata,
                max_pages=2,
            )
        except Exception:
            statuses.append(
                {
                    "creator": item["name"],
                    "creator_mid": item["mid"],
                    "status": "api_failed",
                    "count": 0,
                }
            )
            continue
        if not creator_updates:
            statuses.append(
                {
                    "creator": item["name"],
                    "creator_mid": item["mid"],
                    "status": "no_updates",
                    "count": 0,
                }
            )
        else:
            statuses.append(
                {
                    "creator": item["name"],
                    "creator_mid": item["mid"],
                    "status": "updated",
                    "count": len(creator_updates),
                }
            )
        for update in creator_updates:
            updates.append(
                {
                    **update,
                    "creator": item["name"],
                    "creator_mid": item["mid"],
                    "special": item["special"],
                }
            )
    updates.sort(key=lambda x: int(x.get("created_ts", "0")), reverse=True)
    if limit is None:
        return updates, statuses
    return updates[: max(limit, 0)], statuses


@app.route("/")
def index():
    account = get_bili_session()
    search_keyword = request.args.get("search", "").strip()
    search_results: list[dict[str, str]] = []
    search_error = ""
    followings: list[dict[str, str]] = []
    followings_error = ""
    updates: list[dict[str, str]] = []
    update_statuses: list[dict[str, str]] = []
    updates_error = ""
    login_error = session.pop("login_error", "")

    with get_connection() as conn:
        settings = fetch_settings(conn)

    if account and search_keyword:
        try:
            search_results = fetch_followings(
                account["mid"],
                account["sessdata"],
                search_keyword,
            )
        except Exception:
            search_error = "搜索失败，请检查 SESSDATA 是否有效或稍后重试。"

    if account:
        try:
            followings = fetch_followings_list(
                account["mid"],
                account["sessdata"],
                max_pages=1,
            )
        except Exception:
            followings_error = "关注列表拉取失败，请稍后重试。"
        try:
            updates, update_statuses = fetch_following_updates(
                account["mid"],
                account["sessdata"],
                interval_hours=settings.send_interval_hours,
            )
        except Exception:
            updates_error = "关注更新拉取失败，请稍后重试。"

    with get_connection() as conn:
        keywords = fetch_keywords(conn)
        creators = fetch_up_creators(conn)
        list_entries = fetch_list_entries(conn)

    preview = build_feed_preview(keywords, creators, settings)

    return render_template(
        "index.html",
        keywords=keywords,
        keyword_groups=grouped_keywords(keywords),
        creators=creators,
        list_entries=list_entries,
        settings=settings,
        categories=sorted({"默认"} | {kw.category for kw in keywords}),
        category_suggestions=CATEGORY_SUGGESTIONS,
        preview=preview,
        account=account,
        search_keyword=search_keyword,
        search_results=search_results,
        search_error=search_error,
        followings=followings,
        followings_error=followings_error,
        updates=updates,
        update_statuses=update_statuses,
        updates_error=updates_error,
        login_error=login_error,
    )


@app.route("/keywords/add", methods=["POST"])
def add_keyword():
    term = request.form.get("term", "").strip()
    category = request.form.get("category", "默认").strip() or "默认"
    if term:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO keywords (term, category, enabled) VALUES (?, ?, 1)",
                (term, category),
            )
    return redirect(url_for("index"))


@app.route("/keywords/<int:keyword_id>/toggle", methods=["POST"])
def toggle_keyword(keyword_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE keywords SET enabled = 1 - enabled WHERE id = ?",
            (keyword_id,),
        )
    return redirect(url_for("index"))


@app.route("/keywords/<int:keyword_id>/delete", methods=["POST"])
def delete_keyword(keyword_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
    return redirect(url_for("index"))


@app.route("/settings/update", methods=["POST"])
def update_settings():
    send_interval_hours = int(request.form.get("send_interval_hours", 2))
    aggregates_enabled = parse_bool(request.form.get("aggregates_enabled"))
    highlight_special = parse_bool(request.form.get("highlight_special"))
    highlight_paid = parse_bool(request.form.get("highlight_paid"))
    email_recipients = request.form.get("email_recipients", "").strip()
    wechat_webhook = request.form.get("wechat_webhook", "").strip()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE settings
            SET send_interval_hours = ?,
                aggregates_enabled = ?,
                highlight_special = ?,
                highlight_paid = ?,
                email_recipients = ?,
                wechat_webhook = ?
            WHERE id = 1
            """,
            (
                send_interval_hours,
                int(aggregates_enabled),
                int(highlight_special),
                int(highlight_paid),
                email_recipients,
                wechat_webhook,
            ),
        )
    return redirect(url_for("index"))


@app.route("/account/login", methods=["POST"])
def account_login():
    sessdata = request.form.get("sessdata", "").strip()

    if not sessdata:
        session["login_error"] = "请提供有效的 SESSDATA。"
        return redirect(url_for("index"))

    try:
        profile = fetch_user_profile(sessdata)
    except Exception:
        session["login_error"] = "登录失败，SESSDATA 无效或请求受限。"
        return redirect(url_for("index"))

    clear_bili_session()
    session["bili_session_id"] = create_bili_session(
        profile["display_name"],
        profile["mid"],
        sessdata,
        profile["face"],
    )
    return redirect(url_for("index"))


@app.route("/account/logout", methods=["POST"])
def account_logout():
    clear_bili_session()
    return redirect(url_for("index"))


@app.route("/creators/add", methods=["POST"])
def add_creator():
    name = request.form.get("name", "").strip()
    mid = request.form.get("mid", "").strip()
    tag = request.form.get("tag", "special")
    if name:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO up_creators (name, mid, tag, enabled) VALUES (?, ?, ?, 1)",
                (name, mid, tag),
            )
    return redirect(url_for("index"))


@app.route("/account/creators/add", methods=["POST"])
def add_creator_from_account():
    name = request.form.get("name", "").strip()
    mid = request.form.get("mid", "").strip()
    tag = request.form.get("tag", "special")
    search_keyword = request.form.get("search_keyword", "").strip()
    if name:
        with get_connection() as conn:
            existing = conn.execute(
                """
                SELECT 1 FROM up_creators
                WHERE name = ? AND mid = ? AND tag = ?
                """,
                (name, mid, tag),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO up_creators (name, mid, tag, enabled) VALUES (?, ?, ?, 1)",
                    (name, mid, tag),
                )
    return redirect(url_for("index", search=search_keyword))


@app.route("/creators/<int:creator_id>/toggle", methods=["POST"])
def toggle_creator(creator_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE up_creators SET enabled = 1 - enabled WHERE id = ?",
            (creator_id,),
        )
    return redirect(url_for("index"))


@app.route("/creators/<int:creator_id>/delete", methods=["POST"])
def delete_creator(creator_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM up_creators WHERE id = ?", (creator_id,))
    return redirect(url_for("index"))


@app.route("/lists/add", methods=["POST"])
def add_list_entry():
    name = request.form.get("name", "").strip()
    mid = request.form.get("mid", "").strip()
    list_type = request.form.get("list_type", "whitelist")
    if name:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO list_entries (name, mid, list_type, enabled)
                VALUES (?, ?, ?, 1)
                """,
                (name, mid, list_type),
            )
    return redirect(url_for("index"))


@app.route("/lists/<int:entry_id>/toggle", methods=["POST"])
def toggle_list_entry(entry_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE list_entries SET enabled = 1 - enabled WHERE id = ?",
            (entry_id,),
        )
    return redirect(url_for("index"))


@app.route("/lists/<int:entry_id>/delete", methods=["POST"])
def delete_list_entry(entry_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM list_entries WHERE id = ?", (entry_id,))
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
