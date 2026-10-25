#!/usr/bin/env python3
"""本地形象立绘读取。

只负责找到 ``data/self/character.*`` 和旁边的文字描述，并在原图过大时
压一份缩略图给模型看。这里不画图、不生成配图——配图只能来自绘画 AI
（``bili_draw.py``）或本地表情包（``bili_images.py``）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# 形象立绘和文字描述放这里；个人素材默认被 .gitignore 忽略
SELF_IMAGE_DIR = Path(__file__).parent / "data" / "self"
SELF_IMAGE_STEMS = ("character",)
SELF_IMAGE_EXTS = ("png", "jpg", "jpeg", "webp")
SELF_DESC_NAMES = ("character.md", "character.txt")


def self_image_path() -> Path | None:
    for stem in SELF_IMAGE_STEMS:
        for ext in SELF_IMAGE_EXTS:
            path = SELF_IMAGE_DIR / f"{stem}.{ext}"
            if path.is_file():
                return path
    return None


def self_description() -> str:
    for name in SELF_DESC_NAMES:
        path = SELF_IMAGE_DIR / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
    return ""


def self_image_view(path: Path) -> Path:
    """原图太大时压一份缩略图给模型看，避免 base64 占掉几 MB 上下文。"""
    try:
        if path.stat().st_size <= 1_200_000:
            return path
        view = SELF_IMAGE_DIR / f"{path.stem}.view.jpg"
        if view.is_file() and view.stat().st_mtime >= path.stat().st_mtime:
            return view
        with Image.open(path) as source:
            image = source.convert("RGBA")
            flat = Image.new("RGB", image.size, (255, 255, 255))
            flat.paste(image, mask=image.split()[3])
            flat.thumbnail((1024, 1024), Image.LANCZOS)
            flat.save(view, "JPEG", quality=88)
        return view
    except Exception:
        return path
