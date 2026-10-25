#!/usr/bin/env python3
"""私信读取与归一化。

B 站私信接口返回的字段名没有稳定文档，而且文本消息的 ``content`` 本身
又是一段 JSON 字符串，所以这里做两层宽容解析：

* ``normalize_sessions`` / ``normalize_messages`` 只做纯数据转换，方便测试；
* ``fetch_unread_sessions`` 负责真正请求，把未读会话和最近几条消息拼好。

只读，不改变账号状态；发送私信仍然走 ``bili_send_message``。
"""

from __future__ import annotations

import json
from typing import Any

from bilibili_api import session
from bili_utils import retry_network, to_nonzero_int


def _unwrap(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, dict) else payload
    return {}


def _find_list(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    """在不同版本的接口外壳里找到真正的列表。"""
    data = _unwrap(payload)
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return payload if isinstance(payload, list) else []


def _text_of(content: Any) -> str:
    """私信文本经常是 ``{"content": "..."}`` 的 JSON 字符串。"""
    if isinstance(content, dict):
        for key in ("content", "text", "msg"):
            value = content.get(key)
            if isinstance(value, str):
                return value
        return ""
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict):
            for key in ("content", "text", "msg"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
            return ""
        return text
    return text


def _images_of(content: Any) -> list[str]:
    """抽出图片消息里的图片地址（msg_type=2 的 content 是 JSON 字符串）。"""
    data = content
    if isinstance(content, str):
        text = content.strip()
        if not text.startswith("{"):
            return []
        try:
            data = json.loads(text)
        except ValueError:
            return []
    urls: list[str] = []
    if isinstance(data, dict):
        candidates = [data.get("url")]
        for key in ("pics", "images"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(
                    item.get("url") if isinstance(item, dict) else item
                    for item in value
                )
        for url in candidates:
            if (
                isinstance(url, str)
                and url.startswith(("http://", "https://"))
                and url not in urls
            ):
                urls.append(url)
    return urls


def _self_uid(credential: Any) -> int | None:
    for name in ("dedeuserid", "dede_user_id", "uid"):
        value = getattr(credential, name, None)
        if value:
            return to_nonzero_int(value)
    return None


def normalize_sessions(
    payload: Any, *, self_uid: int | None = None
) -> list[dict[str, Any]]:
    """把会话列表压成 ``uid / unread / last_ts / last_text``。"""
    raw = _find_list(payload, ("session_list", "sessions", "list", "items"))
    if not raw:
        return []

    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        uid = to_nonzero_int(
            item.get("talker_id")
            or item.get("uid")
            or item.get("mid")
            or item.get("sender_uid")
        )
        if uid is None:
            continue
        unread = to_nonzero_int(item.get("unread_count") or item.get("unread")) or 0
        last = item.get("last_msg")
        last = last if isinstance(last, dict) else {}
        last_text = _text_of(
            last.get("content")
            or last.get("msg")
            or item.get("last_msg")
            or item.get("content")
        )
        sender = to_nonzero_int(
            last.get("sender_uid") or last.get("sender_id") or last.get("uid")
        )
        last_from_self = (
            self_uid is not None and sender is not None and sender == self_uid
        )
        last_ts = to_nonzero_int(
            last.get("timestamp")
            or last.get("ts")
            or last.get("time")
            or item.get("session_ts")
            or item.get("last_msg_ts")
        ) or 0
        # system_msg_type 非 0 或带 red_alert_text 的是 B 站系统通知
        # （登录提醒、账号安全等），不是能对话的网友。
        system = bool(
            to_nonzero_int(item.get("system_msg_type"))
            or str(item.get("red_alert_text") or "").strip()
        )
        out.append(
            {
                "uid": uid,
                "unread": int(unread),
                "last_ts": int(last_ts),
                "last_text": last_text,
                "last_from_self": bool(last_from_self),
                "system": system,
            }
        )
    return out


def normalize_messages(
    payload: Any, *, self_uid: int | None = None
) -> list[dict[str, Any]]:
    """把一段会话里的消息压成 ``sender_uid / text / ts / from_self / images``。"""
    raw = _find_list(payload, ("messages", "msgs", "list", "items"))
    if not raw:
        return []

    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sender = to_nonzero_int(
            item.get("sender_uid") or item.get("sender_id") or item.get("uid")
        )
        msg_type = to_nonzero_int(item.get("msg_type") or item.get("type")) or 0
        images = _images_of(item.get("content"))
        text = _text_of(item.get("content") or item.get("text") or item.get("msg"))
        if not text and images:
            text = "[图片]"
        elif not text and msg_type not in (1, 0):
            text = f"[非文本消息 type={msg_type}]"
        ts = to_nonzero_int(
            item.get("timestamp")
            or item.get("ts")
            or item.get("time")
            or item.get("msg_time")
        ) or 0
        out.append(
            {
                "sender_uid": sender,
                "text": text,
                "ts": int(ts),
                "from_self": bool(
                    self_uid is not None and sender is not None and sender == self_uid
                ),
                "images": images,
            }
        )
    out.sort(key=lambda item: item["ts"])
    return out


async def fetch_unread_sessions(
    credential: Any,
    *,
    limit: int = 5,
    per_session: int = 8,
) -> list[dict[str, Any]]:
    """返回未读会话（附最近几条消息），没有未读时返回空列表。"""
    me = _self_uid(credential)
    payload = await retry_network(
        lambda: session.get_sessions(credential, session_type=1),
        what="拉取私信会话",
    )
    sessions = [
        item
        for item in normalize_sessions(payload, self_uid=me)
        if item["unread"] > 0
        and not item["last_from_self"]
        and not item["system"]
    ]
    sessions.sort(key=lambda item: item["last_ts"], reverse=True)

    out: list[dict[str, Any]] = []
    for item in sessions[: max(1, limit)]:
        entry = dict(item)
        entry["messages"] = []
        try:
            messages = await retry_network(
                lambda: session.fetch_session_msgs(
                    item["uid"], credential, session_type=1
                ),
                what=f"拉取会话 {item.get('uid')} 的消息",
            )
            entry["messages"] = normalize_messages(messages, self_uid=me)[
                -max(1, per_session) :
            ]
        except Exception as exc:  # 单个会话失败不影响其他会话
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out.append(entry)
    return out


def render_transcript(sessions: list[dict[str, Any]], max_chars: int = 2000) -> str:
    """把未读私信渲染成可放进 UNTRUSTED 围栏的纯文本。"""
    lines: list[str] = []
    for item in sessions:
        lines.append(f"## 会话 UID {item.get('uid')}（未读 {item.get('unread', 0)}）")
        messages = item.get("messages") or []
        if not messages and item.get("last_text"):
            messages = [{"text": item["last_text"], "from_self": False}]
        for msg in messages:
            who = "我" if msg.get("from_self") else "对方"
            suffix = "（含图片）" if msg.get("images") else ""
            lines.append(f"- {who}：{msg.get('text', '')}{suffix}")
        if item.get("error"):
            lines.append(f"- [读取消息失败] {item['error']}")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…[已截断]"
    return text
