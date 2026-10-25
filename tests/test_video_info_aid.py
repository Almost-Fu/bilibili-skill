"""视频详情：知道 av 号、不知道 BV 号时可以直接反查。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import mcp_server


class FakeVideo:
    calls: list[tuple[str | None, int | None]] = []

    def __init__(self, *, bvid=None, aid=None, credential=None):
        self.bvid = bvid
        self.aid = aid
        FakeVideo.calls.append((bvid, aid))

    async def get_info(self):
        return {
            "bvid": "BV1xx411c7mD",
            "aid": 117491196038518,
            "title": "测试视频",
            "owner": {"name": "UP"},
            "pages": [],
            "stat": {"view": 1},
        }


async def fake_cred():
    return None


class VideoInfoAidTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeVideo.calls = []
        self.patches = (
            patch.object(mcp_server, "get_cred", fake_cred),
            patch.object(mcp_server.video, "Video", FakeVideo),
        )
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    async def test_用aid反查bvid(self):
        data = json.loads(
            await mcp_server.bili_video_info(aid=117491196038518)
        )

        self.assertEqual(FakeVideo.calls, [(None, 117491196038518)])
        self.assertEqual(data["bvid"], "BV1xx411c7mD")
        self.assertEqual(data["aid"], 117491196038518)

    async def test_仍然支持直接传bvid(self):
        data = json.loads(await mcp_server.bili_video_info(bvid="BV1xx411c7mD"))

        self.assertEqual(FakeVideo.calls, [("BV1xx411c7mD", None)])
        self.assertEqual(data["bvid"], "BV1xx411c7mD")

    async def test_bvid和aid都不传时返回错误(self):
        data = json.loads(await mcp_server.bili_video_info())

        self.assertIn("bvid 或 aid", data["error"])
        self.assertEqual(FakeVideo.calls, [])
