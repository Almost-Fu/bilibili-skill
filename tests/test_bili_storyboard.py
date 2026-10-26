"""B 站雪碧图联系表：纯函数 + 假客户端的端到端测试。"""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import bili_storyboard as storyboard


def make_sprite(tile_w: int = 48, tile_h: int = 27, total: int = 12) -> Image.Image:
    sprite = Image.new("RGB", (tile_w * 10, tile_h * 10), "black")
    for index in range(total):
        col, row = index % 10, index // 10
        color = ((index * 37) % 256, (index * 91) % 256, (index * 53) % 256)
        tile = Image.new("RGB", (tile_w, tile_h), color)
        draw = ImageDraw.Draw(tile)
        draw.line((4, tile_h - 5, tile_w - 4, 5), fill="white", width=2)
        sprite.paste(tile, (col * tile_w, row * tile_h))
    return sprite


def make_payload(total: int = 12) -> dict:
    return {
        "data": {
            "image": ["//example.com/sprite.jpg"],
            "index": [5 * i for i in range(total)],
            "img_x_len": 10,
            "img_y_len": 10,
            "img_x_size": 48,
            "img_y_size": 27,
        }
    }


class FakeResponse:
    def __init__(self, payload=None, content: bytes = b"", status: int = 200):
        self._payload = payload
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload: dict, sprite: Image.Image):
        self.payload = payload
        buffer = io.BytesIO()
        sprite.save(buffer, format="JPEG", quality=85)
        self.sprite = buffer.getvalue()
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if "videoshot" in url:
            return FakeResponse(payload=self.payload)
        return FakeResponse(content=self.sprite)


class ParseTests(unittest.TestCase):
    def test_解析雪碧图字段(self):
        meta = storyboard.parse_videoshot(make_payload())
        self.assertEqual(meta["x_len"], 10)
        self.assertEqual(meta["y_len"], 10)
        self.assertEqual(meta["x_size"], 48)
        self.assertEqual(meta["y_size"], 27)
        self.assertEqual(meta["images"], ["https://example.com/sprite.jpg"])
        self.assertEqual(len(meta["index"]), 12)

    def test_没有图时报错(self):
        with self.assertRaises(storyboard.StoryboardError):
            storyboard.parse_videoshot({"data": {"image": [], "index": []}})

    def test_index是秒数(self):
        self.assertEqual(
            storyboard.times_from_index([0, 0, 5, 10, 80], duration=83),
            [0, 0, 5, 10, 80],
        )

    def test_index是帧号时按比例映射(self):
        self.assertEqual(
            storyboard.times_from_index([0, 600, 1200, 1800], duration=60),
            [0, 20, 40, 60],
        )

    def test_候选帧的格子位置(self):
        meta = storyboard.parse_videoshot(make_payload())
        candidates = storyboard.build_candidates(meta, duration=83)
        self.assertEqual(len(candidates), 12)
        self.assertEqual((candidates[0].sprite, candidates[0].col, candidates[0].row), (0, 0, 0))
        self.assertEqual((candidates[11].sprite, candidates[11].col, candidates[11].row), (0, 1, 1))

    def test_均匀抽取保序(self):
        picked = storyboard.evenly_spaced(list(range(10)), 4)
        self.assertEqual(picked[0], 0)
        self.assertEqual(picked[-1], 9)
        self.assertEqual(len(picked), 4)
        self.assertEqual(picked, sorted(picked))


class WindowTests(unittest.TestCase):
    def test_默认整段(self):
        self.assertEqual(storyboard.resolve_window(600), (0, 600))

    def test_从start起两分钟(self):
        self.assertEqual(storyboard.resolve_window(600, 300), (300, 420))

    def test_只给end时向前两分钟(self):
        self.assertEqual(storyboard.resolve_window(600, None, 100), (0, 100))
        self.assertEqual(storyboard.resolve_window(600, None, 500), (380, 500))

    def test_窗口超过两分钟被裁剪(self):
        self.assertEqual(storyboard.resolve_window(600, 0, 300), (0, 120))
        self.assertEqual(storyboard.resolve_window(60, 0, 60), (0, 60))

    def test_靠近片尾被裁剪(self):
        self.assertEqual(storyboard.resolve_window(600, 540, 700), (540, 600))

    def test_窗口无效报错(self):
        with self.assertRaises(ValueError):
            storyboard.resolve_window(600, 300, 300)


class ImageTests(unittest.TestCase):
    def test_空白帧识别(self):
        self.assertTrue(storyboard.is_blank(Image.new("RGB", (48, 27), "black")))
        self.assertTrue(storyboard.is_blank(Image.new("RGB", (48, 27), "white")))
        self.assertFalse(storyboard.is_blank(make_sprite().crop((0, 0, 48, 27))))

    def test_相同图哈希相同_不同图哈希不同(self):
        first = make_sprite().crop((0, 0, 48, 27))
        second = make_sprite().crop((48, 0, 96, 27))
        self.assertEqual(storyboard.dhash(first), storyboard.dhash(first.copy()))
        self.assertNotEqual(storyboard.dhash(first), storyboard.dhash(second))

    def test_联系表尺寸和标签(self):
        sprite = make_sprite()
        meta = storyboard.parse_videoshot(make_payload())
        candidates = storyboard.build_candidates(meta, duration=83)[:8]
        frames = [
            storyboard.Frame(
                index=item.index,
                time=item.time,
                image=storyboard.crop_tile(sprite, item, meta),
            )
            for item in candidates
        ]
        sheet, layout = storyboard.build_contact_sheet(frames)
        self.assertEqual(layout["cols"], 4)
        self.assertEqual(layout["rows"], 2)
        self.assertEqual(sheet.width, 4 * 48)
        self.assertEqual(sheet.height, 2 * (27 + layout["label_height"]))

    def test_保存和清理(self):
        sheet = Image.new("RGB", (10, 10), "red")
        with tempfile.TemporaryDirectory() as tmp:
            path, cleanup_id = storyboard.save_contact_sheet(sheet, base_dir=tmp)
            self.assertTrue(Path(path).is_file())
            self.assertTrue((Path(tmp) / cleanup_id).is_dir())
            self.assertTrue(storyboard.cleanup_storyboard(cleanup_id, base_dir=tmp))
            self.assertFalse((Path(tmp) / cleanup_id).exists())

    def test_清理过期目录(self):
        import os
        import time

        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / ("a" * 32)
            new = Path(tmp) / ("b" * 32)
            old.mkdir()
            new.mkdir()
            past = time.time() - 7200
            os.utime(old, (past, past))
            removed = storyboard.cleanup_old_storyboards(base_dir=tmp)
            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())


