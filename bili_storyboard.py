"""视频“第一层”概览：B 站自带雪碧图 + 字幕。

不下载视频、不依赖 ffmpeg：B 站播放器会为每个视频生成一张（或多张）
10×10 的预览雪碧图，接口是 ``x/player/videoshot``。这里把用到的格子
裁出来、去重、按时间顺序拼成一张联系表，交给模型一次看完。

联系表的 token 成本远低于逐帧发送：一张 10×10 联系表约 1.5k token，
逐帧发送 100 帧则要 30k 以上。
"""

from __future__ import annotations

import asyncio
import io
import math
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageStat

import bili_config

PROJECT_ROOT = Path(__file__).resolve().parent
STORYBOARD_ROOT = PROJECT_ROOT / "data" / "tmp" / "storyboard"

VIDEOSHOT_URL = "https://api.bilibili.com/x/player/videoshot"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}
MAX_SPRITE_BYTES = 20 * 1024 * 1024
MAX_FRAMES = bili_config.get_int(
    "BILI_STORYBOARD_MAX_FRAMES", 24, minimum=1, maximum=60
)
VIDEOSHOT_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.5


class StoryboardError(RuntimeError):
    """雪碧图不可用、下载失败或没有可用帧。"""


class StoryboardUnavailable(StoryboardError):
    """接口返回 200 但数据为空：B 站偶发，值得再问一次。"""


@dataclass(frozen=True)
class Frame:
    index: int
    time: int
    image: Image.Image


@dataclass(frozen=True)
class Candidate:
    index: int
    time: int
    sprite: int
    col: int
    row: int


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_url(url: Any) -> str:
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def format_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# 长视频一次只看一个 2 分钟窗口：40 帧 × 3 秒刚好铺满。
WINDOW_SECONDS = bili_config.get_float(
    "BILI_WINDOW_SECONDS", 120.0, minimum=10.0, maximum=600.0
)


def resolve_window(
    duration: float,
    start: float | None = None,
    end: float | None = None,
    *,
    max_seconds: float = WINDOW_SECONDS,
) -> tuple[float, float]:
    """把可选的 ``start`` / ``end`` 收成一个不超过 ``max_seconds`` 的窗口。

    * 两个都不给：整段视频，返回 ``(0, duration)``；
    * 只给 ``start``：从 ``start`` 起看 2 分钟；
    * 只给 ``end``：看 ``end`` 之前的 2 分钟；
    * 超出视频结尾或窗口过长时自动裁剪，不报错。
    """
    total = max(0.0, float(duration or 0))
    limit = max(1.0, float(max_seconds or WINDOW_SECONDS))
    if start is None and end is None:
        return 0.0, round(total, 2)
    if end is None:
        begin = float(start or 0)
    elif start is None:
        begin = float(end) - limit
    else:
        begin = float(start)
    if total > 0:
        begin = min(begin, max(0.0, total - 0.1))
    begin = max(0.0, begin)
    if end is None:
        finish = begin + limit
    else:
        finish = float(end)
    if total > 0:
        finish = min(finish, total)
    finish = min(finish, begin + limit)
    if finish <= begin:
        raise ValueError("时间窗口无效：end 必须大于 start")
    return round(begin, 2), round(finish, 2)


