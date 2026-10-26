#!/usr/bin/env python3
"""B站 MCP 服务入口。

把浏览、采集、互动、发布、私信和本地素材整理成 MCP 工具，通过 stdio
提供给 Agent 或 MCP 客户端。第一次使用需要扫码登录，凭证保存在同目录的
``bili_credential.json``，之后自动复用并按需续期。

工具按场景分成六组，分组只影响源码阅读顺序；对外稳定的是工具名、参数
和返回的 JSON 结构。
"""

import asyncio
import base64
import io
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import bili_art
import bili_avoid
import bili_config
import bili_draw
import bili_images
import bili_uploads
import qrcode
from mcp.server.fastmcp import FastMCP

from bilibili_api import (
    Credential,
    comment,
    dynamic,
    favorite_list,
    hot,
    rank,
    search,
    session,
    user,
    video,
)
from bilibili_api.exceptions import CredentialNoSessdataException
from bilibili_api.utils.picture import Picture
from bilibili_api.video_uploader import VideoMeta, VideoUploader, VideoUploaderPage

from bili_auth import QrLogin, save_credential
from bili_credential import CredentialManager
from bili_dm import fetch_unread_sessions
from bili_dynamic import fetch_dynamics
from bili_frames import fetch_dense_storyboard, fetch_frames, has_pyav
from bili_storyboard import WINDOW_SECONDS, fetch_storyboard, fetch_subtitle, resolve_window
from bili_utils import retry_network
from mcp_audit import LOG_DIR, install_audit, read_records

# ========== 基础初始化 ==========

ROOT = Path(__file__).resolve().parent
CRED_FILE = ROOT / "bili_credential.json"

mcp = FastMCP("bilibili-skill")
_credentials = CredentialManager(CRED_FILE)
_login_session: QrLogin | None = None

LOGIN_HINT = (
    "未登录B站！请先调用 bili_login 工具获取登录二维码，让用户扫码登录。"
)


class NotLoggedInError(Exception):
    """本地没有可用凭证；工具层会把它转成结构化 login_required。"""


def load_credential():
    """读取本地凭证；文件不存在或关键字段为空时返回 None。"""
    return _credentials.load()


async def get_cred():
    """取一份可用凭证，需要时顺带刷新；未登录时抛 ``NotLoggedInError``。"""
    cred = await _credentials.get()
    if not cred:
        raise NotLoggedInError(LOGIN_HINT)
    return cred


async def get_cred_or_error() -> tuple[Any, str | None]:
    """需要登录的工具入口，返回 ``(凭证, 登录提示 JSON)``。

    预期内的“未登录”走结构化返回，不抛 MCP isError；网络/接口故障仍然
    由各工具自己处理。
    """
    try:
        return await get_cred(), None
    except NotLoggedInError:
        return None, _json(
            {"success": False, "login_required": True, "error": LOGIN_HINT}
        )


async def get_cred_optional():
    """取一份供公开接口使用的凭证；未登录时返回空凭证而不是报错。

    B 站的视频、评论、弹幕、用户资料和公开收藏夹内容都不要求登录，这些
    只读工具不应该因为还没扫码就完全不可用。字幕接口是例外，它强制要求
    sessdata，由 ``bili_subtitle`` 单独给出登录提示。
    """
    cred = await _credentials.get()
    return cred or Credential()


# ========== 小工具 ==========


def _json(value: Any) -> str:
    """统一 JSON 输出格式，中文不转义。"""
    return json.dumps(value, ensure_ascii=False)


def _image_urls(content: Any) -> list[str]:
    """从评论 content 里取最多三张图片地址。"""
    if not isinstance(content, dict):
        return []
    raw = content.get("pictures")
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for entry in raw:
        url = entry.get("img_src") if isinstance(entry, dict) else entry
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            found.append(url)
    return found[:3]


def _outbound_image(
    value: str, max_mb: float, oversize_tail: str
) -> tuple[Path | None, str | None]:
    """校验出站图片来源和大小，返回 (路径, 错误)。"""
    try:
        path = bili_images.resolve_outbound_image(value)
    except bili_images.ImageSourceError as exc:
        return None, str(exc)
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        return None, f"图片太大（{size_mb:.1f}MB），{oversize_tail}"
    return path, None


async def _open_video(
    bvid: str = "", aid: int = 0, *, require_login: bool = True
):
    """打开视频并返回 (credential, Video, info)。

    ``bvid`` 和 ``aid`` 二选一；知道 av 号但不知道 BV 号时传 ``aid``。
    ``require_login=False`` 供只读工具使用：未登录时用空凭证访问公开接口，
    而不是直接报未登录。
    """
    cred = await get_cred() if require_login else await get_cred_optional()
    if bvid:
        client = video.Video(bvid=bvid, credential=cred)
    else:
        client = video.Video(aid=int(aid), credential=cred)
    info = await client.get_info()
    return cred, client, info


def _pick_page(
    info: dict, page: int
) -> tuple[dict[str, Any] | None, str | None]:
    """从视频信息里选出第 page 个分P，返回 (上下文, 错误 JSON)。"""
    pages = info.get("pages") or []
    if not pages:
        cid = int(info.get("cid") or 0)
        if not cid:
            return None, _json({"error": "无法获取cid"})
        return (
            {
                "cid": cid,
                "duration": info.get("duration") or 0,
                "title": info.get("title") or "",
                "page": 1,
                "pages": 1,
            },
            None,
        )

    number = max(1, int(page or 1))
    if number > len(pages):
        return None, _json(
            {"error": f"page 超出范围：这个视频只有 {len(pages)}P"}
        )
    current = pages[number - 1]
    cid = int(current.get("cid") or 0)
    if not cid:
        return None, _json({"error": "无法获取cid"})
    title = info.get("title") or ""
    if len(pages) > 1:
        title = f"{title}（P{number}）"
    return (
        {
            "cid": cid,
            "duration": current.get("duration") or info.get("duration") or 0,
            "title": title,
            "page": number,
            "pages": len(pages),
        },
        None,
    )


def _comment_card(row: dict) -> dict[str, Any]:
    """把一条一级评论整理成对外格式。"""
    content = row.get("content") or {}
    card: dict[str, Any] = {
        "rpid": row.get("rpid", 0),
        "user": (row.get("member") or {}).get("uname", ""),
        "content": content.get("message", "") if isinstance(content, dict) else "",
        "like": row.get("like", 0),
        "reply_count": row.get("rcount", 0),
        "time": row.get("ctime", 0),
    }
    pictures = _image_urls(content)
    if pictures:
        card["pictures"] = pictures

    children: list[dict[str, Any]] = []
    for child in (row.get("replies") or [])[:2]:
        child_content = child.get("content") or {}
        item: dict[str, Any] = {
            "user": (child.get("member") or {}).get("uname", ""),
            "content": (
                child_content.get("message", "")
                if isinstance(child_content, dict)
                else ""
            ),
            "like": child.get("like", 0),
        }
        child_pictures = _image_urls(child_content)
        if child_pictures:
            item["pictures"] = child_pictures
        children.append(item)
    if children:
        card["top_replies"] = children
    return card


def _search_order(value: str):
    table = {
        "totalrank": search.OrderVideo.TOTALRANK,
        "click": search.OrderVideo.CLICK,
        "pubdate": search.OrderVideo.PUBDATE,
        "dm": search.OrderVideo.DM,
    }
    return table.get(value, search.OrderVideo.TOTALRANK)


def _tag_list(tags: str) -> list[str]:
    parsed = [part.strip() for part in (tags or "").split(",") if part.strip()]
    return parsed or ["视频"]


def _upload_cover(cover: Path | None, first_video: str) -> Picture:
    if cover is not None:
        return Picture.from_file(str(cover))
    return _extract_cover_from_video(first_video)


def _extract_comment_rpid(result: Any) -> int | None:
    """从发送评论的返回值里尽量找出新评论 rpid。"""
    if not isinstance(result, dict):
        return None
    queue: list[dict] = [result]
    while queue:
        node = queue.pop(0)
        for key in ("rpid", "rpid_str"):
            raw = node.get(key)
            if raw in (None, "", 0, "0"):
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        for key in ("data", "reply", "result"):
            child = node.get(key)
            if isinstance(child, dict):
                queue.append(child)
    return None


