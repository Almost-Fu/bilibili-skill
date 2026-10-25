#!/usr/bin/env python3
"""表情包库管理：列出现有表情，或把一张图规整后放进库里。

用法：
    python bili_stickers.py list
    python bili_stickers.py add 图片路径 表情名 [--size 512] [--force]

库目录固定为 data/self/stickers/，文件名（去掉扩展名）就是表情名，
Agent 只按名字挑图，不读图片内容。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from bili_images import IMAGE_EXTS, STICKER_DIR

EXTS = set(IMAGE_EXTS)
BAD_CHARS = set('\\/:*?"<>|')


def _check_name(name: str) -> str:
    clean = (name or "").strip()
    if not clean:
        raise SystemExit("表情名不能为空")
    if any(ch in BAD_CHARS for ch in clean) or ".." in clean:
        raise SystemExit(f"表情名不能包含路径字符：{name}")
    if clean.lower().endswith(tuple(EXTS)):
        clean = clean[: clean.rfind(".")]
    if not clean:
        raise SystemExit("表情名不能为空")
    return clean


def _names() -> list[str]:
    if not STICKER_DIR.is_dir():
        return []
    return sorted(
        p.stem for p in STICKER_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in EXTS
    )


def cmd_list(_args: argparse.Namespace) -> int:
    names = _names()
    if not names:
        print(f"表情包库是空的：{STICKER_DIR}")
        return 0
    print(f"{STICKER_DIR}  共 {len(names)} 个：")
    for name in names:
        path = next(
            p for p in STICKER_DIR.iterdir()
            if p.is_file() and p.stem == name and p.suffix.lower() in EXTS
        )
        with Image.open(path) as im:
            print(f"  {name:<8} {im.width}x{im.height}  {path.stat().st_size // 1024}KB")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    src = Path(args.image).expanduser()
    if not src.is_file():
        raise SystemExit(f"图片不存在：{src}")
    name = _check_name(args.name)
    STICKER_DIR.mkdir(parents=True, exist_ok=True)
    dest = STICKER_DIR / f"{name}.png"
    if dest.exists() and not args.force:
        raise SystemExit(f"「{name}」已存在，加 --force 覆盖")

    with Image.open(src) as im:
        image = im.convert("RGBA")
        # 裁成正方形：有透明通道就按主体外框裁，否则按中心裁
        alpha = image.getchannel("A")
        box = alpha.getbbox() if alpha.getextrema()[0] < 255 else None
        if box is None:
            side = min(image.size)
            left = (image.width - side) // 2
            top = (image.height - side) // 2
            box = (left, top, left + side, top + side)
        else:
            left, top, right, bottom = box
            side = max(right - left, bottom - top)
            cx, cy = (left + right) // 2, (top + bottom) // 2
            box = (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)
        image = image.crop(box)
        if image.width != args.size:
            image = image.resize((args.size, args.size), Image.LANCZOS)
        image.save(dest, format="PNG", optimize=True)

    size_kb = dest.stat().st_size / 1024
    print(f"已入库：{dest}  {args.size}x{args.size}  {size_kb:.0f}KB")
    if size_kb > 8 * 1024:
        print("警告：超过 8MB，B站私信可能发不出去")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="表情包库管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出库里现有的表情")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="把一张图规整后放进库里")
    p_add.add_argument("image", help="图片路径")
    p_add.add_argument("name", help="表情名，如 骄傲")
    p_add.add_argument("--size", type=int, default=512, help="边长，默认 512")
    p_add.add_argument("--force", action="store_true", help="覆盖同名表情")
    p_add.set_defaults(func=cmd_add)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
