#!/usr/bin/env python3
"""绘画 AI 适配层（接口先定，供应商待选）。

图片规则：发出去的图只能来自本地表情包或这里返回的绘画 AI 产物，所以
``bili_generate_image`` 不再本地手绘，统一走这个模块。

对外只暴露一个入口：

    await generate(prompt, width=1024, height=1024) -> Path

产物必须落在 ``data/drawn/``，这样 ``bili_images`` 的白名单才认。
供应商还没选定，当前调用会抛 ``DrawNotConfigured``，工具会把这个原因
原样告诉模型，不会偷偷退回本地画图。
"""

from __future__ import annotations

from pathlib import Path


class DrawError(RuntimeError):
    """绘画 AI 调用失败。"""


class DrawNotConfigured(DrawError):
    """还没接上绘画 AI（供应商未选定或缺少配置）。"""


def clamp_size(value: int, default: int = 1024) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = default
    return max(512, min(1600, size))


def configured() -> bool:
    """供应商接上之前始终为 False。"""
    return False


async def generate(
    prompt: str, *, width: int = 1024, height: int = 1024
) -> Path:
    """请求绘画 AI 生成一张图，返回 ``data/drawn/`` 下的本地路径。

    选定供应商后在这里实现具体协议；无论哪家，产物都要保存到
    ``DRAWN_DIR``，并返回保存后的路径。
    """
    text = (prompt or "").strip()
    if not text:
        raise DrawError("prompt 不能为空")
    raise DrawNotConfigured(
        "绘画 AI 还没接入：供应商尚未选定，暂时无法生成新图。"
        "可以先用本地表情包，或者只发文字。"
    )
