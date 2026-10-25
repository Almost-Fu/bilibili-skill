#!/usr/bin/env python3
"""技能内 MCP 启动器。

`skill.json` 只描述「启动这个文件」，不再假设宿主从哪个目录启动。
脚本自己找仓库根：

1. 优先用 ``BILI_SKILL_ROOT``；
2. 否则从技能目录向上找 ``mcp_server.py``。

整个仓库被移动或重命名后仍能启动；技能目录被单独复制、
找不到服务端代码时，也会给出可读的错误，而不是让宿主只看到
``Connection closed``。

用法::

    python skills/bilibili/run_mcp.py
    python skills/bilibili/run_mcp.py --check
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
SERVER_FILE = "mcp_server.py"

# import 名 -> 安装名。这里只放启动时必须能 import 的依赖；
# 缺失会让 Server 直接起不来。
DEPENDENCIES = {
    "bilibili_api": "bilibili-api-python",
    "mcp": "mcp",
    "httpx": "httpx",
    "qrcode": "qrcode[pil]",
    "PIL": "Pillow",
}

# 只在部分工具或可选功能里用到；缺了不阻止启动。
OPTIONAL_DEPENDENCIES = {
    "aiohttp": "aiohttp",
    "av": "av（PyAV，缺了退回雪碧图）",
    "dotenv": "python-dotenv",
}


def find_root() -> Path | None:
    """按环境变量或目录层级找仓库根；找不到返回 None。"""
    override = os.environ.get("BILI_SKILL_ROOT", "").strip()
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            # 相对路径按技能目录解析，避免宿主 cwd 不同导致行为漂移。
            root = SKILL_DIR / root
        try:
            root = root.resolve()
        except OSError:
            return None
        return root if (root / SERVER_FILE).is_file() else None
    for base in (SKILL_DIR, *SKILL_DIR.parents):
        if (base / SERVER_FILE).is_file():
            return base
    return None


def root_error() -> str:
    override = os.environ.get("BILI_SKILL_ROOT", "").strip()
    if override:
        return (
            f"BILI_SKILL_ROOT 指向的目录里没有 {SERVER_FILE}：{override}"
        )
    return (
        "找不到 mcp_server.py。\n"
        "  把 skills/bilibili 放回完整仓库，\n"
        "  或设置 BILI_SKILL_ROOT 指向仓库根。"
    )


def _missing(mapping: dict[str, str]) -> list[tuple[str, str]]:
    """返回 (import 名, 安装名)，不导入模块本身。"""
    missing: list[tuple[str, str]] = []
    for module, package in mapping.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append((module, package))
    return missing


def missing_dependencies() -> list[tuple[str, str]]:
    """启动必需的依赖。"""
    return _missing(DEPENDENCIES)


def missing_optional() -> list[tuple[str, str]]:
    """可选依赖；缺了只影响对应功能。"""
    return _missing(OPTIONAL_DEPENDENCIES)


def install_hint(root: Path) -> str:
    return (
        "用技能包环境的 Python 启动，或安装依赖：\n"
        f'  {sys.executable} -m pip install -r "{root / "requirements.txt"}"'
    )


def fail(message: str) -> int:
    print(f"bilibili-skill: {message}", file=sys.stderr)
    return 2


def check(root: Path | None) -> int:
    print(f"skill: {SKILL_DIR}")
    print(f"python: {sys.executable}")
    if root is None:
        return fail(root_error())
    print(f"root: {root}")
    print(f"server: {root / SERVER_FILE}")
    optional = missing_optional()
    if optional:
        print("optional-missing: " + ", ".join(package for _, package in optional))
    missing = missing_dependencies()
    if missing:
        print("missing: " + ", ".join(package for _, package in missing))
        print(install_hint(root), file=sys.stderr)
        return 2
    print("missing: none")
    return 0


def run(root: Path) -> int:
    sys.path.insert(0, str(root))
    try:
        from mcp_server import mcp
    except ModuleNotFoundError as exc:
        name = exc.name or "unknown"
        package = DEPENDENCIES.get(name, name)
        return fail(f"缺少依赖 {name!r}（{package}）。\n{install_hint(root)}")
    mcp.run(transport="stdio")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查仓库根和依赖，不启动 MCP Server",
    )
    args = parser.parse_args(argv)
    root = find_root()
    if args.check:
        return check(root)
    if root is None:
        return fail(f"{root_error()}\n  当前技能目录：{SKILL_DIR}")
    return run(root)


if __name__ == "__main__":
    raise SystemExit(main())
