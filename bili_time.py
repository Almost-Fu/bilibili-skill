"""给人、给日志、给模型看的时间一律按北京时间（UTC+8）。

系统时区可能与账号作息不一致：判断“现在几点”、按天切分额度、写日志、
给模型看的时间，全部按 UTC+8，不跟系统时区走。

库里的时间戳仍然是 unix 秒，不受影响；这里只负责显示和按天分界。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def now() -> datetime:
    """当前北京时间。"""
    return datetime.now(CN_TZ)


def at(ts: float | int) -> datetime:
    """把 unix 时间戳换成北京时间。"""
    return datetime.fromtimestamp(float(ts), CN_TZ)


def stamp(fmt: str = "%Y-%m-%d %H:%M", ts: float | int | None = None) -> str:
    """格式化北京时间；ts 为空时用当前时间。"""
    moment = at(ts) if ts is not None else now()
    return moment.strftime(fmt)


def today(ts: float | int | None = None) -> str:
    """北京时间的日期字符串（YYYY-MM-DD）。"""
    return stamp("%Y-%m-%d", ts)


def day_start(ts: float | int) -> int:
    """北京时间当天 0 点对应的 unix 秒。"""
    moment = at(ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(moment.timestamp())
