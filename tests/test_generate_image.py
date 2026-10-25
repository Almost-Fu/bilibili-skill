"""生成配图与本地形象：绘画 AI 适配层测试（不联网）。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import bili_art
import bili_draw
import mcp_server


def call_tool(**kwargs):
    return json.loads(asyncio.run(mcp_server.bili_generate_image(**kwargs)))


class ClampTests(unittest.TestCase):
    def test_size_is_clamped(self):
        self.assertEqual(bili_draw.clamp_size(0), 512)
        self.assertEqual(bili_draw.clamp_size(4096), 1600)
        self.assertEqual(bili_draw.clamp_size("768"), 768)
        self.assertEqual(bili_draw.clamp_size("bad"), 1024)


class SelfImageViewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(bili_art, "SELF_IMAGE_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_只认形象立绘(self):
        Image.new("RGB", (64, 64), (1, 2, 3)).save(self.tmp / "avatar.png")
        self.assertIsNone(bili_art.self_image_path())
        character = self.tmp / "character.png"
        Image.new("RGB", (64, 64), (4, 5, 6)).save(character)
        self.assertEqual(bili_art.self_image_path(), character)

    def test_读得到文字描述(self):
        (self.tmp / "character.md").write_text("测试用角色描述", encoding="utf-8")
        self.assertEqual(bili_art.self_description(), "测试用角色描述")

    def test_没有描述时返回空串(self):
        self.assertEqual(bili_art.self_description(), "")

    def test_small_image_is_returned_as_is(self):
        small = self.tmp / "avatar.png"
        Image.new("RGB", (64, 64), (1, 2, 3)).save(small)
        self.assertEqual(bili_art.self_image_view(small), small)

    def test_large_image_gets_resized_copy(self):
        big = self.tmp / "avatar.png"
        noisy = Image.frombytes("RGB", (1400, 1400), __import__("os").urandom(1400 * 1400 * 3))
        noisy.save(big)
        self.assertGreater(big.stat().st_size, 1_200_000)

        view = bili_art.self_image_view(big)
        self.assertEqual(view.name, "avatar.view.jpg")
        self.assertTrue(view.is_file())
        with Image.open(view) as img:
            self.assertLessEqual(max(img.size), 1024)


class SelfImageToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(bili_art, "SELF_IMAGE_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_立绘和描述一起返回(self):
        Image.new("RGB", (64, 64), (1, 2, 3)).save(self.tmp / "character.png")
        (self.tmp / "character.md").write_text("测试用角色描述", encoding="utf-8")
        data = json.loads(asyncio.run(mcp_server.bili_self_image()))
        self.assertTrue(data["success"])
        self.assertEqual(Path(data["path"]).name, "character.png")
        self.assertEqual(data["description"], "测试用角色描述")
        self.assertNotIn("url", data)

    def test_什么都没有时报错(self):
        data = json.loads(asyncio.run(mcp_server.bili_self_image()))
        self.assertFalse(data["success"])
        self.assertIn("character", data["error"])


class GenerateToolTests(unittest.TestCase):
    """bili_generate_image 只负责拼提示词和调绘画 AI。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.self_dir = self.tmp / "self"
        self.self_dir.mkdir()
        (self.self_dir / "character.md").write_text(
            "测试用角色描述", encoding="utf-8"
        )
        patcher = patch.object(bili_art, "SELF_IMAGE_DIR", self.self_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.out = self.tmp / "drawn.png"
        Image.new("RGB", (32, 32), (1, 2, 3)).save(self.out)
        self.prompts: list[str] = []
        self.sizes: list[tuple[int, int]] = []

        async def fake_generate(prompt, *, width, height):
            self.prompts.append(prompt)
            self.sizes.append((width, height))
            return self.out

        patcher = patch.object(mcp_server.bili_draw, "generate", fake_generate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_成功时返回本地路径(self):
        data = call_tool(prompt="站在窗边", width=512, height=768)
        self.assertTrue(data["success"])
        self.assertEqual(data["path"], str(self.out))
        self.assertEqual((data["width"], data["height"]), (512, 768))
        self.assertEqual(self.sizes, [(512, 768)])

    def test_提示词带上形象和画面(self):
        call_tool(prompt="朝镜头挥手", mood="calm")
        prompt = self.prompts[0]
        self.assertIn("测试用角色描述", prompt)
        self.assertIn("朝镜头挥手", prompt)
        self.assertIn("安静", prompt)

    def test_没有形象描述时也能生成(self):
        (self.self_dir / "character.md").unlink()
        data = call_tool(prompt="站在窗边")
        self.assertTrue(data["success"])
        self.assertNotIn("测试用角色描述", self.prompts[0])
        self.assertIn("站在窗边", self.prompts[0])

    def test_绘画ai未配置时返回原因(self):
        async def not_configured(prompt, *, width, height):
            raise bili_draw.DrawNotConfigured("绘画 AI 还没接入")

        with patch.object(mcp_server.bili_draw, "generate", not_configured):
            data = call_tool(prompt="测试")
        self.assertFalse(data["success"])
        self.assertIn("绘画 AI", data["error"])

    def test_空提示词被拒绝(self):
        data = call_tool(prompt="   ")
        self.assertFalse(data["success"])
        self.assertIn("prompt", data["error"])
        self.assertEqual(self.prompts, [])

    def test_未知心情回落happy(self):
        data = call_tool(prompt="测试", mood="愤怒")
        self.assertTrue(data["success"])
        self.assertEqual(data["mood"], "happy")


class DrawStubTests(unittest.TestCase):
    def test_未接供应商时明确报错(self):
        with self.assertRaises(bili_draw.DrawNotConfigured):
            asyncio.run(bili_draw.generate("测试", width=512, height=512))

    def test_空提示词(self):
        with self.assertRaises(bili_draw.DrawError):
            asyncio.run(bili_draw.generate("  "))


if __name__ == "__main__":
    unittest.main()
