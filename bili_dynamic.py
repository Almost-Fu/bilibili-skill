#!/usr/bin/env python3
"""B站用户动态的读取与归一化。

只读；发动态仍然走 ``bili_send_dynamic``。动态接口的字段没有稳定文档，
而且图文、转发、视频投稿三种动态的结构完全不同，所以这里做宽容解析，
只保留 Agent 真正用得上的字段（作者、正文、配图、视频、互动数）。

``normalize_dynamics`` 是纯数据转换，方便测试；
``fetch_dynamics`` 负责真正请求。
"""

from __future__ import annotations

from typing import Any

from bilibili_api import user
from bili_utils import to_int


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "title"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _add_url(urls: list[str], value: Any) -> None:
    if (
        isinstance(value, str)
        and value.startswith(("http://", "https://"))
        and value not in urls
    ):
        urls.append(value)


def _picture_urls(major: dict[str, Any]) -> list[str]:
    """图文动态的配图、旧版图片动态的图、视频动态的封面。"""
    urls: list[str] = []
    opus = major.get("opus") or {}
    for pic in opus.get("pics") or []:
        if isinstance(pic, dict):
            _add_url(urls, pic.get("url"))
    draw = major.get("draw") or {}
    for item in draw.get("items") or []:
        if isinstance(item, dict):
            _add_url(urls, item.get("src"))
    for key in ("archive", "common"):
        block = major.get(key) or {}
        if isinstance(block, dict):
            _add_url(urls, block.get("cover"))
    return urls


def _stats(modules: dict[str, Any]) -> dict[str, int]:
    stat = modules.get("module_stat") or {}
    out: dict[str, int] = {}
    for key in ("like", "comment", "forward"):
        block = stat.get(key) or {}
        out[key] = to_int(block.get("count")) or 0 if isinstance(block, dict) else 0
    return out


def _jump_url(major: dict[str, Any]) -> str:
    for key in ("opus", "archive", "common"):
        block = major.get(key) or {}
        url = block.get("jump_url") if isinstance(block, dict) else None
        if not isinstance(url, str):
            continue
        if url.startswith("//"):
            return "https:" + url
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _one(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    dynamic = modules.get("module_dynamic") or {}
    major = dynamic.get("major") or {}
    opus = major.get("opus") or {}
    archive = major.get("archive") or {}

    text = _text(dynamic.get("desc"))
    if not text:
        text = _text(opus.get("summary")) or _text(opus.get("title"))
    if not text and archive:
        text = _text(archive.get("title")) or _text(archive.get("desc"))

    entry: dict[str, Any] = {
        "id": str(item.get("id_str") or ""),
        "type": item.get("type") or "",
        "author": author.get("name") or "",
        "author_uid": to_int(author.get("mid")) or 0,
        "published_at": to_int(author.get("pub_ts")) or 0,
        "pub_time": author.get("pub_time") or "",
        "text": text,
        "pictures": _picture_urls(major),
        "stats": _stats(modules),
        "url": _jump_url(major),
    }

    if archive.get("bvid"):
        entry["video"] = {
            "bvid": archive.get("bvid"),
            "title": archive.get("title") or "",
        }

    # 转发动态：正文只有转发语，原动态才是内容主体
    orig = item.get("orig")
    if isinstance(orig, dict):
        orig_modules = orig.get("modules") or {}
        orig_author = orig_modules.get("module_author") or {}
        orig_dynamic = orig_modules.get("module_dynamic") or {}
        orig_major = orig_dynamic.get("major") or {}
        entry["forwarded_from"] = orig_author.get("name") or ""
        entry["forwarded_text"] = (
            _text(orig_dynamic.get("desc"))
            or _text((orig_major.get("opus") or {}).get("summary"))
        )
        for url in _picture_urls(orig_major):
            if url not in entry["pictures"]:
                entry["pictures"].append(url)
    return entry


def normalize_dynamics(payload: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else None
    if items is None and isinstance(payload, dict):
        value = payload.get("items")
        items = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for item in items or []:
        entry = _one(item)
        if entry is not None:
            out.append(entry)
        if len(out) >= max(1, limit):
            break
    return out


async def fetch_dynamics(
    credential: Any, uid: int, *, limit: int = 5
) -> list[dict[str, Any]]:
    """返回某个用户最近的动态，最新在前。"""
    payload = await user.User(int(uid), credential=credential).get_dynamics_new()
    return normalize_dynamics(payload, limit=max(1, limit))