# ========== 登录与凭证 ==========


def _qr_png(url: str) -> bytes:
    buffer = io.BytesIO()
    qrcode.make(url, box_size=10, border=4).save(buffer, format="PNG")
    return buffer.getvalue()


async def _close_login() -> None:
    global _login_session
    if _login_session is not None:
        await _login_session.close()
        _login_session = None


async def _credential_ok(cred) -> bool:
    try:
        return bool(await cred.check_valid())
    except Exception:
        return False


@mcp.tool()
async def bili_login() -> str:
    """生成 B 站登录二维码，返回可直接展示的 base64 PNG。

    已登录且凭证有效时不会重复发二维码；用户扫码后再调用
    ``bili_login_check`` 查询结果。

    Returns:
        JSON：status、二维码图片/文件路径、二维码原始链接和下一步提示
    """
    global _login_session

    cred = load_credential()
    if cred:
        valid = await _credential_ok(cred)
        if not valid:
            cred = await _credentials.maybe_refresh(cred, force=True)
            valid = await _credential_ok(cred)
        if valid:
            return _json(
                {
                    "status": "already_logged_in",
                    "message": "已登录B站，无需重复登录",
                }
            )

    # 上一次没扫完的二维码先作废，避免 qrcode_key 串到新会话。
    await _close_login()

    session_obj = QrLogin()
    state = await session_obj.generate()
    if state.status != "qrcode_ready":
        await session_obj.close()
        return _json(
            {
                "status": "error",
                "message": state.message or "生成二维码失败",
            }
        )

    _login_session = session_obj
    png = _qr_png(state.url)
    qr_file = ROOT / "qrcode_login.png"
    qr_file.write_bytes(png)

    return _json(
        {
            "status": "qrcode_ready",
            "message": "请用B站App扫描二维码登录（180秒内有效）",
            "qrcode_image": "data:image/png;base64,"
            + base64.b64encode(png).decode("ascii"),
            "qrcode_file": str(qr_file),
            "qrcode_url": state.url,
            "next_step": "用户扫码后，请调用 bili_login_check 检查登录状态",
        }
    )


@mcp.tool()
async def bili_login_check() -> str:
    """检查扫码登录进度。

    返回 waiting/scanning 时等待几秒再查；done 表示凭证已保存；timeout
    或 error 时重新调用 ``bili_login`` 生成新二维码。

    Returns:
        JSON：当前登录状态和下一步提示
    """
    global _login_session

    if _login_session is None:
        if load_credential():
            return _json(
                {"status": "already_logged_in", "message": "已登录B站"}
            )
        return _json(
            {
                "status": "no_session",
                "message": "没有进行中的登录，请先调用 bili_login 生成二维码",
            }
        )

    state = await _login_session.poll()
    qr_file = ROOT / "qrcode_login.png"

    async def reset() -> None:
        await _close_login()
        qr_file.unlink(missing_ok=True)

    if state.status == "waiting":
        return _json(
            {
                "status": "waiting",
                "message": "等待扫码，请用B站App扫描二维码",
                "next_step": "扫码后请再次调用 bili_login_check",
            }
        )
    if state.status == "confirming":
        return _json(
            {
                "status": "scanning",
                "message": "已扫码，等待用户在手机上确认...",
                "next_step": "请等待3秒后再次调用 bili_login_check",
            }
        )
    if state.status == "timeout":
        await reset()
        return _json(
            {
                "status": "timeout",
                "message": "二维码已过期，请重新调用 bili_login 生成新二维码",
            }
        )
    if state.status == "error":
        await reset()
        return _json(
            {
                "status": "error",
                "message": state.message or "登录失败",
                "cookies_seen": state.cookies_seen,
                "next_step": "请重新调用 bili_login",
            }
        )
    if state.status == "done":
        saved = save_credential(state.credential or {}, CRED_FILE)
        await reset()
        return _json(
            {
                "status": "done",
                "message": "登录成功！凭证已保存，现在可以使用所有B站功能了",
                "credential_file": str(saved),
            }
        )
    return _json(
        {"status": "unknown", "message": f"未知状态: {state.status}"}
    )


@mcp.tool()
async def bili_check_credential() -> str:
    """检查当前凭证是否仍然有效。

    使用需要登录态的 ``get_self_info`` 验证，避免公开接口在 cookie 失效时
    仍然返回数据、造成误判。

    Returns:
        JSON：logged_in、uid、用户名和说明
    """
    cred = load_credential()
    if not cred:
        return _json(
            {
                "logged_in": False,
                "message": "未登录，请调用 bili_login 进行扫码登录",
            }
        )

    cred = await _credentials.maybe_refresh(cred)
    try:
        info = await user.get_self_info(cred)
    except Exception as exc:
        return _json(
            {
                "logged_in": False,
                "message": f"凭证可能已过期: {exc}，请重新调用 bili_login 登录",
            }
        )

    return _json(
        {
            "logged_in": True,
            "uid": str(info.get("mid") or cred.dedeuserid),
            "username": info.get("uname") or info.get("name", ""),
            "message": "凭证有效",
        }
    )


# ========== 浏览与采集 ==========


@mcp.tool()
async def bili_search(
    keyword: str, num: int = 10, order: str = "totalrank"
) -> str:
    """搜索 B 站视频。

    Args:
        keyword: 搜索词
        num: 最多返回多少条，默认 10
        order: totalrank=综合、click=播放、pubdate=最新、dm=弹幕

    Returns:
        JSON：keyword、count 和视频列表
    """
    result = await search.search_by_type(
        keyword=keyword,
        search_type=search.SearchObjectType.VIDEO,
        page=1,
        order_type=_search_order(order),
    )

    videos: list[dict[str, Any]] = []
    for row in result.get("result", [])[:num]:
        title = (
            row.get("title", "")
            .replace('<em class="keyword">', "")
            .replace("</em>", "")
        )
        videos.append(
            {
                "bvid": row.get("bvid", ""),
                "aid": row.get("aid", 0),
                "title": title,
                "author": row.get("author", ""),
                "play": row.get("play", 0),
                "review": row.get("review", 0),
                "danmaku": row.get("video_review", 0),
                "duration": row.get("duration", ""),
                "description": row.get("description", "")[:200],
            }
        )

    return _json({"keyword": keyword, "count": len(videos), "videos": videos})


@mcp.tool()
async def bili_comments(bvid: str, num: int = 30) -> str:
    """获取视频热门评论，包含楼中楼和图片地址。

    Args:
        bvid: 视频 BV 号
        num: 最多返回多少条一级评论，默认 30

    Returns:
        JSON：bvid、count 和 comments
    """
    cred, _client, info = await _open_video(bvid, require_login=False)
    oid = info["aid"]

    collected: list[dict[str, Any]] = []
    page = 1
    while len(collected) < num:
        try:
            response = await comment.get_comments(
                oid=oid,
                type_=comment.CommentResourceType.VIDEO,
                page_index=page,
                order=comment.OrderType.LIKE,
                credential=cred,
            )
            batch = response.get("replies") or []
            if not batch:
                break
            for row in batch:
                collected.append(_comment_card(row))
            page += 1
            await asyncio.sleep(0.5)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if not collected:
                # 第一页就失败时不能返回 count=0：凭证失效、-412 风控和
                # 网络故障都会走到这里，静默返回空评论会被误读成“没人评论”。
                return _json({"bvid": bvid, "error": f"获取评论失败：{detail}"})
            return _json(
                {
                    "bvid": bvid,
                    "count": len(collected[:num]),
                    "comments": collected[:num],
                    "partial": True,
                    "error": f"评论只取到第 {page - 1} 页：{detail}",
                }
            )

    selected = collected[:num]
    return _json({"bvid": bvid, "count": len(selected), "comments": selected})


