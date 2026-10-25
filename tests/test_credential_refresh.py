"""凭证刷新与持久化测试：假 Credential，不联网、不扫码。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bili_credential import (
    CredentialManager,
    credential_to_dict,
    load_credential,
    persist_credential,
)


def write_cred(path: Path, **overrides) -> None:
    data = {
        "sessdata": "sess-1",
        "bili_jct": "jct-1",
        "dedeuserid": "1001",
        "ac_time_value": "token-1",
        "buvid3": "buvid-1",
        "note": "用户自己加的备注",
    }
    data.update(overrides)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class FakeCredential:
    def __init__(self, *, need=True, check_error=None, refresh_error=None):
        self.sessdata = "sess-1"
        self.bili_jct = "jct-1"
        self.dedeuserid = "1001"
        self.ac_time_value = "token-1"
        self.buvid3 = ""
        self.buvid4 = ""
        self.need = need
        self.check_error = check_error
        self.refresh_error = refresh_error
        self.check_calls = 0
        self.refresh_calls = 0

    async def check_refresh(self) -> bool:
        self.check_calls += 1
        if self.check_error:
            raise self.check_error
        return self.need

    async def refresh(self) -> None:
        self.refresh_calls += 1
        if self.refresh_error:
            raise self.refresh_error
        self.sessdata = "sess-2"
        self.bili_jct = "jct-2"
        self.dedeuserid = "1001"
        self.ac_time_value = "token-2"


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cred.json"

    def test_missing_file(self):
        self.assertIsNone(load_credential(self.path))

    def test_incomplete_file(self):
        write_cred(self.path, bili_jct="")
        self.assertIsNone(load_credential(self.path))

    def test_corrupt_file(self):
        self.path.write_text("{不是 json", encoding="utf-8")
        self.assertIsNone(load_credential(self.path))

    def test_carries_refresh_token(self):
        write_cred(self.path)
        cred = load_credential(self.path)
        self.assertIsNotNone(cred)
        self.assertEqual(cred.ac_time_value, "token-1")
        self.assertEqual(cred.sessdata, "sess-1")
        self.assertEqual(cred.dedeuserid, "1001")


class PersistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cred.json"

    def test_merge_keeps_unknown_fields(self):
        write_cred(self.path)
        cred = FakeCredential()
        cred.sessdata = "sess-new"
        cred.ac_time_value = "token-new"
        persist_credential(cred, self.path)

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["sessdata"], "sess-new")
        self.assertEqual(data["ac_time_value"], "token-new")
        self.assertEqual(data["note"], "用户自己加的备注")
        self.assertEqual(data["buvid3"], "buvid-1")  # 空值不覆盖已有 buvid3

    def test_no_temp_file_left(self):
        write_cred(self.path)
        persist_credential(FakeCredential(), self.path)
        self.assertEqual(
            [p.name for p in self.path.parent.iterdir()], [self.path.name]
        )

    def test_credential_to_dict_skips_empty_buvid(self):
        data = credential_to_dict(FakeCredential())
        self.assertNotIn("buvid3", data)
        self.assertEqual(data["ac_time_value"], "token-1")


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cred.json"
        write_cred(self.path)
        self.now = [1000.0]
        self.manager = CredentialManager(
            self.path,
            refresh_interval=600,
            clock=lambda: self.now[0],
        )

    async def test_refreshes_and_persists(self):
        cred = FakeCredential(need=True)
        await self.manager.maybe_refresh(cred)

        self.assertEqual(cred.refresh_calls, 1)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["sessdata"], "sess-2")
        self.assertEqual(data["bili_jct"], "jct-2")
        self.assertEqual(data["ac_time_value"], "token-2")
        self.assertEqual(data["note"], "用户自己加的备注")
        self.assertGreater(self.manager.last_refresh, 0)
        self.assertEqual(self.manager.last_error, "")

    async def test_no_refresh_when_server_says_no(self):
        cred = FakeCredential(need=False)
        await self.manager.maybe_refresh(cred)
        self.assertEqual(cred.check_calls, 1)
        self.assertEqual(cred.refresh_calls, 0)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["sessdata"], "sess-1")

    async def test_throttles_checks(self):
        first = FakeCredential(need=False)
        await self.manager.maybe_refresh(first)
        self.now[0] += 120
        second = FakeCredential(need=False)
        await self.manager.maybe_refresh(second)
        self.assertEqual(second.check_calls, 0)

        self.now[0] += 600
        third = FakeCredential(need=False)
        await self.manager.maybe_refresh(third)
        self.assertEqual(third.check_calls, 1)

    async def test_force_bypasses_throttle(self):
        await self.manager.maybe_refresh(FakeCredential(need=False))
        forced = FakeCredential(need=False)
        await self.manager.maybe_refresh(forced, force=True)
        self.assertEqual(forced.check_calls, 1)

    async def test_check_failure_keeps_old_credential(self):
        cred = FakeCredential(check_error=RuntimeError("network down"))
        result = await self.manager.maybe_refresh(cred)
        self.assertIs(result, cred)
        self.assertEqual(cred.refresh_calls, 0)
        self.assertIn("network down", self.manager.last_error)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["sessdata"], "sess-1")

    async def test_refresh_failure_keeps_old_credential(self):
        cred = FakeCredential(refresh_error=RuntimeError("-101 账号未登录"))
        result = await self.manager.maybe_refresh(cred)
        self.assertIs(result, cred)
        self.assertEqual(cred.refresh_calls, 1)
        self.assertIn("-101", self.manager.last_error)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["sessdata"], "sess-1")

    async def test_no_refresh_token_skips_network(self):
        cred = FakeCredential()
        cred.ac_time_value = ""
        await self.manager.maybe_refresh(cred)
        self.assertEqual(cred.check_calls, 0)
        self.assertEqual(cred.refresh_calls, 0)

    async def test_get_returns_none_without_file(self):
        self.path.unlink()
        self.assertIsNone(await self.manager.get())

    async def test_get_refreshes_once_per_wake(self):
        # get() 每次都重新读文件；刷新后同一轮里的后续调用读到的是新 cookie
        manager = CredentialManager(
            self.path, refresh_interval=600, clock=lambda: self.now[0]
        )
        # 用假对象替换 load，模拟服务器说“该刷新了”
        fake = FakeCredential(need=True)
        manager.load = lambda: fake  # type: ignore[method-assign]
        cred = await manager.get()
        self.assertEqual(cred.sessdata, "sess-2")
        self.assertEqual(fake.refresh_calls, 1)

        self.now[0] += 60
        again = await manager.get()
        self.assertEqual(again.sessdata, "sess-2")
        self.assertEqual(fake.check_calls, 1)


if __name__ == "__main__":
    unittest.main()
