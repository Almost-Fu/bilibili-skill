"""私信归一化与未读会话提取。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bili_dm import (
    fetch_unread_sessions,
    normalize_messages,
    normalize_sessions,
    render_transcript,
)


class DummyCredential:
    dedeuserid = "42"


SESSIONS = {
    "data": {
        "session_list": [
            {
                "talker_id": 123,
                "unread_count": 2,
                "session_ts": 1700,
                "last_msg": {
                    "sender_uid": 123,
                    "timestamp": 1700,
                    "content": json.dumps({"content": "在吗"}),
                },
            },
            {
                "talker_id": 456,
                "unread_count": 0,
                "session_ts": 1600,
                "last_msg": {"sender_uid": 456, "timestamp": 1600},
            },
            {
                "talker_id": 789,
                "unread_count": 1,
                "last_msg": {
                    "sender_uid": 42,
                    "timestamp": 1500,
                    "content": json.dumps({"content": "我自己发的"}),
                },
            },
        ]
    }
}

MESSAGES = {
    "data": {
        "messages": [
            {
                "sender_uid": 123,
                "msg_type": 1,
                "content": json.dumps({"content": "你好"}),
                "timestamp": 1690,
            },
            {
                "sender_uid": 42,
                "msg_type": 1,
                "content": json.dumps({"content": "在的"}),
                "timestamp": 1695,
            },
        ]
    }
}


class NormalizeTests(unittest.TestCase):
    def test_sessions(self):
        items = normalize_sessions(SESSIONS, self_uid=42)
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["uid"], 123)
        self.assertEqual(first["unread"], 2)
        self.assertEqual(first["last_ts"], 1700)
        self.assertEqual(first["last_text"], "在吗")
        self.assertFalse(first["last_from_self"])
        # 最后一条是自己发的，不算对方新消息
        self.assertTrue(items[2]["last_from_self"])

    def test_messages(self):
        items = normalize_messages(MESSAGES, self_uid=42)
        self.assertEqual([item["text"] for item in items], ["你好", "在的"])
        self.assertFalse(items[0]["from_self"])
        self.assertTrue(items[1]["from_self"])

    def test_non_text_message(self):
        items = normalize_messages(
            {"data": {"messages": [{"sender_uid": 1, "msg_type": 2}]}},
            self_uid=42,
        )
        self.assertIn("非文本消息", items[0]["text"])
        self.assertEqual(items[0]["images"], [])

    def test_图片消息(self):
        items = normalize_messages(
            {
                "data": {
                    "messages": [
                        {
                            "sender_uid": 1,
                            "msg_type": 2,
                            "content": json.dumps(
                                {
                                    "height": 400,
                                    "width": 400,
                                    "url": "https://message.biliimg.com/a.jpg",
                                }
                            ),
                        }
                    ]
                }
            },
            self_uid=42,
        )
        self.assertEqual(items[0]["text"], "[图片]")
        self.assertEqual(
            items[0]["images"], ["https://message.biliimg.com/a.jpg"]
        )

    def test_transcript_marks_images(self):
        text = render_transcript(
            [
                {
                    "uid": 1,
                    "unread": 1,
                    "messages": [
                        {"text": "[图片]", "images": ["https://x/a.jpg"]}
                    ],
                }
            ]
        )
        self.assertIn("对方：[图片]（含图片）", text)

    def test_system_notice_is_flagged(self):
        payload = {
            "data": {
                "session_list": [
                    {
                        "talker_id": 844424930131964,
                        "unread_count": 1,
                        "system_msg_type": 7,
                        "red_alert_text": "[账号登录通知]",
                    },
                    {"talker_id": 123, "unread_count": 1},
                ]
            }
        }
        items = normalize_sessions(payload, self_uid=42)
        self.assertTrue(items[0]["system"])
        self.assertFalse(items[1]["system"])

    def test_render_transcript_fences_nothing_but_labels(self):
        sessions = [
            {
                "uid": 123,
                "unread": 2,
                "messages": normalize_messages(MESSAGES, self_uid=42),
            }
        ]
        text = render_transcript(sessions)
        self.assertIn("会话 UID 123", text)
        self.assertIn("对方：你好", text)
        self.assertIn("我：在的", text)


class FetchTests(unittest.TestCase):
    def test_only_unread_incoming_sessions_are_fetched(self):
        get_sessions = AsyncMock(return_value=SESSIONS)
        fetch_msgs = AsyncMock(return_value=MESSAGES)
        with patch("bilibili_api.session.get_sessions", new=get_sessions), patch(
            "bilibili_api.session.fetch_session_msgs", new=fetch_msgs
        ):
            sessions = asyncio.run(
                fetch_unread_sessions(DummyCredential(), limit=5)
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["uid"], 123)
        self.assertEqual(len(sessions[0]["messages"]), 2)
        fetch_msgs.assert_awaited_once()


class SystemNoticeTests(unittest.TestCase):
    def test_system_notice_is_not_fetched(self):
        payload = {
            "data": {
                "session_list": [
                    {
                        "talker_id": 844424930131964,
                        "unread_count": 1,
                        "system_msg_type": 7,
                        "last_msg": {
                            "sender_uid": 844424930131964,
                            "timestamp": 1700,
                            "content": json.dumps({"text": "登录通知"}),
                        },
                    },
                    {
                        "talker_id": 123,
                        "unread_count": 1,
                        "last_msg": {
                            "sender_uid": 123,
                            "timestamp": 1690,
                            "content": json.dumps({"content": "在吗"}),
                        },
                    },
                ]
            }
        }
        get_sessions = AsyncMock(return_value=payload)
        fetch_msgs = AsyncMock(return_value=MESSAGES)
        with patch("bilibili_api.session.get_sessions", new=get_sessions), patch(
            "bilibili_api.session.fetch_session_msgs", new=fetch_msgs
        ):
            sessions = asyncio.run(fetch_unread_sessions(DummyCredential(), limit=5))
        self.assertEqual([item["uid"] for item in sessions], [123])
        fetch_msgs.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