class FlakyVideoshotClient(FakeClient):
    """前 ``empty_times`` 次返回 200 + 空数据，模拟 B 站偶发状态。"""

    def __init__(self, empty_times: int):
        super().__init__(make_payload(), make_sprite())
        self.empty_times = empty_times
        self.videoshot_calls = 0

    async def get(self, url: str, **kwargs):
        if "videoshot" in url:
            self.videoshot_calls += 1
            self.calls.append((url, kwargs))
            if self.videoshot_calls <= self.empty_times:
                return FakeResponse(payload={"data": {"image": [], "index": []}})
            return FakeResponse(payload=self.payload)
        return await super().get(url, **kwargs)


class FetchTests(unittest.TestCase):
    def _no_retry_delay(self):
        old = storyboard.RETRY_DELAY_SECONDS
        storyboard.RETRY_DELAY_SECONDS = 0
        self.addCleanup(setattr, storyboard, "RETRY_DELAY_SECONDS", old)

    def test_雪碧图空数据时自动重试(self):
        self._no_retry_delay()
        client = FlakyVideoshotClient(empty_times=1)
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                storyboard.fetch_storyboard(
                    "BV1test", 123, max_frames=8, duration=83,
                    client=client, base_dir=tmp,
                )
            )
            self.assertGreater(result["count"], 0)
            self.assertTrue(Path(result["sheet_path"]).is_file())
        self.assertEqual(client.videoshot_calls, 2)

    def test_雪碧图一直为空时重试后抛错(self):
        self._no_retry_delay()
        client = FlakyVideoshotClient(empty_times=99)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(storyboard.StoryboardUnavailable):
                asyncio.run(
                    storyboard.fetch_storyboard(
                        "BV1test", 123, client=client, base_dir=tmp
                    )
                )
        self.assertEqual(client.videoshot_calls, storyboard.VIDEOSHOT_ATTEMPTS)

    def test_端到端拼出联系表(self):
        sprite = make_sprite()
        client = FakeClient(make_payload(), sprite)
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                storyboard.fetch_storyboard(
                    "BV1test",
                    123,
                    max_frames=8,
                    duration=83,
                    client=client,
                    base_dir=tmp,
                )
            )
            self.assertLessEqual(result["count"], 8)
            self.assertTrue(Path(result["sheet_path"]).is_file())
            self.assertTrue((Path(tmp) / result["cleanup_id"]).is_dir())
            times = [item["time"] for item in result["frames"]]
            self.assertEqual(times, sorted(times))
            self.assertEqual(result["source"], "bilibili-videoshot")
            self.assertGreaterEqual(len(client.calls), 2)
            self.assertIn("videoshot", client.calls[0][0])

    def test_雪碧图接口失败时抛错(self):
        client = FakeClient({"code": -404, "message": "啥都木有"}, make_sprite())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(storyboard.StoryboardError):
                asyncio.run(
                    storyboard.fetch_storyboard(
                        "BV1test", 123, client=client, base_dir=tmp
                    )
                )

    def test_只取指定时间窗口的帧(self):
        client = FakeClient(make_payload(), make_sprite())
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                storyboard.fetch_storyboard(
                    "BV1test",
                    123,
                    max_frames=8,
                    duration=83,
                    start=20,
                    end=40,
                    client=client,
                    base_dir=tmp,
                )
            )
            times = [item["time"] for item in result["frames"]]
            self.assertTrue(times)
            self.assertTrue(all(20 <= item <= 40 for item in times))
            self.assertEqual(result["start"], 20)
            self.assertEqual(result["end"], 40)

    def test_字幕摘要(self):
        class FakeVideo:
            async def get_subtitle(self, cid):
                return {
                    "subtitles": [
                        {
                            "lan": "ai-zh",
                            "lan_doc": "中文（AI生成）",
                            "subtitle_url": "//example.com/sub.json",
                        }
                    ]
                }

        class FakeSubtitleClient:
            async def get(self, url, **kwargs):
                return FakeResponse(
                    payload={"body": [{"content": "你好"}, {"content": "世界"}]}
                )

        result = asyncio.run(
            storyboard.fetch_subtitle(
                FakeVideo(), 123, client=FakeSubtitleClient(), max_chars=100
            )
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["text"], "你好\n世界")
        self.assertEqual(result["segments"], 2)
        self.assertFalse(result["truncated"])

    def test_未登录字幕返回登录提示(self):
        class CredentialNoSessdataException(Exception):
            pass

        class FakeVideo:
            async def get_subtitle(self, cid):
                raise CredentialNoSessdataException("没有 sessdata")

        result = asyncio.run(
            storyboard.fetch_subtitle(FakeVideo(), 123, max_chars=100)
        )
        self.assertFalse(result["available"])
        self.assertTrue(result["login_required"])
        self.assertIn("登录", result["message"])
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
