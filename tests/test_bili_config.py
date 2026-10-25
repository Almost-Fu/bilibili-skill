"""可调参数的取值优先级：环境变量 > config.json > 代码默认值。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bili_config
import mcp_server

ROOT = Path(__file__).resolve().parent.parent


class BiliConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        bili_config.clear_cache()
        self.addCleanup(bili_config.clear_cache)
        patcher = patch.dict(
            os.environ, {"BILI_CONFIG_PATH": str(self.path)}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, values: object) -> None:
        self.path.write_text(
            json.dumps(values) if not isinstance(values, str) else values,
            encoding="utf-8",
        )
        bili_config.clear_cache()

    def test_没有配置文件时用默认值(self):
        self.assertEqual(bili_config.get_int("BILI_X", 5), 5)
        self.assertEqual(bili_config.get_float("BILI_F", 2.5), 2.5)
        self.assertTrue(bili_config.get_bool("BILI_B", True))
        self.assertEqual(bili_config.get_str("BILI_S", "abc"), "abc")

    def test_config_json可以改值(self):
        self.write({"BILI_X": 9, "BILI_F": 1.25, "BILI_B": True, "BILI_S": "hi"})
        self.assertEqual(bili_config.get_int("BILI_X", 5), 9)
        self.assertEqual(bili_config.get_float("BILI_F", 2.5), 1.25)
        self.assertTrue(bili_config.get_bool("BILI_B", False))
        self.assertEqual(bili_config.get_str("BILI_S", "abc"), "hi")

    def test_环境变量优先于config_json(self):
        self.write({"BILI_X": 9})
        with patch.dict(os.environ, {"BILI_X": "30"}):
            self.assertEqual(bili_config.get_int("BILI_X", 5), 30)

    def test_空白环境变量不算覆盖(self):
        self.write({"BILI_X": 9})
        with patch.dict(os.environ, {"BILI_X": "   "}):
            self.assertEqual(bili_config.get_int("BILI_X", 5), 9)

    def test_非法值回退默认值而不是config(self):
        self.write({"BILI_X": 9})
        with patch.dict(os.environ, {"BILI_X": "abc"}):
            self.assertEqual(bili_config.get_int("BILI_X", 5), 5)

    def test_小数不能当整数用(self):
        self.write({"BILI_X": "3.5"})
        self.assertEqual(bili_config.get_int("BILI_X", 5), 5)

    def test_整数和浮点都会夹到范围内(self):
        self.write({"BILI_X": 999, "BILI_F": 0.1})
        self.assertEqual(bili_config.get_int("BILI_X", 5, minimum=1, maximum=100), 100)
        self.assertEqual(
            bili_config.get_float("BILI_F", 2.5, minimum=0.5, maximum=60.0), 0.5
        )
        with patch.dict(os.environ, {"BILI_X": "-5"}):
            self.assertEqual(bili_config.get_int("BILI_X", 5, minimum=1), 1)

    def test_nan回退默认值(self):
        with patch.dict(os.environ, {"BILI_F": "nan"}):
            self.assertEqual(bili_config.get_float("BILI_F", 2.5), 2.5)

    def test_布尔值写法(self):
        for raw, expected in (
            ("1", True),
            ("true", True),
            (" YES ", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("off", False),
        ):
            with self.subTest(raw=raw), patch.dict(os.environ, {"BILI_B": raw}):
                self.assertIs(bili_config.get_bool("BILI_B", not expected), expected)
        with patch.dict(os.environ, {"BILI_B": "maybe"}):
            self.assertTrue(bili_config.get_bool("BILI_B", True))

    def test_损坏的配置文件当空处理(self):
        self.write("{ this is not json")
        self.assertEqual(bili_config.get_int("BILI_X", 5), 5)
        self.write("[1, 2, 3]")
        self.assertEqual(bili_config.get_int("BILI_X", 5), 5)

    def test_读取有缓存_清掉后才重新解析(self):
        self.write({"BILI_X": 9})
        self.assertEqual(bili_config.get_int("BILI_X", 5), 9)
        self.path.write_text(json.dumps({"BILI_X": 7}), encoding="utf-8")
        self.assertEqual(bili_config.get_int("BILI_X", 5), 9)
        bili_config.clear_cache()
        self.assertEqual(bili_config.get_int("BILI_X", 5), 7)

    def test_超长视频上限从config读取(self):
        self.write({"BILI_MAX_VIDEO_MINUTES": 42})
        self.assertEqual(mcp_server._max_video_minutes(), 42)
        data = json.loads(mcp_server._video_duration_limit_error(43 * 60))
        self.assertEqual(data["max_duration_minutes"], 42)

    def test_模块常量按配置生效(self):
        self.write(
            {
                "BILI_FRAMES_MAX": 6,
                "BILI_DENSE_INTERVAL_SECONDS": 2.5,
                "BILI_STORYBOARD_MAX_FRAMES": 30,
                "BILI_WINDOW_SECONDS": 90,
            }
        )
        code = (
            "import json, bili_frames, bili_storyboard;"
            "print(json.dumps([bili_frames.MAX_FRAMES,"
            " bili_frames.DENSE_INTERVAL,"
            " bili_storyboard.MAX_FRAMES,"
            " bili_storyboard.WINDOW_SECONDS]))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            env=dict(os.environ),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout), [6, 2.5, 30, 90.0])


if __name__ == "__main__":
    unittest.main()