def parse_videoshot(payload: dict[str, Any]) -> dict[str, Any]:
    """把 ``x/player/videoshot`` 的响应压成后续需要的字段。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        message = payload.get("message") if isinstance(payload, dict) else ""
        raise StoryboardError(f"雪碧图接口没有返回 data：{message or payload}")

    images = data.get("image") or []
    if isinstance(images, str):
        images = [images]
    images = [_normalize_url(item) for item in images if _normalize_url(item)]
    index = data.get("index") or []
    if not isinstance(index, list):
        index = []
    index = [_as_int(item) for item in index]

    x_len = max(1, _as_int(data.get("img_x_len"), 10))
    y_len = max(1, _as_int(data.get("img_y_len"), 10))
    x_size = max(0, _as_int(data.get("img_x_size"), 0))
    y_size = max(0, _as_int(data.get("img_y_size"), 0))
    if not images or not index:
        raise StoryboardUnavailable("该视频没有可用的雪碧图")

    # index 可能比可裁的格子多，按图片数量截断
    total = min(len(index), len(images) * x_len * y_len)
    if total <= 0:
        raise StoryboardUnavailable("雪碧图没有可用的帧")
    return {
        "images": images,
        "index": index[:total],
        "x_len": x_len,
        "y_len": y_len,
        "x_size": x_size,
        "y_size": y_size,
    }


def times_from_index(index: list[int], duration: int = 0) -> list[int]:
    """把 ``index`` 解释成秒数。

    绝大多数视频的 ``index`` 就是秒数（如 0, 5, 10...）。少数情况下它是
    帧号；此时按最大值等比映射到视频时长，只用于展示，不保证精确。
    """
    values = [max(0, _as_int(item)) for item in index]
    if not values:
        return []
    peak = max(values)
    if duration > 0 and peak > duration + 5:
        return [round(value / peak * duration) for value in values]
    return values


def build_candidates(meta: dict[str, Any], duration: int = 0) -> list[Candidate]:
    times = times_from_index(meta["index"], duration)
    x_len = meta["x_len"]
    y_len = meta["y_len"]
    per_image = x_len * y_len
    out: list[Candidate] = []
    for frame_index, when in enumerate(times):
        sprite = frame_index // per_image
        if sprite >= len(meta["images"]):
            break
        tile_index = frame_index % per_image
        out.append(
            Candidate(
                index=frame_index,
                time=when,
                sprite=sprite,
                col=tile_index % x_len,
                row=tile_index // x_len,
            )
        )
    return out


def evenly_spaced(items: list[Any], limit: int) -> list[Any]:
    """保序均匀抽取，最多 ``limit`` 个。"""
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


def is_blank(image: Image.Image) -> bool:
    """跳过纯黑、纯白、几乎没有内容的加载页。"""
    gray = image.convert("L")
    stat = ImageStat.Stat(gray)
    mean = stat.mean[0]
    stddev = stat.stddev[0]
    return stddev < 3 and (mean < 12 or mean > 243)


def dhash(image: Image.Image) -> int:
    """64 位差异哈希，用于去掉几乎重复的连续帧。"""
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()
    bits = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits = (bits << 1) | (pixels[base + col] > pixels[base + col + 1])
    return bits


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def crop_tile(sprite: Image.Image, candidate: Candidate, meta: dict[str, Any]) -> Image.Image:
    """按 ``Candidate`` 从雪碧图里裁一格。"""
    tile_w = meta["x_size"] or sprite.width // meta["x_len"]
    tile_h = meta["y_size"] or sprite.height // meta["y_len"]
    left = candidate.col * tile_w
    top = candidate.row * tile_h
    return sprite.crop((left, top, left + tile_w, top + tile_h)).convert("RGB")


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "Arial.ttf", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_contact_sheet(frames: list[Frame]) -> tuple[Image.Image, dict[str, Any]]:
    """按时间顺序拼成一张带时间戳的联系表。"""
    if not frames:
        raise StoryboardError("没有可拼进联系表的帧")
    tile_w, tile_h = frames[0].image.size
    count = len(frames)
    cols = 4 if count <= 12 else 6
    rows = math.ceil(count / cols)
    label_h = max(22, tile_h // 9)
    sheet = Image.new("RGB", (cols * tile_w, rows * (tile_h + label_h)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    font = _font(max(14, label_h - 8))

    for position, frame in enumerate(frames):
        row, col = divmod(position, cols)
        x = col * tile_w
        y = row * (tile_h + label_h)
        draw.rectangle([x, y, x + tile_w, y + label_h], fill=(0, 0, 0))
        draw.text((x + 8, y + 3), format_time(frame.time), fill=(255, 255, 255), font=font)
        sheet.paste(frame.image, (x, y + label_h))
        draw.rectangle(
            [x, y, x + tile_w - 1, y + tile_h + label_h - 1],
            outline=(90, 90, 90),
            width=1,
        )

    layout = {
        "cols": cols,
        "rows": rows,
        "tile_width": tile_w,
        "tile_height": tile_h,
        "label_height": label_h,
    }
    return sheet, layout


def save_contact_sheet(
    sheet: Image.Image,
    *,
    base_dir: str | Path | None = None,
    quality: int = 85,
) -> tuple[str, str]:
    """保存联系表，返回 ``(绝对路径, cleanup_id)``。"""
    cleanup_id = uuid.uuid4().hex
    root = Path(base_dir) if base_dir else STORYBOARD_ROOT
    target = root / cleanup_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / "sheet.jpg"
    sheet.save(path, format="JPEG", quality=quality, optimize=True)
    return str(path.resolve()), cleanup_id


def cleanup_storyboard(cleanup_id: str, *, base_dir: str | Path | None = None) -> bool:
    """删除一次联系表任务目录；``cleanup_id`` 只接受十六进制字符。"""
    if not cleanup_id or not all(ch in "0123456789abcdef" for ch in cleanup_id.lower()):
        return False
    root = Path(base_dir) if base_dir else STORYBOARD_ROOT
    target = root / cleanup_id
    if not target.is_dir():
        return False
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        return False


def cleanup_old_storyboards(
    *,
    max_age_seconds: float = 3600,
    base_dir: str | Path | None = None,
) -> int:
    """清理超过 TTL 的残留目录，防止 Agent 没来得及删时堆积。"""
    root = Path(base_dir) if base_dir else STORYBOARD_ROOT
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
                removed += 1
        except OSError:
            continue
    return removed


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.get(url, params=params, headers=HEADERS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise StoryboardError("雪碧图接口返回的不是 JSON 对象")
    return payload


async def _get_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, headers=HEADERS, follow_redirects=True)
    response.raise_for_status()
    content = response.content
    if len(content) > MAX_SPRITE_BYTES:
        raise StoryboardError("雪碧图文件过大")
    return content


async def _fetch_videoshot_once(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> dict[str, Any]:
    payload = await _get_json(client, VIDEOSHOT_URL, params=params)
    return parse_videoshot(payload)


async def _fetch_videoshot_with_retry(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> dict[str, Any]:
    """空数据是 B 站接口的偶发状态，退避后再问几次。

    只重试 ``StoryboardUnavailable``（HTTP 200 但 image/index 为空）；
    网络错误仍由调用方决定是否重试，避免 DNS 故障时叠加超时。
    """
    for attempt in range(1, VIDEOSHOT_ATTEMPTS + 1):
        try:
            return await _fetch_videoshot_once(client, params)
        except StoryboardUnavailable:
            if attempt >= VIDEOSHOT_ATTEMPTS:
                raise
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
    raise StoryboardError("雪碧图接口重试失败")  # pragma: no cover


async def fetch_videoshot(
    bvid: str,
    cid: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    params = {"bvid": bvid, "cid": cid, "index": 1}
    if client is not None:
        return await _fetch_videoshot_with_retry(client, params)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as own:
        return await _fetch_videoshot_with_retry(own, params)


async def fetch_storyboard(
    bvid: str,
    cid: int,
    *,
    max_frames: int = 12,
    duration: int = 0,
    start: float | None = None,
    end: float | None = None,
    client: httpx.AsyncClient | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """拉雪碧图、裁帧、去重、拼联系表。

    ``start`` / ``end`` 可只取其中一段（最长 2 分钟）；``client`` 只在测试时注入。
    """
    max_frames = max(1, min(int(max_frames or 12), MAX_FRAMES))
    cleanup_old_storyboards(base_dir=base_dir)
    begin, finish = resolve_window(duration, start, end)

    if client is not None:
        return await _fetch_storyboard(
            bvid, cid, max_frames, duration, client, base_dir, begin, finish
        )
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as own:
        return await _fetch_storyboard(
            bvid, cid, max_frames, duration, own, base_dir, begin, finish
        )


async def _fetch_storyboard(
    bvid: str,
    cid: int,
    max_frames: int,
    duration: int,
    client: httpx.AsyncClient,
    base_dir: str | Path | None,
    begin: float = 0.0,
    finish: float = 0.0,
) -> dict[str, Any]:
    meta = await fetch_videoshot(bvid, cid, client=client)
    candidates = build_candidates(meta, duration)
    if duration and finish > 0 and (begin > 0 or finish < duration):
        candidates = [item for item in candidates if begin <= item.time <= finish]
    if not candidates:
        raise StoryboardError("这个时间窗口里没有可用的雪碧图帧")

    # 先多取一些候选，过滤空白/重复后再均匀抽 max_frames 张
    shortlist = evenly_spaced(candidates, max(max_frames * 4, max_frames))
    sprites: dict[int, Image.Image] = {}
    for sprite_index in sorted({item.sprite for item in shortlist}):
        url = meta["images"][sprite_index]
        raw = await _get_bytes(client, url)
        try:
            image = Image.open(io.BytesIO(raw))
            image.load()
        except OSError as exc:
            raise StoryboardError(f"雪碧图无法解码：{exc}") from exc
        sprites[sprite_index] = image.convert("RGB")

    valid: list[Frame] = []
    hashes: list[int] = []
    for candidate in shortlist:
        tile = crop_tile(sprites[candidate.sprite], candidate, meta)
        if is_blank(tile):
            continue
        fingerprint = dhash(tile)
        if any(_hamming(fingerprint, previous) <= 4 for previous in hashes):
            continue
        hashes.append(fingerprint)
        valid.append(Frame(index=candidate.index, time=candidate.time, image=tile))

    if not valid:
        raise StoryboardError("过滤空白/重复画面后没有可用帧")

    frames = evenly_spaced(valid, max_frames)
    sheet, layout = build_contact_sheet(frames)
    sheet_path, cleanup_id = save_contact_sheet(sheet, base_dir=base_dir)
    return {
        "bvid": bvid,
        "cid": cid,
        "count": len(frames),
        "duration": duration,
        "sheet_path": sheet_path,
        "cleanup_id": cleanup_id,
        "layout": layout,
        "frames": [
            {"index": frame.index, "time": frame.time, "label": format_time(frame.time)}
            for frame in frames
        ],
        "source": "bilibili-videoshot",
        "start": begin,
        "end": finish,
        "expires_in_seconds": 3600,
    }


async def fetch_subtitle(
    video_obj: Any,
    cid: int,
    *,
    max_chars: int = 4000,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """取字幕摘要；失败不抛错，返回 ``available: false``。"""
    try:
        payload = await video_obj.get_subtitle(cid=cid)
    except Exception as exc:  # 字幕接口偶发失败，不应拖垮整个概览
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    subtitles = payload.get("subtitles") if isinstance(payload, dict) else None
    if not subtitles:
        return {"available": False, "message": "该视频没有字幕"}

    target = None
    for item in subtitles:
        if isinstance(item, dict) and item.get("lan") in ("ai-zh", "zh-CN", "zh"):
            target = item
            break
    if target is None:
        target = next((item for item in subtitles if isinstance(item, dict)), None)
    if target is None:
        return {"available": False, "message": "该视频没有可用字幕"}

    url = _normalize_url(target.get("subtitle_url"))
    if not url:
        return {"available": False, "message": "字幕地址为空"}

    try:
        if client is not None:
            response = await client.get(url, headers=HEADERS, follow_redirects=True)
        else:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as own:
                response = await own.get(url, headers=HEADERS, follow_redirects=True)
        response.raise_for_status()
        body = response.json().get("body") or []
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    texts = [
        str(item.get("content") or "")
        for item in body
        if isinstance(item, dict) and item.get("content")
    ]
    full_text = "\n".join(texts)
    return {
        "available": True,
        "language": target.get("lan_doc") or target.get("lan") or "",
        "segments": len(texts),
        "total_chars": len(full_text),
        "truncated": len(full_text) > max_chars,
        "text": full_text[:max_chars],
    }
