"""B 站凭证的加载、刷新与持久化。

B 站 web cookie 到期前会要求刷新：``Credential.check_refresh()`` 问服务端
“该刷新了吗”，``Credential.refresh()`` 用 refresh_token（ac_time_value）
换一套新 cookie。``bilibili_api`` 只把新 cookie 留在内存对象里，进程一退出
就丢；这里负责把它写回 ``bili_credential.json``，下次启动直接复用。

规则：

* 刷新检查按时间节流（默认 10 分钟一次），避免每次工具调用都打一次接口；
* 刷新失败不阻塞调用——旧 cookie 可能还有效，等下一轮再试；
* 写文件走临时文件 + 原子替换，避免进程被杀时留下半个凭证文件；
* 日志只记状态，不打印任何 cookie 值。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from bilibili_api import Credential

from bili_auth import save_credential
from bili_utils import file_lock

CRED_FILE = Path(__file__).resolve().parent / "bili_credential.json"

REQUIRED_KEYS = ("sessdata", "bili_jct", "dedeuserid")


def load_credential(path: str | Path | None = None) -> Credential | None:
    """从文件加载凭证；关键字段为空时视为未登录。"""
    path = Path(path or CRED_FILE)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(str(data.get(key) or "").strip() for key in REQUIRED_KEYS):
        return None
    return Credential(
        sessdata=str(data.get("sessdata") or ""),
        bili_jct=str(data.get("bili_jct") or ""),
        buvid3=str(data.get("buvid3") or ""),
        dedeuserid=str(data.get("dedeuserid") or ""),
        # 刷新必须用 ac_time_value（refresh_token）；漏掉它 refresh() 会直接报错
        ac_time_value=str(data.get("ac_time_value") or ""),
    )


def credential_to_dict(credential: Any) -> dict[str, Any]:
    """只取需要落盘的字段；空的 buvid 不覆盖文件里已有的值。"""
    data: dict[str, Any] = {
        "sessdata": str(getattr(credential, "sessdata", "") or ""),
        "bili_jct": str(getattr(credential, "bili_jct", "") or ""),
        "dedeuserid": str(getattr(credential, "dedeuserid", "") or ""),
        "ac_time_value": str(getattr(credential, "ac_time_value", "") or ""),
    }
    for key in ("buvid3", "buvid4"):
        value = str(getattr(credential, key, "") or "")
        if value:
            data[key] = value
    return data


def persist_credential(
    credential: Any, path: str | Path | None = None
) -> Path:
    """把刷新后的凭证写回文件，保留文件里其它我们不认识的字段。"""
    path = Path(path or CRED_FILE)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError):
            existing = {}
    existing.update(credential_to_dict(credential))
    return save_credential(existing, path)


class CredentialManager:
    """每个进程一个：负责加载、按需刷新、写回。"""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        refresh_interval: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ):
        self.path = Path(path or CRED_FILE)
        self.refresh_interval = refresh_interval
        self._clock = clock
        self.logger = logger or logging.getLogger("bili.credential")
        self._lock: asyncio.Lock | None = None
        self.last_check = 0.0
        self.last_refresh = 0.0
        self.last_error = ""

    def load(self) -> Credential | None:
        return load_credential(self.path)

    async def get(self) -> Credential | None:
        cred = self.load()
        if cred is None:
            return None
        return await self.maybe_refresh(cred)

    def _should_check(self, now: float, force: bool) -> bool:
        if force:
            return True
        return not self.last_check or now - self.last_check >= self.refresh_interval

    async def maybe_refresh(
        self, credential: Credential, *, force: bool = False
    ) -> Credential:
        """需要就刷新并落盘；任何失败都原样返回旧凭证。"""
        if not str(getattr(credential, "ac_time_value", "") or "").strip():
            # 没有 refresh_token 就只能等人类重新扫码
            return credential
        if not self._should_check(self._clock(), force):
            return credential

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = self._clock()
            if not self._should_check(now, force):
                return credential
            # 跨进程文件锁：两个宿主同时触发刷新时，refresh_token 会被
            # 轮换，后写的进程如果拿着旧值会覆盖掉新凭证。锁只覆盖
            # “检查 + 刷新 + 落盘”这一段；拿不到锁也继续执行。
            with file_lock(self.path) as locked:
                if locked:
                    current = load_credential(self.path)
                    if (
                        current is not None
                        and current.ac_time_value
                        and current.ac_time_value != credential.ac_time_value
                    ):
                        # 另一个进程已经刷新过，直接用文件里的新凭证。
                        self.last_check = now
                        self.last_refresh = self._clock()
                        self.last_error = ""
                        self.logger.info("检测到其他进程已刷新凭证，直接复用")
                        return current

                self.last_check = now
                try:
                    need = await credential.check_refresh()
                except Exception as exc:
                    self.last_error = f"check_refresh: {type(exc).__name__}: {exc}"
                    self.logger.info("检查 cookie 刷新失败：%s", exc)
                    return credential
                if not need:
                    return credential

                try:
                    await credential.refresh()
                except Exception as exc:
                    self.last_error = f"refresh: {type(exc).__name__}: {exc}"
                    self.logger.warning("刷新 cookie 失败：%s", exc)
                    return credential

                try:
                    persist_credential(credential, self.path)
                except OSError as exc:
                    self.last_error = f"persist: {type(exc).__name__}: {exc}"
                    self.logger.warning("凭证已刷新但写回失败：%s", exc)
                else:
                    self.last_error = ""
                    self.logger.info("凭证已刷新并写回 %s", self.path)
                self.last_refresh = self._clock()
                return credential
