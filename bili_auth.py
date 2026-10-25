"""B 站扫码登录（适配 2025 后的 Set-Cookie 下发方式）。

旧版协议把 SESSDATA 放在 poll 响应的 ``data.url`` 查询串里；新版改为在
poll 响应本身通过 ``Set-Cookie`` 下发，``data.url`` 只承担跳转/换票。
``bilibili_api`` 17.4.2 的 ``QrCodeLogin.check_state()`` 只看 JSON、
丢掉了响应头，因此会保存一份空凭证——这里直接自己发请求并合并
Set-Cookie、URL 查询串和跳转后的 cookie jar。

用法::

    python -m bili_auth          # 终端扫码登录，写入 bili_credential.json
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent
CRED_FILE = PROJECT_ROOT / "bili_credential.json"

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

STATUS_TEXT = {
    86101: "waiting",
    86090: "confirming",
    86038: "timeout",
    0: "done",
}


@dataclass
class LoginState:
    status: str
    message: str = ""
    url: str = ""
    credential: dict[str, Any] | None = None
    cookies_seen: list[str] = field(default_factory=list)


def _merge_cookie_headers(headers: list[str], into: dict[str, str]) -> None:
    for header in headers:
        try:
            jar = SimpleCookie()
            jar.load(header)
        except Exception:
            continue
        for key, morsel in jar.items():
            if morsel.value:
                into[key] = morsel.value


def _cookies_from_url(url: str, into: dict[str, str]) -> None:
    if not url:
        return
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return
    for key, values in query.items():
        if values and values[0]:
            into.setdefault(key, values[0])


def _pick(cookies: dict[str, str], *names: str) -> str:
    wanted = {name.lower() for name in names}
    for key, value in cookies.items():
        if key.lower() in wanted and value:
            return value
    return ""


def _credential_from_cookies(
    cookies: dict[str, str], refresh_token: str = ""
) -> dict[str, Any] | None:
    sessdata = _pick(cookies, "SESSDATA")
    bili_jct = _pick(cookies, "bili_jct")
    dedeuserid = _pick(cookies, "DedeUserID", "dedeuserid")
    if not sessdata or not bili_jct or not dedeuserid:
        return None
    return {
        "sessdata": sessdata,
        "bili_jct": bili_jct,
        "buvid3": _pick(cookies, "buvid3"),
        "dedeuserid": dedeuserid,
        "ac_time_value": refresh_token,
    }


class QrLogin:
    """一次扫码登录会话。每次 ``generate()`` 后循环调用 ``poll()``。"""

    def __init__(self, *, timeout: float = 20.0):
        self.client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=timeout
        )
        self.qrcode_key = ""
        self.login_url = ""

    async def close(self) -> None:
        await self.client.aclose()

    async def generate(self) -> LoginState:
        try:
            response = await self.client.get(GENERATE_URL)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return LoginState("error", message=f"生成二维码请求失败：{exc}")
        if payload.get("code") != 0:
            return LoginState(
                "error",
                message=f"生成二维码失败：{payload.get('message')}",
            )
        data = payload.get("data") or {}
        self.qrcode_key = str(data.get("qrcode_key") or "")
        self.login_url = str(data.get("url") or "")
        if not self.qrcode_key or not self.login_url:
            return LoginState("error", message="登录接口没有返回二维码")
        return LoginState("qrcode_ready", url=self.login_url)

    async def poll(self) -> LoginState:
        if not self.qrcode_key:
            return LoginState("error", message="没有进行中的二维码，请先 generate()")
        try:
            response = await self.client.get(
                POLL_URL, params={"qrcode_key": self.qrcode_key}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return LoginState("error", message=f"轮询请求失败：{exc}")
        if payload.get("code") != 0:
            return LoginState(
                "error", message=f"轮询失败：{payload.get('message')}"
            )

        data = payload.get("data") or {}
        if "code" not in data:
            return LoginState("error", message="登录响应缺少状态码")
        code = int(data.get("code") or 0)
        status = STATUS_TEXT.get(code)
        if status is None:
            return LoginState(
                "error",
                message=f"未知扫码状态：{code} {data.get('message') or ''}".strip(),
            )
        if status != "done":
            return LoginState(status, message=str(data.get("message") or ""))

        jump_url = str(data.get("url") or "")
        refresh_token = str(data.get("refresh_token") or "")
        cookies: dict[str, str] = {}

        # 1) 新版：poll 响应头直接带 Set-Cookie
        _merge_cookie_headers(response.headers.get_list("set-cookie"), cookies)
        # 2) 旧版：data.url 的查询串里直接带 cookie
        _cookies_from_url(jump_url, cookies)
        # 3) 兜底：跟随 data.url，让跳转过程把 cookie 种进 jar
        credential = _credential_from_cookies(cookies, refresh_token)
        if credential is None and jump_url:
            try:
                await self.client.get(jump_url)
            except httpx.HTTPError:
                pass
            for cookie in self.client.cookies.jar:
                if cookie.value:
                    cookies.setdefault(cookie.name, cookie.value)
            credential = _credential_from_cookies(cookies, refresh_token)
        if credential is None:
            for cookie in self.client.cookies.jar:
                if cookie.value:
                    cookies.setdefault(cookie.name, cookie.value)
            credential = _credential_from_cookies(cookies, refresh_token)

        if credential is None:
            return LoginState(
                "error",
                message=(
                    "扫码已成功，但没有拿到 SESSDATA / bili_jct / DedeUserID。"
                    "通常是登录响应被风控或协议再次变化；请重试，或改用浏览器 cookie 导入。"
                ),
                cookies_seen=sorted(cookies),
            )
        return LoginState("done", url=jump_url, credential=credential)


def save_credential(credential: dict[str, Any], path: str | Path = CRED_FILE) -> Path:
    """原子写入：先写临时文件再替换，避免中途被杀留下半个凭证。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(credential, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


async def interactive_login(path: str | Path = CRED_FILE) -> int:
    import os
    import sys

    import qrcode

    # Windows 控制台/Claude Code 的管道常见编码是 GBK，遇到无法编码的字符
    # 直接替换而不是抛 UnicodeEncodeError。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    login = QrLogin()
    qr_path = PROJECT_ROOT / "qrcode_login.png"
    try:
        state = await login.generate()
        if state.status != "qrcode_ready":
            print(f"[失败] {state.message}")
            return 1

        # 终端里的 ASCII 二维码在部分客户端（会吞 ANSI 颜色）看不清，
        # 直接落一张 PNG 并用系统默认看图工具打开。
        image = qrcode.make(state.url, box_size=10, border=4)
        image.save(qr_path)
        print(f"二维码已生成：{qr_path}")
        if os.name == "nt":
            try:
                os.startfile(qr_path)  # noqa: S606 - 本地图片，非用户输入
            except OSError:
                print("自动打开失败，请手动打开上面的文件扫码。")
        else:
            print("请打开上面的图片扫码。")
        print("用 B站 App 扫码并在手机上确认（180 秒内有效）\n")

        deadline = time.monotonic() + 180
        last = ""
        while time.monotonic() < deadline:
            state = await login.poll()
            if state.status != last:
                last = state.status
                if state.status == "waiting":
                    print("等待扫码……")
                elif state.status == "confirming":
                    print("[已扫码] 请在手机上确认……")
                elif state.status == "timeout":
                    qr_path.unlink(missing_ok=True)
                    print("[失败] 二维码超时，请重新运行")
                    return 1
                elif state.status == "error":
                    qr_path.unlink(missing_ok=True)
                    print(f"[失败] {state.message}")
                    return 1
                elif state.status == "done":
                    saved = save_credential(state.credential or {}, path)
                    qr_path.unlink(missing_ok=True)
                    print(f"[成功] 登录成功，凭证已保存到 {saved}")
                    return 0
            await asyncio.sleep(2)
        qr_path.unlink(missing_ok=True)
        print("[失败] 等待超时，请重新运行")
        return 1
    finally:
        await login.close()


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(interactive_login()))
