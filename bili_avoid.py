"""冷处理名单：对方明确不欢迎之后，安静地退场。

Agent 在评论区被人明确表示不满、厌烦（“别回了”“烦不烦”、语气敌意），
道歉一次之后把那个楼层或那个 UP 主记进名单。之后：

* 不再回复这个楼层（循环层先拦，``bili_reply`` 再拦一次）；
* 如果对方是 UP 主，不再在他/她的视频下评论（``bili_reply`` 拿到视频
  信息后按 ``owner.mid`` 拦截，不需要额外请求）；
* 宿主不再为这些楼层安排“处理回复”的唤醒，避免反复撞同一堵墙。

名单落在 ``data/agent.avoid.json``，和别的运行状态一样不进 git：

.. code-block:: json

    {
      "threads": {"123456": {"bvid": "BV1xx", "reason": "...", "at": 1792838000}},
      "ups": {"123456789": {"name": "某某", "reason": "...", "at": 1792838000}}
    }

只增不减：想解禁就直接编辑这个文件删掉对应条目，Agent 下次读就生效，
不需要重启。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import bili_time

DATA_DIR = Path(__file__).parent / "data"
# 宿主和 MCP Server 分属不同进程或目录、需要共用同一份名单时，
# 由宿主的 .env 用 BILI_AVOID_PATH 指向宿主的 data/ 目录。
AVOID_PATH = Path(
    os.getenv("BILI_AVOID_PATH") or (DATA_DIR / "agent.avoid.json")
)

# 同一进程里 Agent 会写，MCP Server 子进程只读；写操作加锁避免并发覆盖
_LOCK = threading.Lock()

_DEFAULT_REASON = "对方明确表示过不欢迎"


def _empty() -> dict[str, dict[str, dict[str, Any]]]:
    return {"threads": {}, "ups": {}}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class AvoidList:
    """冷处理名单的读写。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else AVOID_PATH

    # -- 读写 ----------------------------------------------------------

    def load(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读名单。文件不存在或写坏了都按空名单处理，不影响主流程。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _empty()
        if not isinstance(raw, dict):
            return _empty()
        data = _empty()
        for key in ("threads", "ups"):
            value = raw.get(key)
            if isinstance(value, dict):
                data[key] = {
                    str(k): v for k, v in value.items() if isinstance(v, dict)
                }
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    # -- 记账 ----------------------------------------------------------

    def block_thread(
        self, rpid: Any, *, bvid: str = "", reason: str = ""
    ) -> dict[str, Any]:
        """避开的评论楼层。填楼层根评论，楼中楼的回复也会被拦。"""
        key = str(_as_int(rpid))
        entry = {"bvid": bvid, "reason": reason or _DEFAULT_REASON,
                 "at": int(time.time())}
        if not _as_int(rpid):
            raise ValueError("block_thread 需要有效的 rpid")
        with _LOCK:
            data = self.load()
            data["threads"][key] = entry
            self._save(data)
        return entry

    def block_up(
        self, uid: Any, *, name: str = "", reason: str = ""
    ) -> dict[str, Any]:
        """避开的 UP 主：以后不再在他/她的视频下评论。"""
        if not _as_int(uid):
            raise ValueError("block_up 需要有效的 uid")
        key = str(_as_int(uid))
        entry = {"name": name, "reason": reason or _DEFAULT_REASON,
                 "at": int(time.time())}
        with _LOCK:
            data = self.load()
            data["ups"][key] = entry
            self._save(data)
        return entry

    # -- 查询 ----------------------------------------------------------

    def thread_entry(self, *rpids: Any) -> dict[str, Any] | None:
        ids = [str(_as_int(value)) for value in rpids if _as_int(value)]
        if not ids:
            return None
        data = self.load()
        for key in ids:
            entry = data["threads"].get(key)
            if entry:
                return entry
        return None

    def up_entry(self, uid: Any) -> dict[str, Any] | None:
        if not _as_int(uid):
            return None
        return self.load()["ups"].get(str(_as_int(uid)))

    def reply_blocked(
        self,
        *,
        rpid: Any = 0,
        root: Any = 0,
        bvid: str = "",
        up_uid: Any = None,
        up_name: str = "",
    ) -> str | None:
        """该不该拦下这次评论/回复；拦就返回给模型看的原因。"""
        entry = self.thread_entry(rpid, root)
        if entry:
            where = f"（{entry.get('bvid')}）" if entry.get("bvid") else ""
            return (
                f"这个楼层{where}在冷处理名单里（{entry.get('reason')}），"
                "按规则不再回复。换别的地方逛吧。"
            )
        up = self.up_entry(up_uid)
        if up:
            who = up.get("name") or up_name or f"UID {_as_int(up_uid)}"
            return (
                f"UP 主「{who}」在冷处理名单里（{up.get('reason')}），"
                "按规则不再在其视频下评论。"
            )
        return None

    def summary_lines(self, limit: int = 6) -> list[str]:
        """给模型看的简短名单，避免它又去撞同一堵墙。"""
        data = self.load()
        lines: list[str] = []
        threads = list(data["threads"].items())[-limit:]
        for key, entry in threads:
            where = f" {entry.get('bvid')}" if entry.get("bvid") else ""
            when = bili_time.stamp("%m-%d", entry.get("at"))
            lines.append(f"rpid {key}{where}（{when}）：{entry.get('reason')}")
        ups = list(data["ups"].items())[-limit:]
        for key, entry in ups:
            who = entry.get("name") or f"UID {key}"
            when = bili_time.stamp("%m-%d", entry.get("at"))
            lines.append(f"UP 主 {who}（UID {key}，{when}）：{entry.get('reason')}")
        return lines[: limit * 2]

    def items(self) -> dict[str, list[dict[str, Any]]]:
        """人类看的完整名单（按登记时间排序）。"""
        data = self.load()
        threads = [
            {"rpid": int(k), **v}
            for k, v in sorted(data["threads"].items(), key=lambda x: x[1].get("at") or 0)
        ]
        ups = [
            {"uid": int(k), **v}
            for k, v in sorted(data["ups"].items(), key=lambda x: x[1].get("at") or 0)
        ]
        return {"threads": threads, "ups": ups}


DEFAULT = AvoidList()


def thread_entry(*rpids: Any) -> dict[str, Any] | None:
    return DEFAULT.thread_entry(*rpids)


def up_entry(uid: Any) -> dict[str, Any] | None:
    return DEFAULT.up_entry(uid)


def reply_blocked(**kwargs: Any) -> str | None:
    return DEFAULT.reply_blocked(**kwargs)