@mcp.tool()
async def bili_subtitle(bvid: str) -> str:
    """获取视频字幕（优先中文 AI 字幕）。

    字幕接口需要登录；未登录时返回 ``login_required: true``，不会抛错。

    Args:
        bvid: 视频 BV 号

    Returns:
        JSON：title、language、segments 和拼接后的 text
    """
    _cred, client, info = await _open_video(bvid, require_login=False)
    cid = info.get("cid", 0)
    if not cid and info.get("pages"):
        cid = (info["pages"][0] or {}).get("cid", 0)
    if not cid:
        return _json({"error": "无法获取cid"})

    try:
        listing = await client.get_subtitle(cid=cid)
    except CredentialNoSessdataException:
        # 字幕是唯一强制要求 sessdata 的公开视频接口；未登录时返回结构化
        # 提示而不是让整个工具报错，方便 Agent 向用户解释。
        return _json(
            {
                "bvid": bvid,
                "title": info.get("title", ""),
                "login_required": True,
                "message": "读取字幕需要先登录 B 站。请用户明确要求后，再调用 bili_login 扫码。",
            }
        )
    subtitles = listing.get("subtitles", [])
    if not subtitles:
        return _json({"message": "该视频没有字幕"})

    chosen = next(
        (item for item in subtitles if item.get("lan") in ("ai-zh", "zh-CN", "zh")),
        subtitles[0],
    )

    import aiohttp

    url = chosen.get("subtitle_url", "")
    if url.startswith("//"):
        url = "https:" + url
    async with aiohttp.ClientSession() as web:
        async with web.get(url) as response:
            payload = await response.json()

    lines = [item.get("content", "") for item in payload.get("body", [])]
    return _json(
        {
            "bvid": bvid,
            "title": info.get("title", ""),
            "language": chosen.get("lan_doc", ""),
            "segments": len(lines),
            "text": "\n".join(lines),
        }
    )


@mcp.tool()
async def bili_danmaku(bvid: str, num: int = 100) -> str:
    """获取视频弹幕。

    Args:
        bvid: 视频 BV 号
        num: 最多返回多少条，默认 100

    Returns:
        JSON：bvid、count 和 danmakus
    """
    _cred, client, _info = await _open_video(bvid, require_login=False)
    items = await client.get_danmakus(page_index=0)
    picked = [
        {"text": item.text, "time": item.dm_time}
        for item in items[:num]
    ]
    return _json({"bvid": bvid, "count": len(picked), "danmakus": picked})


@mcp.tool()
async def bili_video_info(bvid: str = "", aid: int = 0) -> str:
    """获取视频详情和统计数据，支持用 avid 反查 BV 号。

    Args:
        bvid: 视频 BV 号
        aid: 视频 av 号（纯数字）；和 bvid 二选一，不知道 BV 号时用它反查

    Returns:
        JSON：标题、简介、UP 主、时长、分P、标签和播放/点赞等统计
    """
    if not bvid and not aid:
        return _json({"error": "请提供 bvid 或 aid"})
    _cred, client, info = await _open_video(
        bvid, aid=aid, require_login=False
    )
    stat = info.get("stat") or {}
    # get_info() 不返回标签，需要单独请求；失败时保持空列表，不影响详情。
    tag_rows = info.get("tag")
    if not isinstance(tag_rows, list) or not tag_rows:
        try:
            tag_rows = await client.get_tags()
        except Exception:
            tag_rows = []
    if not isinstance(tag_rows, list):
        tag_rows = []
    tags = [
        row.get("tag_name")
        for row in tag_rows
        if isinstance(row, dict) and row.get("tag_name")
    ]
    return _json(
        {
            "bvid": info.get("bvid"),
            "aid": info.get("aid"),
            "title": info.get("title"),
            "pic": info.get("pic") or "",
            "description": info.get("desc"),
            "author": (info.get("owner") or {}).get("name"),
            "duration": info.get("duration"),
            "pages": len(info.get("pages", [])),
            "tags": tags,
            "stat": {
                "view": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply": stat.get("reply", 0),
                "favorite": stat.get("favorite", 0),
                "coin": stat.get("coin", 0),
                "like": stat.get("like", 0),
                "share": stat.get("share", 0),
            },
        }
    )


# ========== 视频画面 ==========

DEFAULT_MAX_VIDEO_MINUTES = bili_config.get_int(
    "BILI_MAX_VIDEO_MINUTES", 60, minimum=1, maximum=600
)


def _max_video_minutes() -> int:
    """读取当前允许的最长视频分钟数；环境变量优先于配置文件。"""
    return bili_config.get_int(
        "BILI_MAX_VIDEO_MINUTES",
        DEFAULT_MAX_VIDEO_MINUTES,
        minimum=1,
        maximum=600,
    )


def _video_duration_limit_error(duration: float) -> str | None:
    """超过上限时返回错误 JSON，否则返回 None。"""
    cap = _max_video_minutes()
    seconds = float(duration or 0)
    if seconds <= cap * 60:
        return None
    return _json(
        {
            "error": (
                f"原视频约 {round(seconds / 60)} 分钟，超过超长视频上限 "
                f"{cap} 分钟，已拒绝"
            ),
            "duration": int(seconds),
            "max_duration_minutes": cap,
        }
    )


@mcp.tool()
async def bili_video_overview(
    bvid: str,
    page: int = 1,
    max_frames: int = 40,
    start: float | None = None,
    end: float | None = None,
) -> str:
    """获取视频概览：字幕加上一张按时间排好的密集联系表。

    默认把整段视频压成约每 3 秒一帧的图片，最多 40 帧。需要看细节时
    用 start/end 指定一个两分钟左右的窗口重新抽图；窗口模式下字幕不再
    重复返回。没有 PyAV 时退回 B 站雪碧图。超过
    ``BILI_MAX_VIDEO_MINUTES``（默认 60 分钟）的视频直接拒绝；20 分钟
    以上的视频建议先征得主人同意。

    Args:
        bvid: 视频 BV 号
        page: 分P页码，从 1 开始
        max_frames: 联系表最多放几帧，默认 40
        start: 窗口起始秒；只给 end 时自动取它之前的窗口
        end: 窗口结束秒；只给 start 时默认两分钟

    Returns:
        JSON：视频信息、字幕、联系表、时间戳、窗口信息和 cleanup_id
    """
    _cred, client, info = await _open_video(bvid, require_login=False)
    context, error = _pick_page(info, page)
    if error:
        return error
    cid = context["cid"]
    duration = int(context["duration"] or 0)
    title = context["title"]

    limit_error = _video_duration_limit_error(duration)
    if limit_error:
        return limit_error

    windowed = start is not None or end is not None
    if windowed:
        if duration <= 0:
            return _json({"error": "无法获取视频时长，不能按时间窗口查看"})
        if start is not None and float(start) >= duration:
            return _json({"error": f"start 超出视频时长（{duration} 秒）"})
        if end is not None and float(end) <= 0:
            return _json({"error": "end 必须大于 0"})

    try:
        begin, finish = resolve_window(duration, start, end)
    except ValueError as exc:
        return _json({"error": str(exc)})

    if windowed:
        subtitle = {
            "skipped": True,
            "reason": "按时间窗口查看，字幕沿用第一次整段概览",
        }
    else:
        subtitle = await fetch_subtitle(client, cid)

    storyboard = None
    dense_failure = ""
    if has_pyav() and duration > 0:
        try:
            playurl = await client.get_download_url(cid=cid)
            storyboard = await fetch_dense_storyboard(
                bvid,
                cid,
                duration,
                playurl,
                max_frames=max_frames,
                start=begin,
                end=finish,
            )
        except Exception as exc:
            dense_failure = type(exc).__name__

    if storyboard is None:
        try:
            storyboard = await fetch_storyboard(
                bvid,
                cid,
                max_frames=min(max(1, int(max_frames or 1)), 24),
                duration=duration,
                start=begin,
                end=finish,
            )
            if dense_failure:
                storyboard["fallback_reason"] = dense_failure
        except Exception as exc:
            storyboard = {"error": f"{type(exc).__name__}: {exc}"}

    window = {
        "mode": "window" if windowed else "whole",
        "start": begin,
        "end": finish,
        "seconds": round(finish - begin, 2),
        "max_seconds": int(WINDOW_SECONDS),
        "total_windows": (
            max(1, math.ceil(duration / WINDOW_SECONDS)) if duration > 0 else 0
        ),
        "next_start": (
            finish if windowed and duration > 0 and finish < duration else None
        ),
    }

    return _json(
        {
            "bvid": bvid,
            "title": title,
            "author": (info.get("owner") or {}).get("name") or "",
            "duration": duration,
            "page": context["page"],
            "pages": context["pages"],
            "cid": cid,
            "window": window,
            "subtitle": subtitle,
            "storyboard": storyboard,
        }
    )


