"""私信工具：文字、图片，以及参数校验。不联网，B站接口全部打桩。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import bili_images
import mcp_server


class SendMessageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.png = self.tmp / "card.png"
        Image.new("RGB", (64, 64), (10, 20, 30)).save(self.png)

        # 图片白名单：把临时目录当成表情包库，测试图才算允许的来源
        for target in ("STICKER_DIR", "DRAWN_DIR"):
            patcher = patch.object(bili_images, target, self.tmp)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.calls = []

        async def fake_cred():
            return object()

        async def fake_send(**kwargs):
            self.calls.append(kwargs)
            return {"msg_key": len(self.calls)}

        for target, value in (
            ("get_cred", fake_cred),
            ("send_msg", fake_send),
        ):
            patcher = patch.object(
                mcp_server, target, value
            ) if target == "get_cred" else patch.object(
                mcp_server.session, target, value
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, **kwargs) -> dict:
        return json.loads(asyncio.run(mcp_server.bili_send_message(**kwargs)))

    def test_只发文字(self):
        result = self.call(receiver_uid=1, text="  你好  ")
        self.assertTrue(result["success"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0]["msg_type"], mcp_server.session.EventType.TEXT
        )
        self.assertEqual(self.calls[0]["content"], "你好")

    def test_只发图片(self):
        result = self.call(receiver_uid=1, image=str(self.png))
        self.assertTrue(result["success"])
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["msg_type"], mcp_server.session.EventType.PICTURE)
        self.assertTrue(call["content"].url.startswith("file://"))

    def test_文字和图片一起发(self):
        result = self.call(
            receiver_uid=1, text="看这个", image=str(self.png)
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["sent"], {"text": True, "image": True})
        kinds = [call["msg_type"] for call in self.calls]
        self.assertEqual(
            kinds,
            [
                mcp_server.session.EventType.TEXT,
                mcp_server.session.EventType.PICTURE,
            ],
        )

    def test_图片不存在(self):
        result = self.call(receiver_uid=1, image=str(self.tmp / "nope.png"))
        self.assertFalse(result["success"])
        self.assertIn("不存在", result["error"])
        self.assertEqual(self.calls, [])

    def test_格式不对(self):
        bad = self.tmp / "a.gif"
        bad.write_bytes(b"GIF89a")
        result = self.call(receiver_uid=1, image=str(bad))
        self.assertFalse(result["success"])
        self.assertIn("png", result["error"])

    def test_拒绝网络图片(self):
        result = self.call(
            receiver_uid=1, image="https://i0.hdslb.com/bfs/face/a.jpg"
        )
        self.assertFalse(result["success"])
        self.assertIn("网络图片", result["error"])
        self.assertEqual(self.calls, [])

    def test_拒绝白名单之外的本地图片(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "card.png"
            Image.new("RGB", (8, 8), (1, 2, 3)).save(outside)
            result = self.call(receiver_uid=1, image=str(outside))
        self.assertFalse(result["success"])
        self.assertIn("来源不允许", result["error"])
        self.assertEqual(self.calls, [])

    def test_两个都空(self):
        result = self.call(receiver_uid=1)
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, [])

    def test_图片发失败时文字结果保留(self):
        async def boom(**kwargs):
            self.calls.append(kwargs)
            if kwargs["msg_type"] == mcp_server.session.EventType.PICTURE:
                raise RuntimeError("传输失败")
            return {"msg_key": 1}

        with patch.object(mcp_server.session, "send_msg", boom):
            result = self.call(receiver_uid=1, text="先说话", image=str(self.png))
        self.assertFalse(result["success"])
        self.assertEqual(result["sent"], {"text": True})
        self.assertIn("图片", result["error"])


if __name__ == "__main__":
    unittest.main()
