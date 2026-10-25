"""网络重试：只重试本地网络故障，业务错误原样抛出。"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bili_dm
import bili_utils
import mcp_server


class _SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class RetryNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_success_does_not_retry(self):
        calls = []

        async def operation():
            calls.append(1)
            return "ok"

        self.assertEqual(await bili_utils.retry_network(operation), "ok")
        self.assertEqual(len(calls), 1)

    async def test_retries_network_error_then_succeeds(self):
        calls = []
        sleep = _SleepRecorder()

        async def operation():
            calls.append(1)
            if len(calls) < 3:
                raise OSError("[Errno 11001] getaddrinfo failed")
            return "ok"

        with patch("bili_utils.asyncio.sleep", sleep):
            result = await bili_utils.retry_network(operation, what="测试请求")

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.delays, [0.8, 1.6])

    async def test_gives_up_after_attempts(self):
        calls = []
        sleep = _SleepRecorder()

        async def operation():
            calls.append(1)
            raise OSError("connection refused")

        with patch("bili_utils.asyncio.sleep", sleep):
            with self.assertRaises(OSError):
                await bili_utils.retry_network(operation, attempts=3)

        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleep.delays), 2)

    async def test_business_error_is_not_retried(self):
        calls = []

        async def operation():
            calls.append(1)
            raise ValueError("风控: -352")

        with self.assertRaises(ValueError):
            await bili_utils.retry_network(operation)

        self.assertEqual(len(calls), 1)

    async def test_attempts_below_one_still_calls_once(self):
        calls = []

        async def operation():
            calls.append(1)
            return 1

        self.assertEqual(await bili_utils.retry_network(operation, attempts=0), 1)
        self.assertEqual(len(calls), 1)


class MessageRetryWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_unread_sessions_retries_get_sessions(self):
        calls = []
        sleep = _SleepRecorder()

        async def fake_get_sessions(credential, session_type=1):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("[Errno 11001] getaddrinfo failed")
            return {"data": {"session_list": []}}

        credential = SimpleNamespace(dedeuserid=42)
        with patch.object(bili_dm.session, "get_sessions", fake_get_sessions):
            with patch("bili_utils.asyncio.sleep", sleep):
                sessions = await bili_dm.fetch_unread_sessions(credential)

        self.assertEqual(sessions, [])
        self.assertEqual(len(calls), 2)

    async def test_unread_messages_tool_survives_one_dns_failure(self):
        calls = []

        async def fake_unread(credential=None):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("[Errno 11001] getaddrinfo failed")
            return {"chat": 1}

        with patch.object(mcp_server, "get_cred", AsyncMock(return_value=object())):
            with patch.object(mcp_server.session, "get_unread_messages", fake_unread):
                with patch("bili_utils.asyncio.sleep", _SleepRecorder()):
                    payload = json.loads(await mcp_server.bili_unread_messages())

        self.assertEqual(payload, {"chat": 1})
        self.assertEqual(len(calls), 2)

    async def test_recent_messages_reports_failure_after_retries(self):
        async def always_fail(credential, session_type=1):
            raise OSError("[Errno 11001] getaddrinfo failed")

        with patch.object(mcp_server, "get_cred", AsyncMock(return_value=object())):
            with patch.object(bili_dm.session, "get_sessions", always_fail):
                with patch("bili_utils.asyncio.sleep", _SleepRecorder()):
                    payload = json.loads(await mcp_server.bili_recent_messages())

        self.assertFalse(payload["success"])
        self.assertIn("getaddrinfo", payload["error"])


if __name__ == "__main__":
    unittest.main()