@mcp.tool()
async def bili_video_frames(
    bvid: str,
    times: list[float],
    page: int = 1,
    max_frames: int = 3,
    max_height: int = 1080,
) -> str:
    """按时间点抽取 1-3 张高清画面。

    只在联系表看不清小字、代码、界面或商品参数时使用；工具会远程 seek
    到指定秒数并抽取 720p/1080p 帧，不保存原视频。超过
    ``BILI_MAX_VIDEO_MINUTES`` 的视频直接拒绝；20 分钟以上建议先征求
    主人同意。

    Args:
        bvid: 视频 BV 号
        times: 时间点（秒），最多 3 个
        page: 分P页码，从 1 开始
        max_frames: 最多抽几张，上限 3
        max_height: 最高分辨率，默认 1080，可选 720/480

    Returns:
        JSON：每帧时间、本地路径、宽高和 cleanup_id
    """
    _cred, client, info = await _open_video(bvid, require_login=False)
    context, error = _pick_page(info, page)
    if error:
        return error
    cid = context["cid"]
    duration = float(context["duration"] or 0)

    limit_error = _video_duration_limit_error(duration)
    if limit_error:
        return limit_error

    try:
        playurl = await client.get_download_url(cid=cid)
        result = await fetch_frames(
            bvid,
            cid,
            duration,
            times,
            playurl,
            max_frames=max_frames,
            max_height=max_height,
        )
    except Exception as exc:
        return _json({"error": f"{type(exc).__name__}: {exc}"})
    return _json(result)


# ========== 互动：评论与采集 ==========


@mcp.tool()
async def bili_reply(
    bvid: str,
    text: str,
    rpid: int = 0,
    root: int = 0,
    image: str = "",
) -> str:
    """发表评论，或回复某条评论。

    Args:
        bvid: 视频 BV 号
        text: 评论正文
        rpid: 要回复的评论 ID；0 表示发新评论
        root: 楼层根评论 ID；回复楼中楼时填写
        image: 可选配图，只在发新评论（rpid=0）时可用。只接受本地表情包
            或绘画产物，也可以直接写表情名（如“骄傲”）

    Returns:
        JSON：success、新评论 rpid、root/parent 和图片路径
    """
    image = (image or "").strip()
    if image and rpid != 0:
        return _json(
            {
                "success": False,
                "error": (
                    "回复别人的评论不能带图；只有自己发新评论（rpid=0）时"
                    "才能带一张表情包或配图"
                ),
            }
        )

    image_path: Path | None = None
    if image:
        image_path, image_error = _outbound_image(image, 5, "评论传不上去")
        if image_error:
            return _json({"success": False, "error": image_error})

    try:
        cred, client, info = await _open_video(bvid)
    except NotLoggedInError:
        return _json(
            {"success": False, "login_required": True, "error": LOGIN_HINT}
        )
    aid = info["aid"]

    owner = info.get("owner") or {}
    blocked = bili_avoid.reply_blocked(
        rpid=rpid,
        root=root,
        bvid=bvid,
        up_uid=owner.get("mid"),
        up_name=str(owner.get("name") or ""),
    )
    if blocked:
        return _json({"success": False, "blocked": True, "error": blocked})

    try:
        if rpid == 0:
            picture = Picture.from_file(str(image_path)) if image_path else None
            result = await comment.send_comment(
                text=text,
                oid=aid,
                type_=comment.CommentResourceType.VIDEO,
                credential=cred,
                pic=picture,
            )
        else:
            reply_root = root if root != 0 else rpid
            result = await comment.send_comment(
                text=text,
                oid=aid,
                type_=comment.CommentResourceType.VIDEO,
                root=reply_root,
                parent=rpid,
                credential=cred,
            )
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})

    new_rpid = _extract_comment_rpid(result)
    if rpid == 0:
        actual_root = new_rpid
        actual_parent = new_rpid
    else:
        actual_root = root if root != 0 else rpid
        actual_parent = rpid

    payload: dict[str, Any] = {
        "success": True,
        "message": "评论发送成功",
        "bvid": bvid,
        "oid": aid,
        "rpid": new_rpid,
        "root": actual_root,
        "parent": actual_parent,
        "target_rpid": rpid,
    }
    if new_rpid is None:
        payload["warning"] = "接口未返回新评论 rpid，评论账本无法记录"
    if image_path is not None:
        payload["image"] = str(image_path)
    return _json(payload)


@mcp.tool()
async def bili_crawl(
    keyword: str,
    max_videos: int = 5,
    comments_per_video: int = 20,
    get_subtitles: bool = True,
) -> str:
    """按关键词批量采集视频、评论和字幕。

    Args:
        keyword: 搜索关键词
        max_videos: 最多采集几个视频，默认 5
        comments_per_video: 每个视频取多少条评论，默认 20
        get_subtitles: 是否同时抓字幕，默认 True

    Returns:
        JSON：keyword、视频数、评论总数和每个视频的采集结果
    """
    found = await search.search_by_type(
        keyword=keyword,
        search_type=search.SearchObjectType.VIDEO,
        page=1,
        order_type=search.OrderVideo.TOTALRANK,
    )

    collected: list[dict[str, Any]] = []
    for row in found.get("result", [])[:max_videos]:
        bvid = row.get("bvid", "")
        if not bvid:
            continue

        title = (
            row.get("title", "")
            .replace('<em class="keyword">', "")
            .replace("</em>", "")
        )
        brief = {
            "bvid": bvid,
            "title": title,
            "author": row.get("author", ""),
            "play": row.get("play", 0),
            "review": row.get("review", 0),
        }

        comment_payload = json.loads(
            await bili_comments(bvid=bvid, num=comments_per_video)
        )

        subtitle_text = ""
        subtitle_error = ""
        if get_subtitles:
            try:
                subtitle_payload = json.loads(await bili_subtitle(bvid=bvid))
                subtitle_text = subtitle_payload.get("text", "")
                if subtitle_payload.get("login_required"):
                    subtitle_error = subtitle_payload.get(
                        "message", "未登录，无法抓取字幕"
                    )
            except Exception as exc:
                subtitle_text = ""
                subtitle_error = f"{type(exc).__name__}: {exc}"

        entry = {
            "video": brief,
            "comments": comment_payload.get("comments", []),
            "subtitle_text": subtitle_text,
        }
        if comment_payload.get("error"):
            entry["comments_error"] = comment_payload["error"]
        if subtitle_error:
            entry["subtitle_error"] = subtitle_error
        collected.append(entry)
        await asyncio.sleep(1)

    return _json(
        {
            "keyword": keyword,
            "video_count": len(collected),
            "total_comments": sum(len(item["comments"]) for item in collected),
            "results": collected,
        }
    )


# ========== 封面与本地素材 ==========


def _video_still(video_file: str, second: int) -> Picture | None:
    """用 ffmpeg 在指定秒数截一张图；失败时返回 None。"""
    handle, temp_name = tempfile.mkstemp(prefix="bili_still_", suffix=".png")
    os.close(handle)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(second),
                "-i",
                video_file,
                "-vframes",
                "1",
                "-q:v",
                "2",
                temp_name,
            ],
            capture_output=True,
            timeout=30,
        )
        if os.path.isfile(temp_name) and os.path.getsize(temp_name) > 0:
            return Picture.from_file(temp_name)
    except Exception:
        return None
    finally:
        try:
            os.remove(temp_name)
        except OSError:
            pass
    return None


def _extract_cover_from_video(video_file: str) -> Picture:
    """先从第 3 秒截封面，失败再试第 0 秒。"""
    for second in (3, 0):
        picture = _video_still(video_file, second)
        if picture is not None:
            return picture
    raise RuntimeError(f"无法从视频截取封面: {video_file}")


@mcp.tool()
async def bili_self_image() -> str:
    """查看自己的形象立绘和文字描述。

    图片放在本地，只有调用时才进入上下文。返回的 path 可以直接给支持
    本地图片的客户端展示。

    Returns:
        JSON：success、path 和 description
    """
    local = bili_art.self_image_path()
    description = bili_art.self_description()
    if local is None and not description:
        return _json(
            {
                "success": False,
                "error": "本地还没有形象立绘或描述：把图片放到 data/self/character.png"
                "（或 .jpg/.jpeg/.webp），或写一份 data/self/character.md。",
            }
        )

    payload: dict[str, Any] = {"success": True, "source": "local"}
    if local is not None:
        payload["path"] = str(bili_art.self_image_view(local))
    if description:
        payload["description"] = description
    return _json(payload)


