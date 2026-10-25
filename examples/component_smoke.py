#!/usr/bin/env python3
"""最小宿主示例：按需启动技能包，不在后台常驻。

默认只读 ``skill.json``，打印 Agent 平时能看到的那一两行描述，不启动 MCP；
加 ``--call`` 才真正激活：启动 stdio 子进程、列工具、调用、关闭。

这个脚本故意只依赖 ``skill.json`` 和 MCP 协议，不 import 任何 B 站代码：

1. 读 ``skills/bilibili/skill.json``；
2. 描述模式：只打印 ``description`` / ``when_to_use`` / ``activation``；
3. 激活模式：按 ``mcp`` 段启动子进程，``initialize`` 后列工具；
4. 调一个工具，打印结果；
5. 退出时关闭子进程。

真实宿主会在最后一次调用后启动 idle timer，超时再关闭；示例只调用一次就退出。
权限、额度、重启和审计也由宿主负责，这里只验证组件契约。

用法：
    python examples/component_smoke.py
    python examples/component_smoke.py --call bili_sticker_list
    python examples/component_smoke.py --call bili_search --args '{"keyword": "B站"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "bilibili"
MANIFEST = SKILL_DIR / "skill.json"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def describe(manifest: dict[str, Any]) -> None:
    """宿主启动时只做这件事：把一行描述放进上下文，不拉起进程。"""
    activation = manifest.get("activation") or {}
    print(f"component: {manifest['name']} v{manifest.get('version', '?')}")
    print(f"description: {manifest.get('description', '')}")
    print(f"when_to_use: {manifest.get('when_to_use', '')}")
    print(
        "activation: "
        f"{activation.get('mode', '?')} "
        f"(idle {activation.get('idle_timeout_minutes', '?')} min)"
    )


def server_params(manifest: dict[str, Any]) -> StdioServerParameters:
    """把 manifest 的 mcp 段翻译成 MCP 客户端的启动参数。"""
    cfg = manifest["mcp"]
    command = ""
    if cfg.get("command_env"):
        command = os.environ.get(cfg["command_env"], "")
    command = command or cfg.get("command") or "python"
    if command in {"python", "python3"}:
        command = sys.executable
    cwd = (SKILL_DIR / cfg.get("cwd", ".")).resolve()
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return StdioServerParameters(
        command=command,
        args=list(cfg.get("args") or []),
        cwd=str(cwd),
        env=env,
    )


def content_text(result: Any) -> str:
    parts = []
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def preflight(params: StdioServerParameters) -> None:
    """启动前把最常见的路径/解释器问题说清楚，别只报 Connection closed。"""
    command = params.command
    looks_like_path = (
        Path(command).is_absolute() or "/" in command or "\\" in command
    )
    if looks_like_path:
        if not Path(command).is_file():
            raise RuntimeError(f"找不到 Python 解释器：{command}")
    elif shutil.which(command) is None:
        raise RuntimeError(f"PATH 里找不到命令：{command}")

    if not params.args:
        return
    entry = Path(params.args[0])
    if entry.suffix != ".py":
        return
    if not entry.is_absolute():
        entry = Path(params.cwd or ".") / entry
    if not entry.is_file():
        raise RuntimeError(f"找不到 MCP Server 启动脚本：{entry.resolve()}")


def stderr_tail(errlog: Any, limit: int = 2000) -> str:
    """读出子进程 stderr 的尾部；errlog 不可读时返回空串。"""
    try:
        errlog.flush()
        errlog.seek(0)
        text = errlog.read()
    except (OSError, ValueError):
        return ""
    return str(text or "").strip()[-limit:]


def launch_error(
    params: StdioServerParameters, errlog: Any, exc: BaseException
) -> RuntimeError:
    tail = stderr_tail(errlog)
    detail = tail or f"（子进程没有输出 stderr）\n{type(exc).__name__}: {exc}"
    return RuntimeError(
        "MCP Server 启动失败。\n"
        f"  command: {params.command}\n"
        f"  args: {params.args}\n"
        f"  cwd: {params.cwd}\n"
        f"  stderr:\n{detail}"
    )


async def activate_and_call(manifest: dict[str, Any], call: str, args: dict[str, Any]) -> int:
    """真正要用时才走到这里：拉起子进程，用完立刻关闭。"""
    params = server_params(manifest)
    print(f"activate: {manifest['name']} -> {params.command}")
    preflight(params)
    # StringIO 没有 fileno()，stdio_client 要真实文件描述符，所以用临时文件。
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        initialized = False
        try:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    initialized = True
                    tools = (await session.list_tools()).tools
                    print(f"tools: {len(tools)}")
                    print("first 5:", ", ".join(tool.name for tool in tools[:5]))

                    result = await session.call_tool(call, args)
                    text = content_text(result)
                    print(f"call {call}: {text[:500]}")
        except Exception as exc:
            if initialized:
                # 已经握手成功，后面是工具调用错误，不要误报成启动失败。
                raise
            raise launch_error(params, errlog, exc) from exc
    print("deactivate: closed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--call",
        default="",
        help="要调用的工具名；省略时只描述组件，不启动 MCP",
    )
    parser.add_argument(
        "--args",
        default="{}",
        help="工具参数 JSON，例如 '{\"count\": 1}'",
    )
    ns = parser.parse_args()

    manifest = load_manifest()
    if not ns.call:
        describe(manifest)
        return 0

    try:
        tool_args = json.loads(ns.args)
    except json.JSONDecodeError as exc:
        parser.error(f"--args 不是合法 JSON: {exc}")
    if not isinstance(tool_args, dict):
        parser.error("--args 必须是 JSON 对象")
    try:
        return asyncio.run(activate_and_call(manifest, ns.call, tool_args))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
