"""视频投稿白名单。

只允许上传 ``data/uploads/``（可用 ``BILI_UPLOAD_DIR`` 覆盖）里的视频，
避免 prompt injection 让 agent 把任意本地文件传到 B 站。

这个模块只判断路径，不读文件、不上传。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = ROOT / "data" / "uploads"


class UploadSourceError(ValueError):
    """视频不在允许的投稿目录里，或者根本不是文件。"""


def upload_dir() -> Path:
    """当前允许的投稿目录；相对路径按仓库根解析。"""
    raw = os.environ.get("BILI_UPLOAD_DIR", "").strip()
    if not raw:
        return DEFAULT_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def resolve_upload_video(value: str | Path) -> Path:
    """把视频参数解析成允许上传的本地路径，否则抛 ``UploadSourceError``。"""
    raw = str(value or "").strip()
    if not raw:
        raise UploadSourceError("视频路径不能为空")
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise UploadSourceError(f"视频文件不存在：{raw}") from None
    if not path.is_file():
        raise UploadSourceError(f"不是视频文件：{raw}")

    try:
        root = upload_dir().resolve()
    except OSError:
        raise UploadSourceError("投稿目录不可用，请检查 BILI_UPLOAD_DIR") from None
    if path != root and root not in path.parents:
        raise UploadSourceError(
            f"视频来源不允许：只能上传 {root} 里的文件。"
            "先把视频放到这个目录，或设置 BILI_UPLOAD_DIR。"
        )
    return path