IMAGE_MOOD_PROMPTS = {
    "happy": "明亮、温暖、开心",
    "calm": "安静、柔和、放松",
    "tired": "有点困倦、慵懒",
    "excited": "兴奋、有活力",
}


@mcp.tool()
async def bili_generate_image(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    mood: str = "happy",
) -> str:
    """让绘画 AI 生成一张配图。

    本地配置了形象描述时，会自动拼接到提示词前面；没有配置就只按画面
    内容生成。生成的图片保存在 ``data/drawn/``，返回的 path 可以交给
    发动态或私信工具。

    Args:
        prompt: 画面内容，例如“站在窗边，朝镜头挥手”
        width: 宽度，512-1600，默认 1024
        height: 高度，512-1600，默认 1024
        mood: happy / calm / tired / excited，默认 happy

    Returns:
        JSON：success、path、实际宽高和心情；未配置绘画 AI 时返回原因
    """
    text = (prompt or "").strip()
    if not text:
        return _json({"success": False, "error": "prompt 不能为空"})

    size_w = bili_draw.clamp_size(width)
    size_h = bili_draw.clamp_size(height)
    mood_key = (mood or "happy").strip().lower()
    if mood_key not in IMAGE_MOOD_PROMPTS:
        mood_key = "happy"

    character = bili_art.self_description()
    parts = [character] if character else []
    parts.extend(
        [
            f"画面：{text}",
            f"氛围：{IMAGE_MOOD_PROMPTS[mood_key]}",
            "画风：干净的二次元插画，画面里不要出现文字。",
        ]
    )
    full_prompt = "\n".join(parts)

    try:
        path = await bili_draw.generate(full_prompt, width=size_w, height=size_h)
    except bili_draw.DrawError as exc:
        return _json({"success": False, "error": str(exc)})
    except Exception as exc:
        return _json(
            {
                "success": False,
                "error": f"图片生成失败: {type(exc).__name__}: {exc}",
            }
        )

    return _json(
        {
            "success": True,
            "path": str(path),
            "width": size_w,
            "height": size_h,
            "mood": mood_key,
        }
    )


# ========== 互动与发布 ==========


@mcp.tool()
async def bili_send_dynamic(
    text: str, images: list[str] | None = None,
    topic_id: int = 0, schedule_time: int = 0,
) -> str:
    """发布图文动态。

    Args:
        text: 正文，支持 @ 和方括号表情
        images: 图片列表，最多 9 张；只接受本地表情包或绘画产物
        topic_id: 话题 ID，0 表示不关联
        schedule_time: 定时发布的 Unix 时间戳，0 表示立即发布

    Returns:
        JSON：success、message 和接口返回的 data
    """
    if not text or not text.strip():
        return _json({"success": False, "error": "text 不能为空"})

    resolved: list[Path] = []
    try:
        for value in (images or [])[:9]:
            resolved.append(bili_images.resolve_outbound_image(value))
    except bili_images.ImageSourceError as exc:
        return _json({"success": False, "error": str(exc)})

    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    item = dynamic.BuildDynamic.empty()
    item.add_plain_text(text.strip())
    for path in resolved:
        try:
            item.add_image(Picture.from_file(str(path)))
        except Exception as exc:
            return _json(
                {
                    "success": False,
                    "error": f"图片处理失败: {path} - {exc}",
                }
            )

    if topic_id:
        item.set_topic(topic_id)
    if schedule_time > 0:
        item.set_send_time(schedule_time)

    try:
        result = await dynamic.send_dynamic(info=item, credential=cred)
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})

    return _json(
        {
            "success": True,
            "message": "动态发布成功",
            "data": result if isinstance(result, dict) else str(result),
        }
    )


@mcp.tool()
async def bili_upload_video(
    video_path: str, title: str, desc: str = "", tid: int = 124,
    tags: str = "AI", cover_path: str = "", source: str = "",
    dynamic_text: str = "", no_reprint: bool = True,
) -> str:
    """上传单P视频。

    Args:
        video_path: 本地视频路径，必须在 data/uploads/（或 BILI_UPLOAD_DIR）里
        title: 标题，最多 80 字
        desc: 简介
        tid: 分区 ID，默认 124；可用 bili_video_zones 查询
        tags: 逗号分隔的标签，至少一个
        cover_path: 封面路径，只接受本地表情包或绘画产物；不填则自动截帧
        source: 转载来源 URL；填写后按转载投稿
        dynamic_text: 投稿同时发布的动态
        no_reprint: 原创投稿时是否禁止转载，默认 True

    Returns:
        JSON：success、message 和上传接口返回的 data
    """
    if not video_path or not video_path.strip():
        return _json(
            {
                "success": False,
                "error": "video_path 不能为空，请提供视频文件的本地路径",
            }
        )
    video_path = video_path.strip()
    try:
        video_path = str(bili_uploads.resolve_upload_video(video_path))
    except bili_uploads.UploadSourceError as exc:
        return _json({"success": False, "error": str(exc)})
    if not title or not title.strip():
        return _json({"success": False, "error": "title 不能为空"})

    cover: Path | None = None
    if cover_path and cover_path.strip():
        cover, cover_error = _outbound_image(cover_path, 1e9, "")
        if cover_error:
            return _json({"success": False, "error": cover_error})

    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    phase = {"value": "初始化"}
    try:
        page = VideoUploaderPage(
            path=video_path,
            title=title.strip()[:80],
            description=(desc or "")[:250],
        )
        original = not bool(source and source.strip())
        meta = VideoMeta(
            tid=tid,
            title=title.strip()[:80],
            desc=desc or "",
            cover=_upload_cover(cover, video_path),
            tags=_tag_list(tags),
            original=original,
            source=source.strip() if source and source.strip() else None,
            no_reprint=no_reprint if original else False,
            dynamic=(
                dynamic_text.strip()
                if dynamic_text and dynamic_text.strip()
                else None
            ),
        )
        uploader = VideoUploader(pages=[page], meta=meta, credential=cred)

        @uploader.on("__ALL__")
        async def on_progress(event: dict):
            phase["value"] = str(event)

        result = await uploader.start()
    except Exception as exc:
        return _json(
            {
                "success": False,
                "error": str(exc),
                "last_phase": phase["value"],
            }
        )

    return _json(
        {
            "success": True,
            "message": "视频上传成功",
            "data": result if isinstance(result, dict) else str(result),
        }
    )


@mcp.tool()
async def bili_upload_video_multi(
    video_paths: list[str], page_titles: list[str], title: str,
    desc: str = "", tid: int = 124, tags: str = "AI",
    cover_path: str = "", source: str = "",
) -> str:
    """把多个视频作为分P上传到同一个投稿。

    Args:
        video_paths: 视频路径列表，每个都必须在 data/uploads/（或 BILI_UPLOAD_DIR）里
        page_titles: 分P标题列表，与路径一一对应
        title: 总标题
        desc: 简介
        tid: 分区 ID，默认 124
        tags: 逗号分隔的标签
        cover_path: 封面路径，只接受本地表情包或绘画产物
        source: 转载来源 URL

    Returns:
        JSON：success、message 和上传接口返回的 data
    """
    if not video_paths:
        return _json({"success": False, "error": "video_paths 不能为空"})
    if not page_titles:
        return _json({"success": False, "error": "page_titles 不能为空"})
    if len(video_paths) != len(page_titles):
        return _json(
            {
                "success": False,
                "error": "video_paths 和 page_titles 数量必须一致",
            }
        )
    if not title or not title.strip():
        return _json({"success": False, "error": "title 不能为空"})

    cover: Path | None = None
    if cover_path and cover_path.strip():
        cover, cover_error = _outbound_image(cover_path, 1e9, "")
        if cover_error:
            return _json({"success": False, "error": cover_error})

    pages: list[VideoUploaderPage] = []
    for index, raw in enumerate(video_paths):
        try:
            path = str(bili_uploads.resolve_upload_video(raw))
        except bili_uploads.UploadSourceError as exc:
            return _json({"success": False, "error": str(exc)})
        pages.append(
            VideoUploaderPage(
                path=path,
                title=(page_titles[index] or "").strip()[:80] or f"P{index + 1}",
                description="",
            )
        )

    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    original = not bool(source and source.strip())
    try:
        meta = VideoMeta(
            tid=tid,
            title=(title or "").strip()[:80],
            desc=desc or "",
            cover=_upload_cover(cover, pages[0].path),
            tags=_tag_list(tags),
            original=original,
            source=source.strip() if source and source.strip() else None,
            # 与单 P 投稿保持一致：原创默认禁止转载，转载必须允许转载。
            no_reprint=original,
        )
        uploader = VideoUploader(pages=pages, meta=meta, credential=cred)
        result = await uploader.start()
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})

    return _json(
        {
            "success": True,
            "message": f"多P视频上传成功（共{len(pages)}P）",
            "data": result if isinstance(result, dict) else str(result),
        }
    )


