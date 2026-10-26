"""无业务依赖的小工具。

放在这里的东西必须同时满足：不依赖项目内其他模块、不掺杂 B 站接口语义。
目前有三类：

* 数字转换：B 站接口的字段经常是字符串、空串或 0，需要统一收口；
* JSON 文件读写：先写临时文件再替换，进程中断不会留下半截内容；
* 网络重试：DNS/连接/超时这类偶发失败重试几次，业务错误不重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
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

# bilibili-api 允许换用 httpx / aiohttp / curl_cffi，连接失败和超时会以
# 各自的异常类型抛出，而它们都不继承 OSError。按异常基类的模块名识别，
# 既不用强行 import 可选依赖，也不会把 B 站业务错误（NetworkException）
# 误当成网络抖动去重试。
_NETWORK_ERROR_MODULES = frozenset({"httpx", "aiohttp", "curl_cffi"})


def is_retryable_network_error(exc: BaseException) -> bool:
    """判断异常是否属于可以退避重试的本地网络故障。"""
    if isinstance(exc, (OSError, asyncio.TimeoutError)):
        return True
    return any(
        (base.__module__ or "").split(".")[0] in _NETWORK_ERROR_MODULES
        for base in type(exc).__mro__
    )


async def retry_network(
    operation: Callable[[], Awaitable[_T]],
    *,
    what: str = "请求",
    attempts: int = NETWORK_RETRY_ATTEMPTS,
    base_delay: float = NETWORK_RETRY_BASE_DELAY,
    max_delay: float = NETWORK_RETRY_MAX_DELAY,
) -> _T:
    """执行一次网络请求，遇到网络层错误按指数退避重试。

    覆盖 ``OSError``（DNS 解析失败、连接被拒）、``asyncio.TimeoutError``
    以及 httpx / aiohttp / curl_cffi 的连接类异常，这几类都是本地网络抖动，
    重试有实际意义；B 站返回的业务错误（如风控、HTTP 412/429）会原样抛出，
    交给调用方判断。
    """
    attempts = max(1, int(attempts))
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise
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


def replace_file(source: str | Path, target: str | Path) -> None:
    """``os.replace`` 的 Windows 友好版本。

    Windows 上目标文件被杀毒、索引或另一个进程短暂占用时，``os.replace``
    会抛 ``PermissionError``；换文件本身是幂等的，短暂重试即可，不必让
    凭证/状态写入直接失败。
    """
    source, target = Path(source), Path(target)
    attempts = 5 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt >= attempts - 1:
                raise
            time.sleep(0.05 * (2**attempt))


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
    text = json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"
    # mkstemp 每次生成唯一文件名：两个进程同时写同一个目标时不会互相
    # 截断固定名字的 .tmp 文件。
    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        replace_file(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def file_lock(
    path: str | Path, *, timeout: float = 10.0
) -> Iterator[bool]:
    """跨进程排他锁，返回是否真的拿到了锁。

    锁文件是 ``<path>.lock``。拿不到锁（超时、平台不支持）时 yield
    ``False`` 并继续执行，而不是抛异常——锁只是防止并发刷新凭证时互相
    覆盖，不能反过来把主流程卡死。
    """
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
        else:
            import fcntl

            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
        yield acquired
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
