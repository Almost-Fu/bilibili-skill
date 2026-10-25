"""无业务依赖的小工具。

放在这里的东西必须同时满足：不依赖项目内其他模块、不掺杂 B 站接口语义。
目前有三类：

* 数字转换：B 站接口的字段经常是字符串、空串或 0，需要统一收口；
* JSON 文件读写：先写临时文件再替换，进程中断不会留下半截内容；
* 网络重试：DNS/连接/超时这类偶发失败重试几次，业务错误不重试。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

_T = TypeVar("_T")

_LOGGER = logging.getLogger("bili_utils.retry")

# 私信接口（api.vc.bilibili.com）会偶发 DNS 解析失败；一次失败就放弃
# 会让上层把"没查成"误读成"没有消息"。重试只针对网络层错误：
# 业务错误（风控、参数不对、HTTP 412/429 等）重试只会更糟。
NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_BASE_DELAY = 0.8
NETWORK_RETRY_MAX_DELAY = 4.0


async def retry_network(
    operation: Callable[[], Awaitable[_T]],
    *,
    what: str = "请求",
    attempts: int = NETWORK_RETRY_ATTEMPTS,
    base_delay: float = NETWORK_RETRY_BASE_DELAY,
    max_delay: float = NETWORK_RETRY_MAX_DELAY,
) -> _T:
    """执行一次网络请求，遇到网络层错误按指数退避重试。

    ``OSError`` 覆盖 DNS 解析失败（``getaddrinfo failed``）、连接被拒和
    系统级超时，这几类都是本地网络抖动，重试有实际意义；B 站返回的业务
    错误不继承 ``OSError``，会原样抛出，交给调用方判断。
    """
    attempts = max(1, int(attempts))
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except OSError as exc:
            last = exc
            if attempt >= attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            _LOGGER.warning(
                "%s失败（第 %d/%d 次）：%s，%.1f 秒后重试",
                what,
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def to_int(value: Any) -> int | None:
    """尽量转成 int；``None`` / 空串 / 非法值返回 ``None``，0 原样保留。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_nonzero_int(value: Any) -> int | None:
    """把 0 也当成缺失再转 int，适合 UID、rpid、时间戳这类字段。"""
    number = to_int(value)
    return number or None


def read_json(path: str | Path) -> Any:
    """读 JSON；文件不存在、没有权限或内容坏掉时返回 ``None``。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json_atomic(
    path: str | Path,
    payload: Any,
    *,
    indent: int | None = 2,
) -> None:
    """原子写 JSON：先落临时文件再替换，失败时抛 ``OSError``。

    调用方决定写失败要不要影响主流程：游标类数据可以忽略，运行信息
    这类数据最好让异常冒出去。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, target)