@mcp.tool()
async def bili_send_opus(
    title: str, content: str,
    images: list[str] | None = None,
) -> str:
    """发布带标题的图文动态（Opus）。

    注意：bilibili-api 17 没有专栏发布接口，本工具实际发布的是图文动态，
    标题会作为正文首行；不要把它当专栏文章使用。

    Args:
        title: 标题，会放在正文首行
        content: 正文，用换行分段
        images: 文中图片，最多 9 张；只接受本地表情包或绘画产物

    Returns:
        JSON：success、message 和接口返回的 data
    """
    if not title or not title.strip():
        return _json({"success": False, "error": "title 不能为空"})
    if not content or not content.strip():
        return _json({"success": False, "error": "content 不能为空"})

    resolved: list[Path] = []
    try:
        for value in (images or [])[:9]:
            resolved.append(bili_images.resolve_outbound_image(value))
    except bili_images.ImageSourceError as exc:
        return _json({"success": False, "error": str(exc)})

    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    item = dynamic.BuildDynamic.empty()
    item.add_plain_text(f"【{title.strip()}】\n\n{content.strip()}")
    for path in resolved:
        try:
            item.add_image(Picture.from_file(str(path)))
        except Exception as exc:
            return _json(
                {"success": False, "error": f"图片处理失败: {path} - {exc}"}
            )

    try:
        result = await dynamic.send_dynamic(info=item, credential=cred)
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})

    return _json(
        {
            "success": True,
            "message": "图文动态发布成功",
            "data": result if isinstance(result, dict) else str(result),
        }
    )


@mcp.tool()
async def bili_video_zones() -> str:
    """列出常用视频分区 ID，供投稿时选择 tid。

    Returns:
        JSON：按大类分组的“分区 ID → 名称”
    """
    return _json(
        {
            "科技": {
                188: "科技资讯",
                122: "野生技术协会",
                95: "数码",
                208: "科技",
                209: "手工",
            },
            "知识": {
                201: "科学",
                124: "社科·法律·心理",
                207: "财经商业",
                228: "人文历史",
                36: "科技(知识)",
            },
            "生活": {
                21: "日常",
                160: "生活记录",
                230: "其他",
                231: "美食",
                234: "健身",
                161: "搞笑",
            },
            "游戏": {
                17: "单机游戏",
                171: "电子竞技",
                172: "手机游戏",
                65: "网络游戏",
            },
            "影视": {183: "影视杂谈", 138: "搞笑", 182: "影视剪辑"},
            "动画": {32: "完结动画", 33: "连载动画", 51: "MAD·AMV"},
            "音乐": {28: "原创音乐", 31: "翻唱", 59: "演奏"},
        }
    )


# ========== 热门、排行与用户 ==========


@mcp.tool()
async def bili_hot_videos(pn: int = 1, ps: int = 20) -> str:
    """获取当前热门视频。

    Args:
        pn: 页码，默认 1
        ps: 每页数量，默认 20，最大 50

    Returns:
        JSON：page、count 和 videos
    """
    response = await hot.get_hot_videos(pn=pn, ps=min(ps, 50))
    videos: list[dict[str, Any]] = []
    for row in response.get("list", []):
        stat = row.get("stat") or {}
        videos.append(
            {
                "bvid": row.get("bvid", ""),
                "title": row.get("title", ""),
                "author": (row.get("owner") or {}).get("name", ""),
                "play": stat.get("view", 0),
                "like": stat.get("like", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply": stat.get("reply", 0),
                "desc": (row.get("desc", "") or "")[:100],
                "duration": row.get("duration", 0),
                "tname": row.get("tname", ""),
            }
        )
    return _json({"page": pn, "count": len(videos), "videos": videos})


@mcp.tool()
async def bili_hot_buzzwords(
    page_num: int = 1, page_size: int = 20
) -> str:
    """获取热搜词。

    Args:
        page_num: 页码，默认 1
        page_size: 每页数量，默认 20

    Returns:
        热搜接口的原始 JSON 数据
    """
    response = await hot.get_hot_buzzwords(
        page_num=page_num, page_size=page_size
    )
    return _json(response)


@mcp.tool()
async def bili_weekly_hot(week: int = 0) -> str:
    """获取每周必看。

    Args:
        week: 0 返回期数列表，大于 0 返回该期视频

    Returns:
        期数列表或指定期的视频数据
    """
    if week <= 0:
        response = await hot.get_weekly_hot_videos_list()
    else:
        response = await hot.get_weekly_hot_videos(week=week)
    return _json(response)


@mcp.tool()
async def bili_rank(category: str = "all", day: int = 3) -> str:
    """获取分区排行榜。

    Args:
        category: all/original/rookie/douga/music/dance/game/knowledge/
            technology/sports/car/life/food/animal/fashion/ent/cinephile
        day: 3=三日榜，7=七日榜

    Returns:
        JSON：category、day、count 和 videos
    """
    kind = {
        "all": rank.RankType.All,
        "original": rank.RankType.Original,
        "rookie": rank.RankType.Rookie,
        "douga": rank.RankType.Douga,
        "music": rank.RankType.Music,
        "dance": rank.RankType.Dance,
        "game": rank.RankType.Game,
        "knowledge": rank.RankType.Knowledge,
        "technology": rank.RankType.Technology,
        "sports": rank.RankType.Sports,
        "car": rank.RankType.Car,
        "life": rank.RankType.Life,
        "food": rank.RankType.Food,
        "animal": rank.RankType.Animal,
        "fashion": rank.RankType.Fashion,
        "ent": rank.RankType.Ent,
        "cinephile": rank.RankType.Cinephile,
    }
    period = {
        3: rank.RankDayType.THREE_DAY,
        7: rank.RankDayType.WEEK,
    }

    response = await rank.get_rank(
        type_=kind.get((category or "all").lower(), rank.RankType.All),
        day=period.get(day, rank.RankDayType.THREE_DAY),
    )
    videos: list[dict[str, Any]] = []
    for row in response.get("list", []):
        stat = row.get("stat") or {}
        videos.append(
            {
                "bvid": row.get("bvid", ""),
                "title": row.get("title", ""),
                "author": (row.get("owner") or {}).get("name", ""),
                "play": stat.get("view", 0),
                "like": stat.get("like", 0),
                "coin": stat.get("coin", 0),
                "score": row.get("score", 0),
                "tname": row.get("tname", ""),
            }
        )
    return _json(
        {
            "category": category,
            "day": day,
            "count": len(videos),
            "videos": videos,
        }
    )


@mcp.tool()
async def bili_user_info(uid: int) -> str:
    """获取用户资料、粉丝数和 UP 主累计数据。

    未登录时累计数据接口不可用，``likes`` / ``archive_view`` /
    ``article_view`` 返回 null，并带 ``stats_unavailable: true``。

    Args:
        uid: 用户 UID

    Returns:
        JSON：昵称、签名、等级、头像、粉丝/关注数和播放量
    """
    cred = await get_cred_optional()
    account = user.User(uid=uid, credential=cred)
    info = await account.get_user_info()

    up_stat: dict[str, Any] = {}
    stats_available = True
    stats_error = ""
    try:
        up_stat = await account.get_up_stat()
    except Exception as exc:
        stats_available = False
        stats_error = f"{type(exc).__name__}: {exc}"

    relation: dict[str, Any] = {}
    try:
        relation = await account.get_relation_info()
    except Exception:
        relation = {}

    payload: dict[str, Any] = {
        "uid": uid,
        "name": info.get("name", ""),
        "sign": info.get("sign", ""),
        "level": info.get("level", 0),
        "face": info.get("face", ""),
        "fans": relation.get("follower", info.get("follower", 0)),
        "following": relation.get("following", info.get("following", 0)),
        "is_senior_member": info.get("is_senior_member", 0),
        "top_photo": info.get("top_photo", ""),
    }
    if stats_available:
        payload["likes"] = up_stat.get("likes", 0)
        payload["archive_view"] = (up_stat.get("archive") or {}).get("view", 0)
        payload["article_view"] = (up_stat.get("article") or {}).get("view", 0)
    else:
        # 不要用 0 冒充“没有数据”：未登录时这就是不可用。
        payload["likes"] = None
        payload["archive_view"] = None
        payload["article_view"] = None
        payload["stats_unavailable"] = True
        payload["stats_error"] = stats_error
    return _json(payload)


@mcp.tool()
async def bili_user_videos(
    uid: int,
    pn: int = 1,
    ps: int = 30,
    order: str = "pubdate",
    keyword: str = "",
) -> str:
    """获取用户投稿列表。

    Args:
        uid: 用户 UID
        pn: 页码，默认 1
        ps: 每页数量，默认 30
        order: pubdate=最新、click=播放、stow=收藏
        keyword: 在该用户投稿里搜索

    Returns:
        JSON：uid、page、total、count 和 videos
    """
    cred = await get_cred_optional()
    account = user.User(uid=uid, credential=cred)
    order_map = {
        "pubdate": user.VideoOrder.PUBDATE,
        "click": user.VideoOrder.VIEW,
        "stow": user.VideoOrder.FAVORITE,
    }
    response = await account.get_videos(
        pn=pn,
        ps=ps,
        order=order_map.get(order, user.VideoOrder.PUBDATE),
        keyword=keyword,
    )

    listing = response.get("list") or {}
    rows = listing.get("vlist", []) if isinstance(listing, dict) else []
    videos = [
        {
            "bvid": row.get("bvid", ""),
            "title": row.get("title", ""),
            "play": row.get("play", 0),
            "comment": row.get("comment", 0),
            "created": row.get("created", 0),
            "length": row.get("length", ""),
            "description": (row.get("description", "") or "")[:100],
        }
        for row in rows
    ]
    return _json(
        {
            "uid": uid,
            "page": pn,
            "total": (response.get("page") or {}).get("count", 0),
            "count": len(videos),
            "videos": videos,
        }
    )


@mcp.tool()
async def bili_space_dynamics(uid: int, limit: int = 5) -> str:
    """获取用户最近的图文、转发和视频动态。

    Args:
        uid: 用户 UID
        limit: 最多返回几条，默认 5，最大 20

    Returns:
        JSON：success、uid、count 和 dynamics
    """
    cred = await get_cred_optional()
    try:
        items = await fetch_dynamics(
            cred, uid, limit=max(1, min(20, int(limit)))
        )
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})
    return _json(
        {
            "success": True,
            "uid": uid,
            "count": len(items),
            "dynamics": items,
        }
    )


