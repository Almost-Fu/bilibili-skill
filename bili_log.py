#!/usr/bin/env python3
"""查看 B站 MCP 的操作审计日志。

用法：
    python bili_log.py                  # 今天的最近 30 条
    python bili_log.py -n 100           # 今天最近 100 条
    python bili_log.py --writes         # 只看写入操作（评论/私信/动态/投稿）
    python bili_log.py --tool bili_reply
    python bili_log.py --summary        # 今天的操作统计
    python bili_log.py --date 2026-10-22
    python bili_log.py --list           # 有哪些天的日志
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from mcp_audit import LOG_DIR, log_days, read_records


def _short(value: object, limit: int = 70) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ").replace("\r", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _args_line(args: dict) -> str:
    if not args:
        return ""
    parts = [f"{k}={_short(v, 50)}" for k, v in args.items()]
    return "  " + ", ".join(parts)


def cmd_list() -> int:
    days = log_days()
    if not days:
        print(f"日志目录为空: {LOG_DIR}")
        return 0
    print(f"日志目录: {LOG_DIR}")
    for day in days:
        records = read_records(limit=None, date=day)
        errors = sum(1 for r in records if r.get("status") != "ok")
        writes = sum(1 for r in records if r.get("category") == "write")
        print(f"  {day}  {len(records)} 条  写入 {writes}  失败 {errors}")
    return 0


def cmd_summary(date: str | None) -> int:
    records = read_records(limit=None, date=date)
    if not records:
        print(f"{date or '今天'} 没有操作记录（目录: {LOG_DIR}）")
        return 0
    counter = Counter(r.get("tool", "?") for r in records)
    errors = [r for r in records if r.get("status") != "ok"]
    writes = [r for r in records if r.get("category") == "write"]
    times = sorted(r.get("time", "") for r in records)
    print(f"日期: {records[0].get('date', date or 'today')}  共 {len(records)} 次调用")
    print(f"时间范围: {times[0]} ~ {times[-1]}")
    print(f"写入操作: {len(writes)} 次    失败: {len(errors)} 次")
    print("\n工具调用次数:")
    for tool, n in counter.most_common():
        mark = " [写入]" if tool in {
            "bili_reply", "bili_favorite", "bili_send_dynamic",
            "bili_send_message", "bili_upload_video", "bili_upload_video_multi",
            "bili_send_opus",
        } else ""
        print(f"  {n:>4}  {tool}{mark}")
    if writes:
        print("\n写入明细:")
        for r in reversed(writes):
            print(f"  {r.get('time','')}  {r.get('tool','')}  {_args_line(r.get('args', {}))[:120]}")
    if errors:
        print("\n失败明细:")
        for r in reversed(errors):
            reason = r.get("error") or _short(r.get("result", ""), 100)
            print(f"  {r.get('time','')}  {r.get('tool','')}  {reason}")
    return 0


def cmd_list_records(args: argparse.Namespace) -> int:
    records = read_records(
        limit=args.tail,
        date=args.date,
        tool=args.tool,
        writes_only=args.writes,
    )
    if not records:
        print(f"没有匹配的记录（目录: {LOG_DIR}）")
        return 0
    print(f"日志目录: {LOG_DIR}")
    print(f"共 {len(records)} 条，最新在前\n")
    for r in records:
        mark = "[OK] " if r.get("status") == "ok" else "[ERR]"
        kind = {"write": "写入", "login": "登录", "crawl": "采集", "read": "读取"}.get(
            r.get("category", ""), r.get("category", "")
        )
        duration = r.get("duration_ms", 0) / 1000
        print(f"{r.get('time','')} [{kind}] {r.get('tool',''):<26} {mark} {duration:6.2f}s{_args_line(r.get('args', {}))}")
        if r.get("status") != "ok":
            reason = r.get("error") or _short(r.get("result", ""), 120)
            print(f"          └─ {reason}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="查看 B站 MCP 操作审计日志")
    parser.add_argument("-n", "--tail", type=int, default=30, help="显示最近多少条，默认 30")
    parser.add_argument("-d", "--date", default=None, help="日期 YYYY-MM-DD / today / yesterday")
    parser.add_argument("-t", "--tool", default=None, help="只看指定工具")
    parser.add_argument("-w", "--writes", action="store_true", help="只看写入操作")
    parser.add_argument("-s", "--summary", action="store_true", help="输出当天统计")
    parser.add_argument("-l", "--list", action="store_true", dest="list_days", help="列出有日志的日期")
    args = parser.parse_args()

    try:
        if args.list_days:
            return cmd_list()
        if args.summary:
            return cmd_summary(args.date)
        return cmd_list_records(args)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
