import json
import tempfile
import unittest
from pathlib import Path

import httpx

from bili_auth import (
    POLL_URL,
    QrLogin,
    _credential_from_cookies,
    _cookies_from_url,
    _merge_cookie_headers,
    save_credential,
)


class CookieParsingTests(unittest.TestCase):
    def test_merges_set_cookie_headers(self):
        cookies: dict[str, str] = {}
        _merge_cookie_headers(
            [
                "SESSDATA=abc%2Cdef; Path=/; Domain=.bilibili.com; HttpOnly",
                "bili_jct=token; Path=/",
                "DedeUserID=42; Path=/",
                "buvid3=b3; Path=/",
            ],
            cookies,
        )
        self.assertEqual(cookies["SESSDATA"], "abc%2Cdef")
        self.assertEqual(cookies["bili_jct"], "token")
        self.assertEqual(cookies["DedeUserID"], "42")
        self.assertEqual(cookies["buvid3"], "b3")

    def test_old_style_url_query(self):
        cookies: dict[str, str] = {}
        _cookies_from_url(
            "https://passport.biligame.com/crossDomain"
            "?SESSDATA=s&bili_jct=j&DedeUserID=9",
            cookies,
        )
        self.assertEqual(cookies["SESSDATA"], "s")
        self.assertEqual(cookies["DedeUserID"], "9")

    def test_credential_requires_all_three_fields(self):
        self.assertIsNone(_credential_from_cookies({"SESSDATA": "s"}))
        cred = _credential_from_cookies(
            {"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "9"}, "rt"
        )
        self.assertEqual(cred["sessdata"], "s")
        self.assertEqual(cred["bili_jct"], "j")
        self.assertEqual(cred["dedeuserid"], "9")
        self.assertEqual(cred["ac_time_value"], "rt")

    def test_cookie_names_are_case_insensitive(self):
        cred = _credential_from_cookies(
            {"sessdata": "s", "BILI_JCT": "j", "dedeuserid": "9"}
        )
        self.assertEqual(cred["bili_jct"], "j")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


class PollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.login = QrLogin()

    async def asyncTearDown(self):
        await self.login.close()

    async def _install(self, handler):
        await self.login.client.aclose()
        self.login.client = _client(handler)
        self.login.qrcode_key = "key"

    async def test_success_reads_set_cookie_headers(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url).split("?")[0], POLL_URL)
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "SESSDATA=sess; Path=/; Domain=.bilibili.com"),
                    ("set-cookie", "bili_jct=csrf; Path=/; Domain=.bilibili.com"),
                    ("set-cookie", "DedeUserID=123; Path=/; Domain=.bilibili.com"),
                ],
                json={"code": 0, "data": {"code": 0, "refresh_token": "rt"}},
            )

        await self._install(handler)
        state = await self.login.poll()
        self.assertEqual(state.status, "done")
        self.assertEqual(state.credential["sessdata"], "sess")
        self.assertEqual(state.credential["bili_jct"], "csrf")
        self.assertEqual(state.credential["dedeuserid"], "123")
        self.assertEqual(state.credential["ac_time_value"], "rt")

    async def test_success_reads_old_style_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "code": 0,
                        "url": "https://passport.biligame.com/crossDomain"
                        "?SESSDATA=s&bili_jct=j&DedeUserID=9",
                    },
                },
            )

        await self._install(handler)
        state = await self.login.poll()
        self.assertEqual(state.status, "done")
        self.assertEqual(state.credential["sessdata"], "s")

    async def test_follows_jump_url_for_cookies(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/jump":
                return httpx.Response(
                    200,
                    headers=[
                        ("set-cookie", "SESSDATA=s; Domain=.bilibili.com; Path=/"),
                        ("set-cookie", "bili_jct=j; Domain=.bilibili.com; Path=/"),
                        ("set-cookie", "DedeUserID=9; Domain=.bilibili.com; Path=/"),
                    ],
                    text="ok",
                )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"code": 0, "url": "https://www.bilibili.com/jump"}},
            )

        await self._install(handler)
        state = await self.login.poll()
        self.assertEqual(state.status, "done")
        self.assertEqual(state.credential["dedeuserid"], "9")

    async def test_reports_error_when_credentials_missing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"code": 0}})

        await self._install(handler)
        state = await self.login.poll()
        self.assertEqual(state.status, "error")
        self.assertIn("SESSDATA", state.message)

    async def test_waiting_and_confirming(self):
        states = iter([86101, 86090])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"code": 0, "data": {"code": next(states)}}
            )

        await self._install(handler)
        self.assertEqual((await self.login.poll()).status, "waiting")
        self.assertEqual((await self.login.poll()).status, "confirming")

    async def test_http_error_is_returned_not_raised(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        await self._install(handler)
        state = await self.login.poll()
        self.assertEqual(state.status, "error")


class SaveCredentialTests(unittest.TestCase):
    def test_writes_json_without_printing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cred.json"
            saved = save_credential(
                {"sessdata": "s", "bili_jct": "j", "dedeuserid": "9"}, path
            )
            self.assertEqual(saved, path)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["sessdata"], "s"
            )


if __name__ == "__main__":
    unittest.main()
