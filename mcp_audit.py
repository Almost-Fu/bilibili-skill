#!/usr/bin/env python3
"""B站 MCP Server 调用审计日志。

记录 AI 每一次工具调用的时间、参数、耗时、结果状态，输出两种格式：

- ``logs/activity-YYYY-MM-DD.jsonl``  机器可读，一行一条记录
- ``logs/activity-YYYY-MM-DD.md``     人可读，按会话分组的 Markdown

设计约束：日志失败绝不能影响工具本身，所有写入异常都被吞掉。
日志目录可用环境变量 ``BILI_MCP_LOG_DIR`` 覆盖。
"""

from __future__ import annotations

import bili_time
import functools
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

LOG_DIR = Path(os.environ.get("BILI_MCP_LOG_DIR") or (Path(__file__).parent / "logs"))
SESSION_ID = f"{bili_time.stamp('%Y%m%d-%H%M%S')}-{os.getpid()}"
SESSION_STARTED = bili_time.now().isoformat(timespec="seconds")

# 单个字符串参数/结果的最大留存长度，避免字幕、二维码把日志撑爆
MAX_ARG_CHARS = 4000
MAX_RESULT_CHARS = 8000
MAX_MD_RESULT_CHARS = 1200

WRITE_TOOLS = {
    "bili_reply",
    "bili_favorite",
    "bili_send_dynamic",
    "bili_send_message",
    "bili_send_sticker",
    "bili_upload_video",
    "bili_upload_video_multi",
    "bili_send_opus",
}
LOGIN_TOOLS = {"bili_login", "bili_login_check", "bili_check_credential"}
CRAWL_TOOLS = {"bili_crawl"}

# 查询日志本身不记录，否则每看一次日志就多一条噪音
EXCLUDE_TOOLS = {"bili_activity_log"}

_LOCK = threading.Lock()
_session_header_written = False


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"…[truncated {len(value) - limit} chars]"


def _jsonable(value: Any, depth: int = 0) -> Any:
    """把任意参数值转成可 JSON 序列化的结构，同时限制长度。"""
    if depth > 4:
        return _clip(repr(value), 500)
    if isinstance(value, str):
        return _clip(value, MAX_ARG_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in value]
    return _clip(repr(value), 500)


def _category(tool: str) -> str:
    if tool in WRITE_TOOLS:
        return "write"
    if tool in LOGIN_TOOLS:
        return "login"
    if tool in CRAWL_TOOLS:
        return "crawl"
    return "read"


def _classify(result: Any) -> str:
    """工具自己返回 {"success": false} / {"error": ...} 时也算失败。"""
    if not isinstance(result, str):
        return "ok"
    text = result.lstrip()
    if not text.startswith("{"):
        return "ok"
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return "ok"
    if not isinstance(data, dict):
        return "ok"
    if data.get("success") is False:
        return "error"
    if data.get("error"):
        return "error"
    if data.get("status") in {"timeout", "no_session", "unknown"}:
        return "error"
    return "ok"


def _format_args(args: dict[str, Any]) -> str:
    if not args:
        return "（无参数）"
    parts = []
    for key, value in args.items():
        if isinstance(value, str):
            shown = value.replace("\n", " ").replace("\r", " ")
            shown = shown if len(shown) <= 200 else shown[:200] + "…"
            parts.append(f"`{key}`={json.dumps(shown, ensure_ascii=False)}")
        else:
            shown = json.dumps(value, ensure_ascii=False)
            shown = shown if len(shown) <= 200 else shown[:200] + "…"
            parts.append(f"`{key}`={shown}")
    return "，".join(parts)


def _format_markdown(record: dict[str, Any]) -> str:
    ok = record["status"] == "ok"
    mark = "[OK]" if ok else "[ERR]"
    kind = {
        "write": "写入操作",
        "login": "登录",
        "crawl": "批量采集",
        "read": "读取",
    }.get(record["category"], record["category"])
    lines = [
        f"### {record['time']} · `{record['tool']}` · {mark} · {record['duration_ms'] / 1000:.2f}s",
        f"- **类型**: {kind}",
        f"- **参数**: {_format_args(record['args'])}",
    ]
    if record.get("error"):
        lines.append(f"- **错误**: {record['error']}")
    if record.get("result"):
        preview = record["result"].replace("\r", " ")
        preview = preview[:MAX_MD_RESULT_CHARS]
        if record.get("result_truncated") or len(record["result"]) > MAX_MD_RESULT_CHARS:
            preview += " …"
        lines.append(f"- **结果**: {preview}")
    return "\n".join(lines) + "\n\n"


