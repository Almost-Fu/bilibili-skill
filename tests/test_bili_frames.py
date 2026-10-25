"""高清抽帧：时间点、流选择、远程 seek 和临时文件清理。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from PIL import Image

from bili_frames import (
    FramesError,
    _decode_at,
    _extract_and_save,
    _label,
    cleanup_frames,
    cleanup_old_frames,
    fetch_dense_storyboard,
    fetch_frames,
    normalize_times,
    pick_stream,
    rank_streams,
    sample_times,
)
from bili_storyboard import cleanup_storyboard


def stream(height: int, codec: int = 7, host: str = "cdn.example") -> dict:
    return {
        "height": height,
        "width": height * 16 // 9,
        "codecid": codec,
        "bandwidth": height * 1000,
        "baseUrl": f"https://{host}/{height}.m4s",
        "backupUrl": [f"https://backup.example/{height}.m4s"],
    }


class FakeFrame:
    def __init__(self, when: float, size=(640, 360)):
        self.time = when
        self._size = size

    def to_image(self):
        return Image.new("RGB", self._size, (30, 60, 90))


class FakeStream:
    time_base = Fraction(1, 16000)
    width = 640
    height = 360


class FakeContainer:
    def __init__(self, frames):
        self.frames = list(frames)
        self.streams = type("Streams", (), {"video": [FakeStream()]})()
        self.seeks = []
        self.closed = False

    def seek(self, offset, stream=None):
        self.seeks.append((offset, stream))

    def decode(self, stream=None):
        return iter(self.frames)

    def close(self):
        self.closed = True


class NormalizeTests(unittest.TestCase):
    def test_排序去重并截到时长内(self):
        self.assertEqual(
            normalize_times([30, 5, 5.1, 60, 99], duration=83),
            [5, 30, 60],
        )

    def test_负数和非数字被丢掉(self):
        self.assertEqual(normalize_times([-1, "x", None, 3]), [3])

    def test_超过上限时均匀抽(self):
        # 上限可配置，这里固定成 3，只验证"均匀抽"本身
        self.assertEqual(
            normalize_times([0, 10, 20, 30, 40, 50], max_frames=3),
            [0, 20, 50],
        )

    def test_单个数字也能用(self):
        self.assertEqual(normalize_times(7.5, duration=10), [7.5])

    def test_全部无效返回空(self):
        self.assertEqual(normalize_times([100, 200], duration=83), [])


class SampleTimesTests(unittest.TestCase):
    def test_默认约三秒一抽(self):
        self.assertEqual(
            sample_times(9, interval=3),
            [0, 3, 6, 9],
        )
        times = sample_times(83, interval=3)
        self.assertEqual(len(times), 28)
        self.assertEqual(times[:3], [0, 3, 6])
        self.assertEqual(times[-1], 81)

    def test_超过上限时保持首尾(self):
        times = sample_times(100, interval=3, max_frames=10)
        self.assertEqual(len(times), 10)
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 99)

    def test_时长无效返回空(self):
        self.assertEqual(sample_times(0), [])
        self.assertEqual(sample_times(-5), [])


class PickStreamTests(unittest.TestCase):
    def test_同高度优先AVC(self):
        chosen = pick_stream(
            [stream(1080, 12), stream(1080, 7), stream(720, 7)], max_height=1080
        )
        self.assertEqual(chosen["height"], 1080)
        self.assertEqual(chosen["codecid"], 7)

    def test_高度上限生效(self):
        chosen = pick_stream([stream(1080), stream(720), stream(480)], max_height=720)
        self.assertEqual(chosen["height"], 720)

    def test_没有可用流报错(self):
        with self.assertRaises(FramesError):
            pick_stream([stream(1080)], max_height=480)

    def test_没有地址的流跳过(self):
        bad = {"height": 1080, "codecid": 7}
        chosen = pick_stream([bad, stream(480)], max_height=1080)
        self.assertEqual(chosen["height"], 480)

    def test_候选顺序按高度和编码(self):
        ranked = rank_streams(
            [stream(720, 7), stream(1080, 12), stream(1080, 7)],
            max_height=1080,
        )
        self.assertEqual(
            [(item["height"], item["codecid"]) for item in ranked],
            [(1080, 7), (1080, 12), (720, 7)],
        )


class DecodeTests(unittest.TestCase):
    def test_seek按流时间基换算(self):
        container = FakeContainer([FakeFrame(4), FakeFrame(5), FakeFrame(6)])
        frame = _decode_at(container, FakeStream(), 5.0)
        self.assertEqual(frame.time, 5)
        self.assertEqual(container.seeks[0][0], 5 * 16000)

    def test_取第一帧不早于目标(self):
        container = FakeContainer([FakeFrame(4), FakeFrame(5), FakeFrame(6)])
        frame = _decode_at(container, FakeStream(), 5.5)
        self.assertEqual(frame.time, 6)

    def test_没有画面时报错(self):
        with self.assertRaises(FramesError):
            _decode_at(FakeContainer([]), FakeStream(), 5)

    def test_时间标签(self):
        self.assertEqual(_label(5), "00:05")
        self.assertEqual(_label(65.4), "01:05.4")


class SaveAndCleanupTests(unittest.TestCase):
    def test_保存帧并清理(self):
        container = FakeContainer([FakeFrame(5), FakeFrame(6)])
        captured = {}

        def opener(url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return container

        with tempfile.TemporaryDirectory() as tmp:
            result = _extract_and_save(
                [stream(720)],
                [5.0, 6.0],
                max_height=1080,
                base_dir=tmp,
                opener=opener,
            )
            self.assertEqual(len(result["frames"]), 2)
            self.assertIn("Referer:", captured["headers"])
            for frame in result["frames"]:
                self.assertTrue(Path(frame["path"]).is_file())
                with Image.open(frame["path"]) as image:
                    self.assertEqual(image.size, (640, 360))

            self.assertTrue(cleanup_frames(result["cleanup_id"], base_dir=tmp))
            self.assertFalse((Path(tmp) / result["cleanup_id"]).exists())
            # 同一个编号再删一次返回 False，不抛异常
            self.assertFalse(cleanup_frames(result["cleanup_id"], base_dir=tmp))

    def test_一路打不开换下一路(self):
        bad = {"height": 1080, "codecid": 7, "baseUrl": "https://bad/1.m4s"}
        good = stream(1080, 12)
        container = FakeContainer([FakeFrame(5)])
        calls = []

        def opener(url, headers):
            calls.append(url)
            if "bad" in url:
                raise OSError("bad cdn")
            return container

        with tempfile.TemporaryDirectory() as tmp:
            result = _extract_and_save(
                rank_streams([bad, good], max_height=1080),
                [5.0],
                max_height=1080,
                base_dir=tmp,
                opener=opener,
            )
            self.assertEqual(result["stream_codec"], 12)
            self.assertTrue(any("bad" in url for url in calls))
            cleanup_frames(result["cleanup_id"], base_dir=tmp)

    def test_全部时间点失败时删掉空目录(self):
        def opener(url, headers):
            raise OSError("cdn down")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FramesError):
                _extract_and_save(
                    [stream(720)],
                    [5.0],
                    max_height=1080,
                    base_dir=tmp,
                    opener=opener,
                )
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_清理过期目录(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / ("a" * 32)
            old.mkdir()
            (old / "x.jpg").write_bytes(b"x")
            self.assertEqual(cleanup_old_frames(max_age_seconds=0, base_dir=tmp), 1)
            self.assertFalse(old.exists())

    def test_非法编号不删(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(cleanup_frames("../etc", base_dir=tmp))


class DenseSheetTests(unittest.TestCase):
    def test_密集联系表按三秒抽并清掉中间帧(self):
        container = FakeContainer(
            [FakeFrame(0), FakeFrame(3), FakeFrame(6), FakeFrame(9)]
        )

        def opener(url, headers):
            return container

        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                fetch_dense_storyboard(
                    "BV1",
                    1,
                    9,
                    {"dash": {"video": [stream(360)]}},
                    interval=3,
                    base_dir=tmp,
                    opener=opener,
                )
            )
            self.assertEqual(result["source"], "pyav-dense")
            self.assertEqual(result["count"], 4)
            self.assertEqual(
                [item["time"] for item in result["frames"]], [0, 3, 6, 8]
            )
            self.assertTrue(Path(result["sheet_path"]).is_file())
            # 只留下联系表目录，中间帧目录已删除
            dirs = [item for item in Path(tmp).iterdir() if item.is_dir()]
            self.assertEqual(dirs, [Path(tmp) / result["cleanup_id"]])
            cleanup_storyboard(result["cleanup_id"], base_dir=tmp)

    def test_没有视频流时报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FramesError):
                asyncio.run(
                    fetch_dense_storyboard(
                        "BV1", 1, 9, {"dash": {}}, base_dir=tmp
                    )
                )

    def test_只抽指定两分钟窗口(self):
        frames = [FakeFrame(when) for when in range(300, 421, 3)]

        def opener(url, headers):
            return FakeContainer(frames)

        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                fetch_dense_storyboard(
                    "BV1",
                    1,
                    600,
                    {"dash": {"video": [stream(360)]}},
                    interval=3,
                    start=300,
                    end=420,
                    base_dir=tmp,
                    opener=opener,
                )
            )
            self.assertEqual(result["start"], 300)
            self.assertEqual(result["end"], 420)
            self.assertEqual(result["window_seconds"], 120)
            self.assertEqual(result["count"], 40)
            times = [item["time"] for item in result["frames"]]
            self.assertEqual(times[0], 300)
            self.assertEqual(times[-1], 420)
            self.assertTrue(all(300 <= item <= 420 for item in times))
            cleanup_storyboard(result["cleanup_id"], base_dir=tmp)


class FetchTests(unittest.TestCase):
    def test_完整流程返回帧和清理编号(self):
        container = FakeContainer([FakeFrame(5), FakeFrame(6)])

        def opener(url, headers):
            return container

        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                fetch_frames(
                    "BV1",
                    1,
                    83,
                    [6, 5],
                    {"dash": {"video": [stream(720)]}},
                    base_dir=tmp,
                    opener=opener,
                )
            )
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["times"], [5.0, 6.0])
            self.assertEqual(result["source"], "pyav-remote-seek")
            self.assertTrue((Path(tmp) / result["cleanup_id"]).is_dir())
            cleanup_frames(result["cleanup_id"], base_dir=tmp)

    def test_没有DASH流报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FramesError):
                asyncio.run(
                    fetch_frames(
                        "BV1", 1, 83, [5], {"dash": {}}, base_dir=tmp
                    )
                )

    def test_时间点全无效报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FramesError):
                asyncio.run(
                    fetch_frames(
                        "BV1", 1, 83, [100], {"dash": {"video": []}}, base_dir=tmp
                    )
                )


if __name__ == "__main__":
    unittest.main()
