#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime


def fetch_json(url: str, sessdata: str, retries: int = 3) -> tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    request_obj = urllib.request.Request(url, headers=headers)
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request_obj, timeout=10) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8", errors="replace")
        if status == 429 and attempt < retries - 1:
            print(f"rate limited (HTTP {status}), retry in {delay:.1f}s")
            time.sleep(delay)
            delay *= 2
            continue
        return status, body
    return status, body


def fmt_ts(ts: int) -> str:
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def resolve_sessdata(value: str) -> str:
    if value:
        return value
    env_value = os.getenv("BILI_SESSDATA", "")
    if env_value:
        return env_value
    return getpass.getpass("SESSDATA: ").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessdata", default="", help="B站 SESSDATA（可留空）")
    parser.add_argument("--mids", required=True, help="MID 列表，逗号分隔")
    parser.add_argument("--hours", type=int, default=2, help="推送频率小时数")
    parser.add_argument("--sleep", type=float, default=1.0, help="每次请求间隔秒数")
    args = parser.parse_args()

    sessdata = resolve_sessdata(args.sessdata)
    mids = [m.strip() for m in args.mids.split(",") if m.strip()]
    cutoff_ts = int(time.time()) - max(args.hours, 1) * 3600
    print(f"cutoff_ts={cutoff_ts} ({fmt_ts(cutoff_ts)})")

    for mid in mids:
        query = urllib.parse.urlencode(
            {
                "mid": mid,
                "pn": 1,
                "ps": 30,
                "order": "pubdate",
            }
        )
        url = f"https://api.bilibili.com/x/space/arc/search?{query}"
        status, body = fetch_json(url, sessdata)

        print("=" * 70)
        print(f"MID={mid}")
        print(f"URL={url}")
        print(f"Cookie={'yes' if sessdata else 'no'}")
        print(f"HTTP_STATUS={status}")
        print(f"BODY_HEAD={body[:300]}")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print("PARSE_ERROR=invalid_json")
            time.sleep(args.sleep)
            continue

        code = data.get("code")
        message = data.get("message")
        vlist = (data.get("data", {}).get("list", {}) or {}).get("vlist") or []
        hits = [item for item in vlist if int(item.get("created") or 0) >= cutoff_ts]
        created_ts = [int(item.get("created") or 0) for item in hits]

        earliest = min(created_ts) if created_ts else 0
        latest = max(created_ts) if created_ts else 0

        print(f"API_CODE={code}")
        print(f"API_MESSAGE={message}")
        print(f"HIT_COUNT={len(hits)}")
        print(f"EARLIEST={fmt_ts(earliest)}")
        print(f"LATEST={fmt_ts(latest)}")
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
