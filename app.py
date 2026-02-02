from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from flask import Flask, redirect, render_template, request, url_for

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


@app.route("/")
def index():
    with get_connection() as conn:
        keywords = fetch_keywords(conn)
        creators = fetch_up_creators(conn)
        list_entries = fetch_list_entries(conn)
        settings = fetch_settings(conn)

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
