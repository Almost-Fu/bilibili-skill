#!/usr/bin/env python3
"""出站图片白名单。

本账号发出去的图片只能来自两个地方：

* 本地表情包库 ``data/self/stickers/``；
* 绘画 AI 的产物 ``data/drawn/``。

网络图片、账号头像、形象立绘、旧的手绘配图、任意本地路径一律拒绝。
这个模块只做“这张图能不能发”的判断，不负责读图或上传。
"""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
STICKER_DIR = DATA_DIR / "self" / "stickers"
DRAWN_DIR = DATA_DIR / "drawn"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


class ImageSourceError(ValueError):
    """图片不在允许的来源里，或者根本不是一张可用的图片。"""


def allowed_dirs() -> tuple[Path, ...]:
    """当前允许的两个来源目录；测试可以替换模块常量。"""
    return (STICKER_DIR, DRAWN_DIR)


def _inside(path: Path, root: Path) -> bool:
    try:
        root = root.resolve()
    except OSError:
        return False
    return path == root or root in path.parents


def _looks_like_name(raw: str) -> bool:
    """参数像「表情名」而不是路径：没有路径分隔符，也不是网址。"""
    name = raw.strip().strip('"').strip("'").strip()
    return bool(name) and not any(ch in name for ch in '\\/:*?"<>|') and ".." not in name


def _sticker_by_name(raw: str) -> Path | None:
    """按名字在表情包里找图，可带或不带扩展名。

    表情名是模型最常用的写法（如 ``骄傲``），这里直接认，省得它拼路径。
    只接受纯名字，带分隔符的走正常路径解析。
    """
    if not _looks_like_name(raw) or not STICKER_DIR.is_dir():
        return None
    stem = raw.strip().strip('"').strip("'").strip()
    for ext in IMAGE_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    stem = stem.strip().lower()
    if not stem:
        return None
    try:
        candidates = sorted(STICKER_DIR.iterdir())
    except OSError:
        return None
    for path in candidates:
        if (
            path.is_file()
            and path.suffix.lower() in IMAGE_EXTS
            and path.stem.lower() == stem
        ):
            try:
                return path.resolve(strict=True)
            except OSError:
                return None
    return None


def resolve_outbound_image(value: str | Path) -> Path:
    """把图片参数解析成允许发送的本地路径，否则抛 ``ImageSourceError``。

    可以直接写表情名（``骄傲`` / ``骄傲.png``），也可以写白名单目录里的完整路径。
    """
    raw = str(value or "").strip()
    if not raw:
        raise ImageSourceError("图片路径不能为空")
    if raw.startswith(("http://", "https://", "//")):
        raise ImageSourceError("不允许发送网络图片，只能用本地表情包或绘画 AI 生成的图")

    path = _sticker_by_name(raw)
    if path is None:
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            if _looks_like_name(raw):
                raise ImageSourceError(
                    f"表情包里没有「{raw}」，先用 bili_sticker_list 看有哪些名字；"
                    "绘画产物要写完整路径"
                ) from None
            raise ImageSourceError(f"图片不存在：{raw}") from None

    if not path.is_file():
        raise ImageSourceError(f"不是图片文件：{raw}")
    if path.suffix.lower() not in IMAGE_EXTS:
        raise ImageSourceError("只支持 png / jpg / webp 图片")

    if not any(_inside(path, root) for root in allowed_dirs()):
        raise ImageSourceError(
            "图片来源不允许：只能用本地表情包（data/self/stickers/）"
            "或绘画 AI 生成的图（data/drawn/）"
        )
    return path
