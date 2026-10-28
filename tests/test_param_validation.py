"""分页/数量参数的下界保护：负数不能变成“从末尾切片”或原始接口错误。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import mcp_server


class FakeDanmakuClient:
    async def get_danmakus(self, page_index: int = 0):
        return [
            SimpleNamespace(text="第一条", dm_time=1.0),
            SimpleNamespace(text="第二条", dm_time=2.0),
        ]


class ParamValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_搜索负数数量按1条返回(self):
        async def fake_search(**kwargs):
            return {"result": [{"bvid": f"BV{index}"} for index in range(5)]}

        with patch.object(mcp_server.search, "search_by_type", fake_search):
            data = json.loads(
                await mcp_server.bili_search("Python", num=-1)
            )

        self.assertEqual(data["count"], 1)

    async def test_弹幕负数数量按1条返回(self):
        with patch.object(
            mcp_server,
            "_open_video",
            AsyncMock(return_value=(None, FakeDanmakuClient(), {})),
        ):
            data = json.loads(
                await mcp_server.bili_danmaku("BV1xx411c7mD", num=-1)
            )

        self.assertEqual(data["count"], 1)

    async def test_热门视频负数页码和数量回到1(self):
        captured: dict = {}

        async def fake_hot(**kwargs):
            captured.update(kwargs)
            return {"list": []}

        with patch.object(mcp_server.hot, "get_hot_videos", fake_hot):
            await mcp_server.bili_hot_videos(pn=-1, ps=-1)

        self.assertEqual(captured, {"pn": 1, "ps": 1})

    async def test_热搜负数页码和数量回到1(self):
        captured: dict = {}

        async def fake_buzzwords(**kwargs):
            captured.update(kwargs)
            return {}

        with patch.object(
            mcp_server.hot, "get_hot_buzzwords", fake_buzzwords
        ):
            await mcp_server.bili_hot_buzzwords(page_num=-1, page_size=-1)

        self.assertEqual(captured, {"page_num": 1, "page_size": 1})


if __name__ == "__main__":
    unittest.main()
