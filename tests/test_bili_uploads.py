"""投稿视频目录白名单。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bili_uploads


class UploadSourceTests(unittest.TestCase):
    def test_允许目录内的文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"x")
            with mock.patch.dict(os.environ, {"BILI_UPLOAD_DIR": tmp}):
                self.assertEqual(
                    bili_uploads.resolve_upload_video(video), video.resolve()
                )

    def test_拒绝目录外的文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowed = root / "uploads"
            allowed.mkdir()
            outside = root / "outside.mp4"
            outside.write_bytes(b"x")
            with mock.patch.dict(
                os.environ, {"BILI_UPLOAD_DIR": str(allowed)}
            ):
                with self.assertRaises(bili_uploads.UploadSourceError):
                    bili_uploads.resolve_upload_video(outside)

    def test_拒绝不存在的文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"BILI_UPLOAD_DIR": tmp}):
                with self.assertRaises(bili_uploads.UploadSourceError):
                    bili_uploads.resolve_upload_video(
                        Path(tmp) / "missing.mp4"
                    )

    def test_拒绝目录(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"BILI_UPLOAD_DIR": tmp}):
                with self.assertRaises(bili_uploads.UploadSourceError):
                    bili_uploads.resolve_upload_video(tmp)

    def test_相对目录按仓库根解析(self):
        with mock.patch.dict(
            os.environ, {"BILI_UPLOAD_DIR": "data/uploads"}
        ):
            self.assertEqual(
                bili_uploads.upload_dir(),
                bili_uploads.ROOT / "data" / "uploads",
            )


if __name__ == "__main__":
    unittest.main()