# ========== 收藏夹 ==========


@mcp.tool()
async def bili_favorite_lists(uid: int = 0) -> str:
    """获取收藏夹列表。

    Args:
        uid: 用户 UID；0 表示当前登录账号

    Returns:
        JSON：uid、count 和 lists
    """
    owner = int(uid or 0)
    cred = await get_cred_optional()
    if owner == 0:
        # uid=0 表示“当前账号”：直接从凭证取 UID，未登录则结构化提示。
        owner = int(getattr(cred, "dedeuserid", 0) or 0)
        if owner == 0:
            return _json(
                {
                    "uid": 0,
                    "success": False,
                    "login_required": True,
                    "error": LOGIN_HINT,
                }
            )

    response = await favorite_list.get_video_favorite_list(
        uid=owner, credential=cred
    )
    if response is None:
        # 未登录时 B 站对 list-all 返回 code=0 但 data=null：不报错，
        # 也拿不到任何收藏夹。区分“未登录”和“对方没公开”。
        logged_in = bool(getattr(cred, "sessdata", None))
        return _json(
            {
                "uid": owner,
                "success": False,
                "login_required": not logged_in,
                "error": (
                    "未登录时 B 站不返回收藏夹列表，请登录后重试。"
                    if not logged_in
                    else "B 站未返回收藏夹列表，对方可能没有公开收藏夹。"
                ),
            }
        )
    lists = [
        {
            "id": row.get("id", 0),
            "title": row.get("title", ""),
            "media_count": row.get("media_count", 0),
            "fav_state": row.get("fav_state", 0),
        }
        for row in (response.get("list") or [])
    ]
    return _json({"uid": owner, "count": len(lists), "lists": lists})


@mcp.tool()
async def bili_favorite_content(
    media_id: int, page: int = 1, keyword: str = ""
) -> str:
    """获取收藏夹内的视频。

    Args:
        media_id: 收藏夹 ID，从 bili_favorite_lists 获取
        page: 页码，默认 1
        keyword: 收藏夹内搜索词

    Returns:
        JSON：media_id、page、has_more、count 和 medias
    """
    cred = await get_cred_optional()
    response = await favorite_list.get_video_favorite_list_content(
        media_id=media_id,
        page=page,
        keyword=keyword if keyword else None,
        credential=cred,
    )
    if response is None:
        return _json(
            {
                "media_id": media_id,
                "error": "收藏夹不存在、不可见，或需要登录后查看",
            }
        )
    medias = [
        {
            "bvid": row.get("bvid", ""),
            "title": row.get("title", ""),
            "play": (row.get("cnt_info") or {}).get("play", 0),
            "collect": (row.get("cnt_info") or {}).get("collect", 0),
            "author": (row.get("upper") or {}).get("name", ""),
            "duration": row.get("duration", 0),
            "fav_time": row.get("fav_time", 0),
        }
        for row in (response.get("medias") or [])
    ]
    return _json(
        {
            "media_id": media_id,
            "page": page,
            "has_more": response.get("has_more", False),
            "count": len(medias),
            "medias": medias,
        }
    )


@mcp.tool()
async def bili_favorite(
    bvid: str, media_id: int = 0, remove: bool = False
) -> str:
    """收藏或取消收藏视频。

    Args:
        bvid: 视频 BV 号
        media_id: 收藏夹 ID；0 表示默认收藏夹
        remove: True 取消收藏，False 收藏

    Returns:
        JSON：success、message、bvid、media_id 和收藏夹名称
    """
    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    folder_id = int(media_id or 0)
    folder_name = "默认收藏夹"

    if folder_id == 0:
        uid = int(getattr(cred, "dedeuserid", 0) or 0)
        response = await favorite_list.get_video_favorite_list(
            uid=uid, credential=cred
        )
        folders = response.get("list") or []
        if not folders:
            return _json(
                {
                    "success": False,
                    "error": "账号还没有收藏夹，请先在 B 站网页端创建一个",
                }
            )
        default = next(
            (row for row in folders if row.get("title") == "默认收藏夹"),
            folders[0],
        )
        folder_id = int(default.get("id", 0))
        folder_name = default.get("title", "")

    client = video.Video(bvid=bvid, credential=cred)
    try:
        if remove:
            response = await client.set_favorite(del_media_ids=[folder_id])
        else:
            response = await client.set_favorite(add_media_ids=[folder_id])
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})

    payload: dict[str, Any] = {
        "success": True,
        "message": "已取消收藏" if remove else "收藏成功",
        "bvid": bvid,
        "media_id": folder_id,
        "folder": folder_name,
    }
    if isinstance(response, dict) and response.get("code") not in (None, 0):
        payload["api_message"] = response.get("message", "")
    return _json(payload)


# ========== 私信与表情包 ==========


