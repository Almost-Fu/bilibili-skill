"""评论配图：只有自己发主楼评论时能带一张图，回复别人只能文字。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bili_images
import mcp_server


class FakeVideo:
    def __init__(self, *, bvid, credential=None):
        self.bvid = bvid

    async def get_info(self):
        return {
            "aid": 123,
            "owner": {"mid": 456, "name": "UP"},
        }


async def fake_cred():
    return None


def fake_blocked(**kwargs):
    return None


class ReplyImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.image = Path(self._tmp.name) / "sticker.png"
        self.image.write_bytes(b"not-really-a-png")
        self.picture = object()
        self.calls: dict[str, object] = {}

    def tearDown(self):
        self._tmp.cleanup()

    async def _send_comment(self, **kwargs):
        self.calls.update(kwargs)
        return {"rpid": 456}

    def _patches(self):
        return (
            patch.object(mcp_server, "get_cred", fake_cred),
            patch.object(mcp_server.video, "Video", FakeVideo),
            patch.object(mcp_server.bili_avoid, "reply_blocked", fake_blocked),
            patch.object(mcp_server.comment, "send_comment", self._send_comment),
            patch.object(
                mcp_server.bili_images,
                "resolve_outbound_image",
                lambda value: self.image,
            ),
            patch.object(
                mcp_server.Picture, "from_file", lambda path: self.picture
            ),
        )

    async def test_主楼评论可以带一张图(self):
        patches = self._patches()
        for item in patches:
            item.start()
        try:
            data = json.loads(
                await mcp_server.bili_reply(
                    "BV1xx411c7mD", "配图评论", image=str(self.image)
                )
            )
        finally:
            for item in reversed(patches):
                item.stop()

        self.assertTrue(data["success"])
        self.assertIs(self.calls["pic"], self.picture)
        self.assertEqual(data["image"], str(self.image))
        self.assertIsNone(self.calls.get("root"))
        self.assertIsNone(self.calls.get("parent"))

    async def test_主楼评论不带图时pic为空(self):
        patches = self._patches()
        for item in patches:
            item.start()
        try:
            data = json.loads(
                await mcp_server.bili_reply("BV1xx411c7mD", "纯文字")
            )
        finally:
            for item in reversed(patches):
                item.stop()

        self.assertTrue(data["success"])
        self.assertIsNone(self.calls["pic"])
        self.assertNotIn("image", data)

    async def test_主楼评论可以直接写表情名(self):
        sticker_dir = Path(self._tmp.name) / "stickers"
        sticker_dir.mkdir()
        sticker = sticker_dir / "暗中观察.png"
        sticker.write_bytes(b"not-really-a-png")
        patches = (
            patch.object(mcp_server, "get_cred", fake_cred),
            patch.object(mcp_server.video, "Video", FakeVideo),
            patch.object(mcp_server.bili_avoid, "reply_blocked", fake_blocked),
            patch.object(mcp_server.comment, "send_comment", self._send_comment),
            patch.object(bili_images, "STICKER_DIR", sticker_dir),
            patch.object(bili_images, "DRAWN_DIR", Path(self._tmp.name) / "drawn"),
            patch.object(mcp_server.Picture, "from_file", lambda path: self.picture),
        )
        for item in patches:
            item.start()
        try:
            data = json.loads(
                await mcp_server.bili_reply(
                    "BV1xx411c7mD", "配图评论", image="暗中观察"
                )
            )
        finally:
            for item in reversed(patches):
                item.stop()

        self.assertTrue(data["success"], data)
        self.assertIs(self.calls["pic"], self.picture)
        self.assertEqual(data["image"], str(sticker.resolve()))

    async def test_回复别人的评论不能带图(self):
        data = json.loads(
            await mcp_server.bili_reply(
                "BV1xx411c7mD", "回复", rpid=321, image=str(self.image)
            )
        )
        self.assertFalse(data["success"])
        self.assertIn("回复别人的评论不能带图", data["error"])
        self.assertEqual(self.calls, {})

    async def test_图片来源不合法时拒绝(self):
        with patch.object(
            mcp_server.bili_images,
            "resolve_outbound_image",
            side_effect=bili_images.ImageSourceError("只能用表情包或绘画产物"),
        ):
            data = json.loads(
                await mcp_server.bili_reply(
                    "BV1xx411c7mD", "配图评论", image="https://example.com/a.png"
                )
            )
        self.assertFalse(data["success"])
        self.assertIn("只能用表情包或绘画产物", data["error"])
        self.assertEqual(self.calls, {})


if __name__ == "__main__":
    unittest.main()
