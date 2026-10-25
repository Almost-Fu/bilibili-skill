"""用户动态归一化。"""

from __future__ import annotations

import unittest

from bili_dynamic import normalize_dynamics


def _item(**overrides):
    item = {
        "id_str": "1",
        "type": "DYNAMIC_TYPE_DRAW",
        "modules": {
            "module_author": {
                "name": "影视飓风",
                "mid": 946974,
                "pub_ts": "1792659310",
                "pub_time": "2天前",
            },
            "module_dynamic": {
                "desc": None,
                "major": {
                    "type": "MAJOR_TYPE_OPUS",
                    "opus": {
                        "summary": {"text": "一条图文动态"},
                        "pics": [
                            {"url": "http://i0.hdslb.com/a.jpg"},
                            {"url": "http://i0.hdslb.com/b.jpg"},
                        ],
                        "jump_url": "//www.bilibili.com/opus/1",
                    },
                },
            },
            "module_stat": {
                "like": {"count": 10},
                "comment": {"count": 2},
                "forward": {"count": 1},
            },
        },
    }
    item.update(overrides)
    return item


class NormalizeTests(unittest.TestCase):
    def test_opus_dynamic(self):
        items = normalize_dynamics({"items": [_item()]})
        self.assertEqual(len(items), 1)
        entry = items[0]
        self.assertEqual(entry["author"], "影视飓风")
        self.assertEqual(entry["author_uid"], 946974)
        self.assertEqual(entry["text"], "一条图文动态")
        self.assertEqual(
            entry["pictures"],
            ["http://i0.hdslb.com/a.jpg", "http://i0.hdslb.com/b.jpg"],
        )
        self.assertEqual(entry["stats"], {"like": 10, "comment": 2, "forward": 1})
        self.assertEqual(entry["url"], "https://www.bilibili.com/opus/1")

    def test_video_dynamic_carries_cover(self):
        item = _item(
            type="DYNAMIC_TYPE_AV",
            modules={
                "module_author": {"name": "UP", "mid": 1, "pub_ts": "100"},
                "module_dynamic": {
                    "desc": {"text": "新视频"},
                    "major": {
                        "type": "MAJOR_TYPE_ARCHIVE",
                        "archive": {
                            "bvid": "BV1xx",
                            "title": "视频标题",
                            "cover": "http://i0.hdslb.com/cover.jpg",
                        },
                    },
                },
            },
        )
        entry = normalize_dynamics({"items": [item]})[0]
        self.assertEqual(entry["text"], "新视频")
        self.assertEqual(entry["video"], {"bvid": "BV1xx", "title": "视频标题"})
        self.assertEqual(entry["pictures"], ["http://i0.hdslb.com/cover.jpg"])

    def test_forward_includes_original(self):
        item = _item(
            type="DYNAMIC_TYPE_FORWARD",
            orig={
                "modules": {
                    "module_author": {"name": "原作者"},
                    "module_dynamic": {
                        "desc": {"text": "原动态正文"},
                        "major": {
                            "opus": {
                                "pics": [{"url": "http://i0.hdslb.com/orig.jpg"}]
                            }
                        },
                    },
                }
            },
        )
        entry = normalize_dynamics({"items": [item]})[0]
        self.assertEqual(entry["forwarded_from"], "原作者")
        self.assertEqual(entry["forwarded_text"], "原动态正文")
        self.assertIn("http://i0.hdslb.com/orig.jpg", entry["pictures"])

    def test_limit_and_bad_payload(self):
        self.assertEqual(normalize_dynamics(None), [])
        self.assertEqual(normalize_dynamics({"items": "bad"}), [])
        items = normalize_dynamics({"items": [_item(), _item(), _item()]}, limit=2)
        self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