@mcp.tool()
async def bili_send_message(
    receiver_uid: int, text: str = "", image: str = ""
) -> str:
    """给用户发私信，可以只发文字、只发图片，或先文字后图片。

    Args:
        receiver_uid: 接收者 UID
        text: 私信正文；image 为空时必填
        image: 图片路径，只接受本地表情包或绘画产物，也可以直接写表情名

    Returns:
        JSON：success、sent 明细和可选图片路径；部分失败时列出错误
    """
    text = (text or "").strip()
    image = (image or "").strip()
    if not text and not image:
        return _json(
            {"success": False, "error": "text 和 image 至少要有一个"}
        )

    image_path: Path | None = None
    if image:
        image_path, image_error = _outbound_image(
            image, 8, "B站私信传不上去"
        )
        if image_error:
            return _json({"success": False, "error": image_error})

    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    sent: dict[str, bool] = {}
    errors: list[str] = []

    if text:
        try:
            await session.send_msg(
                credential=cred,
                receiver_id=receiver_uid,
                msg_type=session.EventType.TEXT,
                content=text,
            )
            sent["text"] = True
        except Exception as exc:
            errors.append(f"文字：{exc}")

    if image_path is not None:
        try:
            picture = Picture.from_file(str(image_path))
            await session.send_msg(
                credential=cred,
                receiver_id=receiver_uid,
                msg_type=session.EventType.PICTURE,
                content=picture,
            )
            sent["image"] = True
        except Exception as exc:
            errors.append(f"图片：{exc}")

    if errors:
        return _json(
            {
                "success": False,
                "error": "；".join(errors),
                "sent": sent,
            }
        )

    payload: dict[str, Any] = {
        "success": True,
        "message": f"私信已发送给UID:{receiver_uid}",
        "sent": sent,
    }
    if image_path is not None:
        payload["image"] = str(image_path)
    return _json(payload)


STICKER_DIR = bili_images.STICKER_DIR
STICKER_EXTS = bili_images.IMAGE_EXTS


def _sticker_path(name: str) -> tuple[Path | None, str]:
    """按表情名查图，返回 (路径, 规范名)；只接受纯名字，不接受路径。"""
    clean = (name or "").strip().strip('"').strip("'").strip()
    if not clean:
        return None, ""
    if any(ch in clean for ch in '\\/:*?"<>|') or ".." in clean:
        return None, clean

    stem = clean
    for ext in STICKER_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    stem = stem.strip()
    if not stem or not STICKER_DIR.is_dir():
        return None, stem

    try:
        files = sorted(
            path
            for path in STICKER_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in STICKER_EXTS
        )
    except OSError:
        return None, stem

    exact = next((path for path in files if path.stem == stem), None)
    if exact is not None:
        return exact, exact.stem
    folded = stem.lower()
    for path in files:
        if path.stem.lower() == folded:
            return path, path.stem
    return None, stem


def _sticker_names() -> list[str]:
    if not STICKER_DIR.is_dir():
        return []
    return sorted(
        {
            path.stem
            for path in STICKER_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in STICKER_EXTS
        }
    )


@mcp.tool()
async def bili_sticker_list() -> str:
    """列出本地表情包的名字，不读取图片内容。

    文件名就是表情名；先看名字，再用 bili_send_sticker 发送。

    Returns:
        JSON：success、count 和 stickers
    """
    names = _sticker_names()
    return _json(
        {
            "success": True,
            "count": len(names),
            "stickers": names,
            "hint": (
                "按名字挑最贴近当前情绪的一个；没有合适的就只发文字。"
                if names
                else "表情包库还是空的：把图片放进 data/self/stickers/，文件名就是表情名。"
            ),
        }
    )


@mcp.tool()
async def bili_send_sticker(
    receiver_uid: int, sticker: str, text: str = ""
) -> str:
    """从本地表情包库挑一张表情发私信。

    Args:
        receiver_uid: 接收者 UID
        sticker: 表情名，可以带或不带扩展名
        text: 可选，附在表情前的一句话

    Returns:
        JSON：发送结果和实际使用的表情名
    """
    path, name = _sticker_path(sticker)
    if path is None:
        available = _sticker_names()[:40]
        return _json(
            {
                "success": False,
                "error": f"表情包库里没有「{name or sticker}」",
                "available": available,
                "hint": (
                    f"现有表情：{'、'.join(available)}"
                    if available
                    else "表情包库是空的，先往 data/self/stickers/ 里放图片"
                ),
            }
        )

    try:
        result = json.loads(
            await bili_send_message(
                receiver_uid=receiver_uid,
                text=text,
                image=str(path),
            )
        )
    except Exception as exc:
        return _json(
            {
                "success": False,
                "error": f"发送失败：{type(exc).__name__}: {exc}",
            }
        )

    if not isinstance(result, dict):
        result = {"success": False, "error": "发送结果异常"}
    result["sticker"] = name
    return _json(result)


# ========== 消息通知 ==========


@mcp.tool()
async def bili_unread_messages() -> str:
    """获取私信、@、回复、点赞等各类未读数。

    Returns:
        未读消息接口的 JSON 数据
    """
    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    try:
        result = await retry_network(
            lambda: session.get_unread_messages(credential=cred),
            what="获取未读消息",
        )
    except Exception as exc:
        return _json({"error": str(exc)})
    return _json(result)


@mcp.tool()
async def bili_recent_messages(
    limit: int = 5, messages_per_session: int = 8
) -> str:
    """获取未读私信会话和最近几条消息。

    Args:
        limit: 最多几个未读会话
        messages_per_session: 每个会话带几条最近消息

    Returns:
        JSON：success、count 和 sessions
    """
    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    try:
        sessions = await fetch_unread_sessions(
            cred,
            limit=max(1, min(20, int(limit))),
            per_session=max(1, min(30, int(messages_per_session))),
        )
    except Exception as exc:
        return _json({"success": False, "error": str(exc)})
    return _json(
        {"success": True, "count": len(sessions), "sessions": sessions}
    )


@mcp.tool()
async def bili_received_replies() -> str:
    """获取最近收到的评论回复通知。

    每条通知的 ``item`` 里，``subject_id`` 是视频 av 号，``uri`` 带 BV 号，
    ``root_id`` / ``source_id`` 是楼层评论 ID。只有 av 号时，把它传给
    ``bili_video_info(aid=...)`` 即可反查 BV 号。

    Returns:
        回复通知接口的 JSON 数据
    """
    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    try:
        result = await retry_network(
            lambda: session.get_replies(credential=cred),
            what="获取回复通知",
        )
    except Exception as exc:
        return _json({"error": str(exc)})
    return _json(result)


@mcp.tool()
async def bili_received_at_and_likes() -> str:
    """获取最近收到的 @ 和点赞通知。

    Returns:
        JSON：at 和 likes 两部分；单项失败时额外带 at_error/likes_error
    """
    cred, login_error = await get_cred_or_error()
    if login_error:
        return login_error
    result: dict[str, Any] = {}
    try:
        result["at"] = await retry_network(
            lambda: session.get_at(credential=cred),
            what="获取 @ 通知",
        )
    except Exception as exc:
        result["at_error"] = str(exc)
    try:
        result["likes"] = await retry_network(
            lambda: session.get_likes(credential=cred),
            what="获取点赞通知",
        )
    except Exception as exc:
        result["likes_error"] = str(exc)
    return _json(result)


# ========== 操作日志 ==========


@mcp.tool()
async def bili_activity_log(
    count: int = 20,
    date: str = "",
    tool: str = "",
    writes_only: bool = False,
) -> str:
    """查看 AI 最近对账号执行过哪些操作。

    Args:
        count: 返回最近多少条，默认 20，最大 200
        date: YYYY-MM-DD；留空或 today 表示今天，yesterday 表示昨天
        tool: 只看某个工具，如 bili_reply
        writes_only: 只看评论/私信/动态/投稿等写入操作

    Returns:
        JSON：date、log_dir、count 和 records
    """
    records = read_records(
        limit=max(1, min(count, 200)),
        date=date or None,
        tool=tool or None,
        writes_only=writes_only,
    )
    slim = [
        {
            "time": row.get("ts", row.get("time", "")),
            "tool": row.get("tool", ""),
            "category": row.get("category", ""),
            "status": row.get("status", ""),
            "duration_ms": row.get("duration_ms", 0),
            "args": row.get("args", {}),
            "result": (row.get("result") or "")[:600],
            "error": row.get("error"),
        }
        for row in records
    ]
    return _json(
        {
            "date": date or "today",
            "log_dir": str(LOG_DIR),
            "count": len(slim),
            "records": slim,
        }
    )


# ========== 启用审计与启动 ==========

_audited_tools = install_audit(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")
