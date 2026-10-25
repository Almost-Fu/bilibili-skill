"""出站图片白名单：只允许表情包和绘画 AI 产物。不联网。"""

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


def png(path: Path, size: tuple[int, int] = (16, 16)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (1, 2, 3)).save(path)
    return path


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.stickers = self.root / "stickers"
        self.drawn = self.root / "drawn"
        self.other = self.root / "other"
        for directory in (self.stickers, self.drawn, self.other):
            directory.mkdir()
        for target, value in (
            ("STICKER_DIR", self.stickers),
            ("DRAWN_DIR", self.drawn),
        ):
            patcher = patch.object(bili_images, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_表情包允许(self):
        path = png(self.stickers / "骄傲.png")
        self.assertEqual(bili_images.resolve_outbound_image(str(path)), path)

    def test_绘画产物允许(self):
        path = png(self.drawn / "dynamic.png")
        self.assertEqual(bili_images.resolve_outbound_image(str(path)), path)

    def test_子目录也允许(self):
        path = png(self.drawn / "2026" / "a.jpg")
        self.assertEqual(bili_images.resolve_outbound_image(str(path)), path)

    def test_表情名可以直接用(self):
        path = png(self.stickers / "暗中观察.png")
        expected = path.resolve()
        for value in ("暗中观察", "暗中观察.png", " 暗中观察 "):
            with self.subTest(value=value):
                self.assertEqual(
                    bili_images.resolve_outbound_image(value), expected
                )

    def test_表情名忽略大小写(self):
        path = png(self.stickers / "Happy.png")
        self.assertEqual(
            bili_images.resolve_outbound_image("happy"), path.resolve()
        )

    def test_不存在的表情名提示先看列表(self):
        with self.assertRaises(bili_images.ImageSourceError) as ctx:
            bili_images.resolve_outbound_image("不存在的表情")
        self.assertIn("bili_sticker_list", str(ctx.exception))

    def test_其他目录不能靠名字混进来(self):
        png(self.other / "a.png")
        with self.assertRaises(bili_images.ImageSourceError) as ctx:
            bili_images.resolve_outbound_image("a")
        self.assertIn("表情包里没有", str(ctx.exception))

    def test_网络图片拒绝(self):
        for url in ("https://x/a.jpg", "http://x/a.jpg", "//x/a.jpg"):
            with self.subTest(url=url):
                with self.assertRaises(bili_images.ImageSourceError) as ctx:
                    bili_images.resolve_outbound_image(url)
                self.assertIn("网络图片", str(ctx.exception))

    def test_其他本地目录拒绝(self):
        path = png(self.other / "a.png")
        with self.assertRaises(bili_images.ImageSourceError) as ctx:
            bili_images.resolve_outbound_image(str(path))
        self.assertIn("来源不允许", str(ctx.exception))

    def test_路径穿越按真实位置判断(self):
        png(self.other / "a.png")
        with self.assertRaises(bili_images.ImageSourceError):
            bili_images.resolve_outbound_image(
                str(self.stickers / ".." / "other" / "a.png")
            )

    def test_不存在拒绝(self):
        with self.assertRaises(bili_images.ImageSourceError) as ctx:
            bili_images.resolve_outbound_image(str(self.stickers / "nope.png"))
        self.assertIn("不存在", str(ctx.exception))

    def test_不支持的格式拒绝(self):
        path = self.stickers / "a.gif"
        path.write_bytes(b"GIF89a")
        with self.assertRaises(bili_images.ImageSourceError) as ctx:
            bili_images.resolve_outbound_image(str(path))
        self.assertIn("png", str(ctx.exception))

    def test_空路径拒绝(self):
        with self.assertRaises(bili_images.ImageSourceError):
            bili_images.resolve_outbound_image("   ")


class ToolGuardTests(unittest.TestCase):
    """发动态、专栏、视频封面都必须走同一套白名单。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.stickers = self.root / "stickers"
        self.drawn = self.root / "drawn"
        self.other = self.root / "other"
        for directory in (self.stickers, self.drawn, self.other):
            directory.mkdir()
        for target, value in (
            ("STICKER_DIR", self.stickers),
            ("DRAWN_DIR", self.drawn),
        ):
            patcher = patch.object(bili_images, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.outside = png(self.other / "a.png")
        self.video = self.root / "v.mp4"
        self.video.write_bytes(b"not a real video")

    def test_动态拒绝网络图片(self):
        result = json.loads(asyncio.run(
            mcp_server.bili_send_dynamic("hi", images=["https://x/a.jpg"])
        ))
        self.assertFalse(result["success"])
        self.assertIn("网络图片", result["error"])

    def test_动态拒绝白名单外的本地图片(self):
        result = json.loads(asyncio.run(
            mcp_server.bili_send_dynamic("hi", images=[str(self.outside)])
        ))
        self.assertFalse(result["success"])
        self.assertIn("来源不允许", result["error"])

    def test_专栏拒绝网络图片(self):
        result = json.loads(asyncio.run(
            mcp_server.bili_send_opus("标题", "正文", images=["https://x/a.jpg"])
        ))
        self.assertFalse(result["success"])
        self.assertIn("网络图片", result["error"])

    def test_视频封面拒绝网络图片(self):
        result = json.loads(asyncio.run(
            mcp_server.bili_upload_video(
                str(self.video), "标题", cover_path="https://x/a.jpg"
            )
        ))
        self.assertFalse(result["success"])
        self.assertIn("网络图片", result["error"])

    def test_多p封面拒绝白名单外的图片(self):
        result = json.loads(asyncio.run(
            mcp_server.bili_upload_video_multi(
                [str(self.video)], ["P1"], "标题", cover_path=str(self.outside)
            )
        ))
        self.assertFalse(result["success"])
        self.assertIn("来源不允许", result["error"])


if __name__ == "__main__":
    unittest.main()