def _day_paths(day: str) -> tuple[Path, Path]:
    return (
        LOG_DIR / f"activity-{day}.jsonl",
        LOG_DIR / f"activity-{day}.md",
    )


def _write_record(record: dict[str, Any]) -> None:
    global _session_header_written
    day = record["date"]
    jsonl_path, md_path = _day_paths(day)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            needs_header = not md_path.exists() or md_path.stat().st_size == 0
            with open(md_path, "a", encoding="utf-8") as f:
                if needs_header:
                    f.write(
                        f"# B站 MCP 操作日志 · {day}\n\n"
                        "> 记录 AI 对该B站账号的每一次工具调用：时间、参数、耗时与结果。\n\n"
                    )
                if not _session_header_written:
                    f.write(f"## 会话 {SESSION_ID}（启动于 {SESSION_STARTED}）\n\n")
                    _session_header_written = True
                f.write(_format_markdown(record))
    except Exception:
        # 审计日志绝不能影响正常工具调用
        pass


def audit(fn):
    """包装一个 MCP 工具函数，记录调用与结果。"""

    @functools.wraps(fn)
    async def wrapper(**kwargs):
        started = time.perf_counter()
        result: Any = None
        error: str | None = None
        status = "ok"
        try:
            result = await fn(**kwargs)
            status = _classify(result)
            return result
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            now = bili_time.now()
            result_text = result if isinstance(result, str) else (
                json.dumps(result, ensure_ascii=False) if result is not None else ""
            )
            record = {
                "time": now.strftime("%H:%M:%S"),
                "ts": now.isoformat(timespec="milliseconds"),
                "unix": round(time.time(), 3),
                "date": now.strftime("%Y-%m-%d"),
                "session": SESSION_ID,
                "tool": fn.__name__,
                "category": _category(fn.__name__),
                "status": status,
                "duration_ms": duration_ms,
                "args": _jsonable(kwargs),
                "result": _clip(result_text, MAX_RESULT_CHARS),
                "result_chars": len(result_text),
                "result_truncated": len(result_text) > MAX_RESULT_CHARS,
                "error": error,
            }
            _write_record(record)

    wrapper.__audit_wrapped__ = True
    return wrapper


def install_audit(mcp) -> int:
    """给已注册的全部工具套上审计包装，返回包装数量。"""
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None) or {}
    wrapped = 0
    for name, tool in tools.items():
        if name in EXCLUDE_TOOLS:
            continue
        fn = getattr(tool, "fn", None)
        if fn is None or getattr(fn, "__audit_wrapped__", False):
            continue
        tool.fn = audit(fn)
        wrapped += 1
    return wrapped


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_day(date: str | None) -> str:
    if not date or date in {"today", "今天"}:
        return bili_time.today()
    if date in {"yesterday", "昨天"}:
        from datetime import timedelta

        return (bili_time.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # 只接受 YYYY-MM-DD，避免把任意路径拼进日志文件名
    return date if _DAY_RE.match(date) else bili_time.today()


def log_days() -> list[str]:
    """列出已有日志的日期，新的在前。"""
    try:
        return sorted(
            (p.name[len("activity-"):-len(".jsonl")] for p in LOG_DIR.glob("activity-*.jsonl")),
            reverse=True,
        )
    except OSError:
        return []


def read_records(
    limit: int | None = 50,
    date: str | None = None,
    tool: str | None = None,
    writes_only: bool = False,
) -> list[dict[str, Any]]:
    """读取某天的记录，返回最新在前的列表。"""
    day = _resolve_day(date)
    jsonl_path, _ = _day_paths(day)
    if not jsonl_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if tool and record.get("tool") != tool:
                    continue
                if writes_only and record.get("category") != "write":
                    continue
                records.append(record)
    except OSError:
        return []
    if limit:
        records = records[-limit:]
    return list(reversed(records))
