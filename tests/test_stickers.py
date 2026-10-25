"""表情包库：按名字查找、拒绝路径、发送时只传路径不读图。不联网。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import mcp_server


class StickerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for name in ("骄傲.png", "开心.png", "困.jpg", "Happy.png"):
            Image.new("RGB", (32, 32), (1, 2, 3)).save(self.dir / name)
        patcher = patch.object(mcp_server, "STICKER_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_列出表情只返回名字(self):
        result = json.loads(asyncio.run(mcp_server.bili_sticker_list()))
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 4)
        self.assertEqual(
            result["stickers"], sorted(["骄傲", "开心", "困", "Happy"])
        )
        self.assertNotIn("images", result)

    def test_名字带不带扩展名都能找到(self):
        path, name = mcp_server._sticker_path("骄傲")
        self.assertEqual(name, "骄傲")
        self.assertEqual(path, self.dir / "骄傲.png")

        path, name = mcp_server._sticker_path("骄傲.png")
        self.assertEqual(path, self.dir / "骄傲.png")

        path, name = mcp_server._sticker_path("  开心.PNG  ")
        self.assertEqual(path, self.dir / "开心.png")

    def test_大小写不敏感(self):
        path, name = mcp_server._sticker_path("happy")
        self.assertEqual(name, "Happy")
        self.assertEqual(path, self.dir / "Happy.png")

    def test_拒绝路径穿越(self):
        for bad in ("../秘密", "a/b", "C:\\x", "骄傲/../开心", ".."):
            path, _ = mcp_server._sticker_path(bad)
            self.assertIsNone(path, bad)

    def test_没有的表情返回现有列表(self):
        result = json.loads(
            asyncio.run(mcp_server.bili_send_sticker(receiver_uid=1, sticker="难过"))
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            result["available"], sorted(["骄傲", "开心", "困", "Happy"])
        )

    def test_发送时只把路径交给私信工具(self):
        calls = []

        async def fake_send(receiver_uid, text="", image=""):
            calls.append({"receiver_uid": receiver_uid, "text": text, "image": image})
            return json.dumps({"success": True, "sent": {"image": True}})

        with patch.object(mcp_server, "bili_send_message", fake_send):
            raw = asyncio.run(
                mcp_server.bili_send_sticker(receiver_uid=123456789, sticker="骄傲",
                                             text="测试文字")
            )
        result = json.loads(raw)
        self.assertTrue(result["success"])
        self.assertEqual(result["sticker"], "骄傲")
        self.assertEqual(calls[0]["image"], str(self.dir / "骄傲.png"))
        self.assertEqual(calls[0]["text"], "测试文字")
        self.assertNotIn("base64", raw.lower())

    def test_表情库不存在时也不报错(self):
        with patch.object(mcp_server, "STICKER_DIR", self.dir / "nope"):
            result = json.loads(asyncio.run(mcp_server.bili_sticker_list()))
            self.assertEqual(mcp_server._sticker_names(), [])
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
