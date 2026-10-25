"""视频“第二层”：按时间点远程抽高清帧。

第一层用雪碧图给出 480×270 的联系表，适合看“发生了什么”；这一层只在
需要读小字、代码、UI、商品参数时，按模型指定的秒数 seek 到视频流，
抽 1-3 张 720p/1080p 帧。视频不落盘，只保存抽出来的 JPEG。

PyAV 自带 FFmpeg 解码器，不需要系统安装 ffmpeg；B 站 DASH 流支持
HTTP Range，``av.open`` 可以直接远程 seek，不会把整段视频下载下来。
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image

try:
    import av
except ImportError:  # 没装 PyAV 时，第一层和其余功能仍然可用
    av = None

import bili_config

from bili_storyboard import (
    Frame,
    build_contact_sheet,
    format_time,
    resolve_window,
    save_contact_sheet,
)

PROJECT_ROOT = Path(__file__).resolve().parent
FRAMES_ROOT = PROJECT_ROOT / "data" / "tmp" / "frames"

# 下面这些都可以用环境变量或 config.json 覆盖，见 bili_config.py。
MAX_FRAMES = bili_config.get_int("BILI_FRAMES_MAX", 3, minimum=1, maximum=9)
MAX_HEIGHT = bili_config.get_int(
    "BILI_FRAMES_MAX_HEIGHT", 1080, minimum=360, maximum=2160
)
MIN_HEIGHT = 360
MAX_AGE_SECONDS = bili_config.get_int(
    "BILI_FRAMES_MAX_AGE_SECONDS", 3600, minimum=60, maximum=86400
)

# 密集联系表：默认每 3 秒一帧，最多 40 帧（约覆盖 2 分钟）。
# 更长的视频自动降频，避免一张图里塞几百格。
DENSE_INTERVAL = bili_config.get_float(
    "BILI_DENSE_INTERVAL_SECONDS", 3.0, minimum=0.5, maximum=60.0
)
DENSE_MAX_FRAMES = bili_config.get_int(
    "BILI_DENSE_MAX_FRAMES", 40, minimum=1, maximum=120
)
DENSE_HEIGHT = bili_config.get_int(
    "BILI_DENSE_HEIGHT", 480, minimum=240, maximum=2160
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
HEADER_TEXT = f"Referer: https://www.bilibili.com/\r\nUser-Agent: {USER_AGENT}\r\n"


class FramesError(RuntimeError):
    """没有可用视频流、解码失败或时间点全部无效。"""


def has_pyav() -> bool:
    """第一层密集联系表依赖 PyAV；没有时仍可用雪碧图版。"""
    return av is not None


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def normalize_times(
    times: Sequence[Any],
    duration: float = 0,
    *,
    max_frames: int = MAX_FRAMES,
) -> list[float]:
    """排序、去重、截到时长内，并最多保留 ``max_frames`` 个时间点。"""
    if isinstance(times, (int, float)):
        times = [times]
    values: list[float] = []
    for item in times or []:
        number = _as_float(item)
        if number is None or number < 0:
            continue
        if duration > 0 and number > duration:
            continue
        values.append(round(number, 2))

    # 0.25 秒内视为同一个点，避免模型把 30 和 30.1 当成两个需求
    unique: list[float] = []
    for value in sorted(values):
        if not unique or value - unique[-1] > 0.25:
            unique.append(value)

    limit = max(1, min(int(max_frames or MAX_FRAMES), MAX_FRAMES))
    if len(unique) > limit:
        if limit == 1:
            unique = [unique[0]]
        else:
            step = (len(unique) - 1) / (limit - 1)
            unique = [unique[round(index * step)] for index in range(limit)]
    return unique


def sample_times(
    duration: float,
    *,
    interval: float = DENSE_INTERVAL,
    max_frames: int = DENSE_MAX_FRAMES,
) -> list[float]:
    """按固定间隔取样，超过上限时保持首尾均匀降频。"""
    total = _as_float(duration) or 0
    step = _as_float(interval) or 0
    if total <= 0 or step <= 0:
        return []
    times = [round(index * step, 2) for index in range(int(total // step) + 1)]
    limit = max(1, min(int(max_frames or DENSE_MAX_FRAMES), DENSE_MAX_FRAMES))
    if len(times) <= limit:
        return times
    if limit == 1:
        return [times[0]]
    ratio = (len(times) - 1) / (limit - 1)
    return [times[round(index * ratio)] for index in range(limit)]


def _urls_of(stream: dict[str, Any]) -> list[str]:
    urls = [stream.get("baseUrl")]
    urls.extend(stream.get("backupUrl") or [])
    return [
        url
        for url in urls
        if isinstance(url, str) and url.startswith(("http://", "https://"))
    ]


def rank_streams(
    streams: Sequence[Any], *, max_height: int = MAX_HEIGHT
) -> list[dict[str, Any]]:
    """按“高度优先、同高度 AVC 优先”排好候选流，供失败时逐个重试。"""
    cap = max(MIN_HEIGHT, min(int(max_height or MAX_HEIGHT), MAX_HEIGHT))
    usable: list[tuple[int, int, int, dict[str, Any]]] = []
    for item in streams or []:
        if not isinstance(item, dict):
            continue
        urls = _urls_of(item)
        if not urls:
            continue
        height = int(item.get("height") or 0)
        if height <= 0 or height > cap:
            continue
        codec = int(item.get("codecid") or 0)
        # AVC（7）兼容性最好；HEVC/AV1 作为同高度下的备选
        avc = 1 if codec == 7 else 0
        bandwidth = int(item.get("bandwidth") or 0)
        usable.append((height, avc, bandwidth, item))
    if not usable:
        raise FramesError(f"没有不超过 {cap}p 的可用视频流")
    usable.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [row[3] for row in usable]


def pick_stream(
    streams: Sequence[Any], *, max_height: int = MAX_HEIGHT
) -> dict[str, Any]:
    """挑最优的一路；实际抽帧时用 ``rank_streams`` 保留备选。"""
    return rank_streams(streams, max_height=max_height)[0]


def _open_stream(
    streams: Sequence[dict[str, Any]],
    *,
    opener: Callable[[str, dict[str, str]], Any] | None = None,
) -> tuple[Any, Any, dict[str, Any]]:
    """逐个候选流/ CDN 尝试；返回 ``(container, video_stream, stream)``。

    B 站部分 CDN 会间歇性 I/O 失败，同一路流换备用地址通常能打开；
    1080p AVC 全失败时再退到 HEVC/AV1 或 720p。
    """
    if opener is None:
        if av is None:
            raise FramesError("未安装 PyAV，无法抽高清帧")
        opener = lambda url, headers: av.open(  # noqa: E731
            url,
            options={"headers": headers},
            timeout=(8, 20),
        )

    last: Exception | None = None
    attempts = 0
    for stream in streams:
        for url in _urls_of(stream):
            if attempts >= 6:  # 别让一次抽帧卡在坏 CDN 上太久
                break
            attempts += 1
            container = None
            try:
                container = opener(url, HEADER_TEXT)
                video_stream = container.streams.video[0]
                return container, video_stream, stream
            except Exception as exc:  # 单个 CDN 失败就换备用地址
                last = exc
                if container is not None:
                    try:
                        container.close()
                    except Exception:
                        pass
                continue
        if attempts >= 6:
            break
    raise FramesError(f"视频流打不开：{type(last).__name__}: {last}")


def _decode_at(container: Any, stream: Any, target: float) -> Any:
    """seek 到目标时间，解码出第一帧不早于 ``target`` 的画面。"""
    time_base = float(stream.time_base)
    if time_base <= 0:
        raise FramesError("视频流时间基无效")
    container.seek(int(target / time_base), stream=stream)
    for frame in container.decode(stream):
        stamp = getattr(frame, "time", None)
        if stamp is None:
            continue
        if stamp + 0.001 >= target:
            return frame
    raise FramesError(f"{target:.2f}s 处没有解码出画面")


def _label(seconds: float) -> str:
    whole = int(seconds)
    fraction = seconds - whole
    text = format_time(whole)
    if fraction >= 0.05:
        return f"{text}.{int(round(fraction * 10))}"
    return text


def _extract_and_save(
    streams: Sequence[dict[str, Any]],
    times: list[float],
    *,
    max_height: int,
    base_dir: str | Path | None,
    opener: Callable[[str, dict[str, str]], Any] | None,
) -> dict[str, Any]:
    cleanup_id = uuid.uuid4().hex
    root = Path(base_dir) if base_dir else FRAMES_ROOT
    target_dir = root / cleanup_id
    try:
        container, video_stream, stream = _open_stream(streams, opener=opener)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    target_dir.mkdir(parents=True, exist_ok=True)

    frames: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    try:
        for index, when in enumerate(times):
            try:
                frame = _decode_at(container, video_stream, when)
                image = frame.to_image()
                if image.mode != "RGB":
                    image = image.convert("RGB")
                # 流本身已经不超过 max_height；这里只做保险，避免异常大帧
                if image.height > max_height:
                    ratio = max_height / image.height
                    image = image.resize(
                        (max(1, int(image.width * ratio)), max_height),
                        Image.Resampling.LANCZOS,
                    )
                path = target_dir / (
                    f"frame_{index:02d}_{_label(when).replace(':', '-')}.jpg"
                )
                image.save(path, format="JPEG", quality=90, optimize=True)
                frames.append(
                    {
                        "time": when,
                        "label": _label(when),
                        "path": str(path.resolve()),
                        "width": image.width,
                        "height": image.height,
                        "bytes": path.stat().st_size,
                    }
                )
            except Exception as exc:
                skipped.append(
                    {"time": when, "error": f"{type(exc).__name__}: {exc}"}
                )
    finally:
        try:
            container.close()
        except Exception:
            pass

    if not frames:
        shutil.rmtree(target_dir, ignore_errors=True)
        detail = skipped[0]["error"] if skipped else "没有有效时间点"
        raise FramesError(f"没有抽到可用画面：{detail}")

    return {
        "cleanup_id": cleanup_id,
        "frames": frames,
        "skipped": skipped,
        "stream_height": int(stream.get("height") or 0),
        "stream_codec": int(stream.get("codecid") or 0),
        "stream_width": int(stream.get("width") or 0),
    }


async def fetch_frames(
    bvid: str,
    cid: int,
    duration: float,
    times: Sequence[Any],
    playurl: dict[str, Any],
    *,
    max_frames: int = MAX_FRAMES,
    max_height: int = MAX_HEIGHT,
    base_dir: str | Path | None = None,
    opener: Callable[[str, dict[str, str]], Any] | None = None,
) -> dict[str, Any]:
    """按时间点抽帧并保存；``playurl`` 是 ``get_download_url()`` 的结果。"""
    cleanup_old_frames(base_dir=base_dir)
    normalized = normalize_times(times, duration, max_frames=max_frames)
    if not normalized:
        raise FramesError("没有有效时间点：请传 0 到视频时长之间的秒数")

    streams = (playurl.get("dash") or {}).get("video") or []
    if not streams:
        raise FramesError("该视频没有 DASH 视频流")
    candidates = rank_streams(streams, max_height=max_height)

    result = await asyncio.to_thread(
        _extract_and_save,
        candidates,
        normalized,
        max_height=max_height,
        base_dir=base_dir,
        opener=opener,
    )
    return {
        "bvid": bvid,
        "cid": cid,
        "count": len(result["frames"]),
        "requested": len(normalized),
        "times": normalized,
        "duration": duration,
        "frames": result["frames"],
        "skipped": result["skipped"],
        "cleanup_id": result["cleanup_id"],
        "stream": {
            "height": result["stream_height"],
            "width": result["stream_width"],
            "codecid": result["stream_codec"],
        },
        "source": "pyav-remote-seek",
        "expires_in_seconds": MAX_AGE_SECONDS,
    }


async def fetch_dense_storyboard(
    bvid: str,
    cid: int,
    duration: float,
    playurl: dict[str, Any],
    *,
    interval: float = DENSE_INTERVAL,
    max_frames: int = DENSE_MAX_FRAMES,
    max_height: int = DENSE_HEIGHT,
    start: float | None = None,
    end: float | None = None,
    base_dir: str | Path | None = None,
    opener: Callable[[str, dict[str, str]], Any] | None = None,
) -> dict[str, Any]:
    """按固定间隔抽帧并拼成一张密集联系表（第一层的加密版）。

    默认整段视频；传 ``start`` / ``end`` 只看其中不超过 2 分钟的一段，
    长视频可以按窗口逐段循环。与雪碧图版相比，时间点不再受 B 站
    ``videoshot`` 约 5 秒的粒度限制；代价是要远程解码视频流。默认用最低
    一档清晰度（≤480p），拼图后每格仍保持流本身的尺寸，token 成本由整张
    图的分辨率决定。
    """
    if av is None:
        raise FramesError("未安装 PyAV，无法生成密集联系表")
    total = _as_float(duration) or 0
    begin, finish = resolve_window(total, start, end)
    span = finish - begin
    if total <= 0 or span <= 0:
        raise FramesError("视频时长无效，无法按间隔取样")
    times = [
        round(begin + item, 3)
        for item in sample_times(span, interval=interval, max_frames=max_frames)
    ]
    if not times:
        raise FramesError("视频时长无效，无法按间隔取样")
    # 采样点正好落在片尾时通常解码不出画面，往前退半秒
    if times[-1] >= total - 0.05:
        times[-1] = max(begin, round(total - 0.5, 3))
        times = sorted(set(times))

    streams = (playurl.get("dash") or {}).get("video") or []
    if not streams:
        raise FramesError("该视频没有 DASH 视频流")
    candidates = rank_streams(streams, max_height=max_height)
    # 联系表优先用最低一档省带宽；打不开时 _open_stream 会退到更高档
    candidates = sorted(candidates, key=lambda item: int(item.get("height") or 0))

    extracted = await asyncio.to_thread(
        _extract_and_save,
        candidates,
        times,
        max_height=max_height,
        base_dir=base_dir,
        opener=opener,
    )
    try:
        frames: list[Frame] = []
        for index, item in enumerate(extracted["frames"]):
            with Image.open(item["path"]) as image:
                rgb = image.convert("RGB")
            frames.append(
                Frame(
                    index=index,
                    time=int(round(float(item["time"]))),
                    image=rgb,
                )
            )
        sheet, layout = build_contact_sheet(frames)
        sheet_path, cleanup_id = save_contact_sheet(sheet, base_dir=base_dir)
    finally:
        # 中间帧只用来拼图，拼完就删；只保留联系表目录
        cleanup_frames(extracted["cleanup_id"], base_dir=base_dir)

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
        "source": "pyav-dense",
        "interval_seconds": interval,
        "start": begin,
        "end": finish,
        "window_seconds": round(span, 2),
        "stream": {
            "height": extracted["stream_height"],
            "width": extracted["stream_width"],
            "codecid": extracted["stream_codec"],
        },
        "expires_in_seconds": MAX_AGE_SECONDS,
    }


def cleanup_frames(cleanup_id: str, *, base_dir: str | Path | None = None) -> bool:
    """删除一次抽帧任务目录；``cleanup_id`` 只接受十六进制字符。"""
    if not cleanup_id or not all(ch in "0123456789abcdef" for ch in cleanup_id.lower()):
        return False
    root = Path(base_dir) if base_dir else FRAMES_ROOT
    target = root / cleanup_id
    if not target.is_dir():
        return False
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        return False


def cleanup_old_frames(
    *, max_age_seconds: float = MAX_AGE_SECONDS, base_dir: str | Path | None = None
) -> int:
    """清理超过 TTL 的残留目录，防止进程崩溃后临时帧堆积。"""
    root = Path(base_dir) if base_dir else FRAMES_ROOT
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
