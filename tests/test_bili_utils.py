"""共享小工具测试：数字转换与 JSON 原子读写。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bili_utils import read_json, to_int, to_nonzero_int, write_json_atomic


class ToIntTests(unittest.TestCase):
    def test_保留零(self):
        self.assertEqual(to_int(0), 0)
        self.assertEqual(to_int("0"), 0)
        self.assertEqual(to_int(" 42 "), 42)

    def test_非法值返回空(self):
        self.assertIsNone(to_int(None))
        self.assertIsNone(to_int(""))
        self.assertIsNone(to_int("abc"))
        self.assertIsNone(to_int([]))

    def test_非零版本把零当缺失(self):
        self.assertIsNone(to_nonzero_int(0))
        self.assertIsNone(to_nonzero_int("0"))
        self.assertEqual(to_nonzero_int("42"), 42)


class JsonFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state.json"

    def test_写读往返(self):
        write_json_atomic(self.path, {"uids": [1, 2], "name": "测试账号"})
        self.assertEqual(
            read_json(self.path), {"uids": [1, 2], "name": "测试账号"}
        )
        self.assertFalse(self.path.with_name("state.json.tmp").exists())

    def test_覆盖旧内容(self):
        write_json_atomic(self.path, {"v": 1})
        write_json_atomic(self.path, {"v": 2})
        self.assertEqual(read_json(self.path), {"v": 2})

    def test_不存在或坏掉返回空(self):
        self.assertIsNone(read_json(self.path))
        self.path.write_text("not json", encoding="utf-8")
        self.assertIsNone(read_json(self.path))

    def test_自动建父目录(self):
        target = Path(self._tmp.name) / "nested" / "state.json"
        write_json_atomic(target, [1])
        self.assertEqual(read_json(target), [1])


if __name__ == "__main__":
    unittest.main()
