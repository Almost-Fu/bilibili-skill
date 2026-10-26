"""未登录读权限：公开内容照常可看，写入和私信仍然要求登录。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

import mcp_server
from bilibili_api.exceptions import CredentialNoSessdataException


class FakeVideo:
    def __init__(self, *, bvid=None, aid=None, credential=None):
        self.bvid = bvid
        self.aid = aid
        self.credential = credential

    async def get_info(self):
        return {
            "bvid": self.bvid or "BV1xx411c7mD",
            "aid": self.aid or 1,
            "title": "测试视频",
            "cid": 123,
            "owner": {"name": "UP", "mid": 1},
            "pages": [],
            "stat": {},
            "duration": 60,
        }

    async def get_subtitle(self, cid=None):
        raise CredentialNoSessdataException()


class NoCredential:
    """模拟未登录：CredentialManager.get() 返回 None。"""

    def __enter__(self):
        self._patch = patch.object(
            mcp_server._credentials, "get", AsyncMock(return_value=None)
        )
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class PublicAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_未登录时公开凭证为空而不是报错(self):
        with NoCredential():
            cred = await mcp_server.get_cred_optional()
        self.assertIsInstance(cred, mcp_server.Credential)
        self.assertFalse(cred.sessdata)

    async def test_需要登录的工具仍然给出登录提示(self):
        with NoCredential():
            with self.assertRaises(Exception) as ctx:
                await mcp_server.get_cred()
        self.assertIn("bili_login", str(ctx.exception))

    async def test_未登录可以看视频详情(self):
        with NoCredential(), patch.object(
            mcp_server.video, "Video", FakeVideo
        ):
            data = json.loads(
                await mcp_server.bili_video_info("BV1xx411c7mD")
            )
        self.assertEqual(data["title"], "测试视频")

    async def test_未登录读字幕返回结构化登录提示(self):
        with NoCredential(), patch.object(
            mcp_server.video, "Video", FakeVideo
        ):
            data = json.loads(await mcp_server.bili_subtitle("BV1xx411c7mD"))
        self.assertTrue(data["login_required"])
        self.assertIn("登录", data["message"])

    async def test_收藏夹查询未登录时不直接报错(self):
        listing = AsyncMock(
            return_value={"list": [{"id": 1, "title": "公开收藏夹"}]}
        )
        with NoCredential(), patch.object(
            mcp_server.favorite_list, "get_video_favorite_list", listing
        ):
            data = json.loads(await mcp_server.bili_favorite_lists(uid=123))
        self.assertEqual(data["uid"], 123)
        self.assertEqual(data["count"], 1)

    async def test_收藏夹列表为空时提示登录(self):
        # 未登录时接口返回 data=null，不能当成“没有收藏夹”。
        listing = AsyncMock(return_value=None)
        with NoCredential(), patch.object(
            mcp_server.favorite_list, "get_video_favorite_list", listing
        ):
            data = json.loads(await mcp_server.bili_favorite_lists(uid=123))
        self.assertTrue(data["login_required"])
        self.assertIn("登录", data["message"])

    async def test_查自己的收藏夹仍然需要登录(self):
        with NoCredential():
            with self.assertRaises(Exception) as ctx:
                await mcp_server.bili_favorite_lists(uid=0)
        self.assertIn("bili_login", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
