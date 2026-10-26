"""超长视频保护：超过上限时概览和抽帧工具直接拒绝。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bili_config
import mcp_server


class FakeVideo:
    def __init__(self, *, bvid, credential=None):
        self.bvid = bvid

    async def get_info(self):
        return {
            "title": "超长视频",
            "duration": 181 * 60,
            "cid": 123,
            "pages": [],
        }


async def fake_cred():
    return mcp_server.Credential()


class VideoDurationLimitTests(unittest.TestCase):
    def setUp(self):
        # 隔离本机 config.json，避免用户调过上限后测试结果漂移。
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        config = Path(self._tmp.name) / "config.json"
        patcher = patch.dict(
            os.environ, {"BILI_CONFIG_PATH": str(config)}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        bili_config.clear_cache()
        self.addCleanup(bili_config.clear_cache)

    def test_默认上限是60分钟(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BILI_MAX_VIDEO_MINUTES", None)
            self.assertIsNone(
                mcp_server._video_duration_limit_error(60 * 60)
            )
            error = mcp_server._video_duration_limit_error(60 * 60 + 1)
        self.assertIsNotNone(error)
        data = json.loads(error)
        self.assertEqual(data["max_duration_minutes"], 60)
        self.assertEqual(data["duration"], 60 * 60 + 1)

    def test_环境变量可以改上限(self):
        with patch.dict(os.environ, {"BILI_MAX_VIDEO_MINUTES": "30"}):
            self.assertIsNone(
                mcp_server._video_duration_limit_error(30 * 60)
            )
            data = json.loads(
                mcp_server._video_duration_limit_error(30 * 60 + 1)
            )
        self.assertEqual(data["max_duration_minutes"], 30)

    def test_非法环境变量回退默认值(self):
        with patch.dict(os.environ, {"BILI_MAX_VIDEO_MINUTES": "abc"}):
            self.assertEqual(mcp_server._max_video_minutes(), 60)

    def test_概览拒绝超长视频(self):
        with (
            patch.dict(os.environ, {"BILI_MAX_VIDEO_MINUTES": "180"}),
            patch.object(mcp_server, "get_cred_optional", fake_cred),
            patch.object(mcp_server.video, "Video", FakeVideo),
        ):
            data = json.loads(
                asyncio.run(mcp_server.bili_video_overview("BV1xx411c7mD"))
            )
        self.assertIn("超过超长视频上限", data["error"])
        self.assertEqual(data["max_duration_minutes"], 180)

    def test_高清补帧拒绝超长视频(self):
        with (
            patch.dict(os.environ, {"BILI_MAX_VIDEO_MINUTES": "180"}),
            patch.object(mcp_server, "get_cred_optional", fake_cred),
            patch.object(mcp_server.video, "Video", FakeVideo),
        ):
            data = json.loads(
                asyncio.run(
                    mcp_server.bili_video_frames("BV1xx411c7mD", [1.0])
                )
            )
        self.assertIn("超过超长视频上限", data["error"])
        self.assertEqual(data["max_duration_minutes"], 180)


if __name__ == "__main__":
    unittest.main()
