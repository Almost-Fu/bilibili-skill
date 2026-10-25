"""B 站工具的可调参数。

取值优先级：**环境变量 > 仓库根目录的 config.json > 代码默认值**。

``config.json`` 与 ``mcp_server.py`` 同级，由桌面设置界面写入，不需要提交
（可选 ``BILI_CONFIG_PATH`` 环境变量指到别处）。MCP Server 在启动时读取，
改完要重启 Server（Agent 里就是重新激活技能）才生效；环境变量适合临时覆盖，
不用改文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONFIG_ENV = "BILI_CONFIG_PATH"

# 缓存按文件路径分开存，测试可以传自己的临时路径。
_CACHE: dict[str, dict[str, Any]] = {}


def config_path() -> Path:
    """当前使用的 config.json 路径。"""
    raw = (os.getenv(CONFIG_ENV) or "").strip()
    return Path(raw).expanduser() if raw else ROOT / "config.json"


def load(path: str | Path | None = None) -> dict[str, Any]:
    """读取配置字典；文件不存在或格式错误时返回空字典。"""
    target = Path(path) if path is not None else config_path()
    key = os.path.normcase(str(target.absolute()))
    if key not in _CACHE:
        values: dict[str, Any] = {}
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                values = raw
        except (OSError, ValueError):
            values = {}
        _CACHE[key] = values
    return _CACHE[key]


def clear_cache() -> None:
    """丢掉缓存，下次读取重新解析（测试和设置界面保存后使用）。"""
    _CACHE.clear()


def _raw(name: str, path: str | Path | None = None) -> str | None:
    env_value = os.getenv(name)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    value = load(path).get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def get_str(name: str, default: str, *, path: str | Path | None = None) -> str:
    raw = _raw(name, path)
    return default if raw is None else raw


def get_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    path: str | Path | None = None,
) -> int:
    try:
        value = int(str(_raw(name, path)).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def get_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    path: str | Path | None = None,
) -> float:
    try:
        value = float(str(_raw(name, path)).strip())
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def get_bool(name: str, default: bool, *, path: str | Path | None = None) -> bool:
    raw = _raw(name, path)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default
