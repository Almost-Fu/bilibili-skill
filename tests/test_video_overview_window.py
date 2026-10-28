"""视频概览：整段模式必须覆盖全片，窗口模式才使用 120 秒窗口。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

import mcp_server


class FakeClient:
    async def get_download_url(self, cid: int | None = None) -> dict:
        return {"dash": {"video": []}}


class OverviewWindowTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, **kwargs):
        info = {"title": "测试视频", "duration": 600, "cid": 1, "pages": []}
        captured: dict = {}

        async def fake_dense(bvid, cid, duration, playurl, **options):
            captured.update(options)
            return {
                "start": options.get("start") or 0,
                "end": options.get("end") or duration,
                "frame_times": [],
            }

        with (
            patch.object(
                mcp_server,
                "_open_video",
                AsyncMock(return_value=(None, FakeClient(), info)),
            ),
            patch.object(
                mcp_server,
                "fetch_subtitle",
                AsyncMock(return_value={"skipped": True}),
            ),
            patch.object(mcp_server, "has_pyav", lambda: True),
            patch.object(mcp_server, "fetch_dense_storyboard", fake_dense),
        ):
            data = json.loads(
                await mcp_server.bili_video_overview("BV1xx411c7mD", **kwargs)
            )
        return data, captured

    async def test_整段模式把全片交给底层(self):
        data, captured = await self._call(max_frames=5)

        self.assertEqual(data["window"]["mode"], "whole")
        self.assertEqual(data["window"]["end"], 600)
        self.assertIsNone(captured["start"])
        self.assertIsNone(captured["end"])

    async def test_窗口模式仍然只传窗口(self):
        data, captured = await self._call(start=10, end=20)

        self.assertEqual(data["window"]["mode"], "window")
        self.assertEqual(captured["start"], 10)
        self.assertEqual(captured["end"], 20)


if __name__ == "__main__":
    unittest.main()
