#!/usr/bin/env python3
"""B站扫码登录，保存凭证供 MCP Server / Agent 使用。

实际协议细节在 ``bili_auth.py``：新版扫码成功后 SESSDATA 走
``Set-Cookie`` 响应头下发，旧版 ``bilibili_api`` 只解析 JSON 会保存空值。
"""

import asyncio

from bili_auth import interactive_login

if __name__ == "__main__":
    raise SystemExit(asyncio.run(interactive_login()))
