"""工具清单一致性测试。

`mcp_server.py` 注册哪些工具、`tool_manifest.py` 怎么分类，必须一一对应。
以后新增工具时如果忘了登记，这里会直接失败，而不是等到运行时才发现
某个写工具没被额度拦住。
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

import tool_manifest as manifest

MCP_SERVER = Path(__file__).resolve().parent.parent / "mcp_server.py"
SKILL_MANIFEST = (
    Path(__file__).resolve().parent.parent / "skills" / "bilibili" / "skill.json"
)


def registered_tool_names() -> set[str]:
    """扫描 ``@mcp.tool()`` 装饰的函数名，不 import（避免启动 Server）。"""
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                names.add(node.name)
    return names


class ManifestTests(unittest.TestCase):
    def test_清单覆盖所有注册工具(self):
        self.assertEqual(registered_tool_names(), set(manifest.ALL_TOOL_NAMES))

    def test_三类互不重叠(self):
        groups = [
            manifest.WRITE_TOOL_NAMES,
            manifest.READ_ONLY_TOOL_NAMES,
            manifest.HUMAN_ONLY_TOOL_NAMES,
        ]
        for index, left in enumerate(groups):
            for right in groups[index + 1 :]:
                self.assertFalse(left & right)

    def test_私信工具属于写入类(self):
        self.assertLessEqual(manifest.MESSAGE_TOOL_NAMES, manifest.WRITE_TOOL_NAMES)

    def test_登录工具不在写入类(self):
        self.assertFalse(
            manifest.HUMAN_ONLY_TOOL_NAMES & manifest.WRITE_TOOL_NAMES
        )

    def test_投稿工具属于人类专用(self):
        for name in ("bili_upload_video", "bili_upload_video_multi"):
            self.assertIn(name, manifest.HUMAN_ONLY_TOOL_NAMES)

    def test_登录轮询不挡在人类专用里(self):
        # 扫码本身必须由人类发起，但轮询状态只是只读；
        # 若把它标成 human_only，用户扫码后 Agent 无法继续完成登录。
        self.assertIn("bili_login", manifest.HUMAN_ONLY_TOOL_NAMES)
        self.assertIn("bili_login_check", manifest.READ_ONLY_TOOL_NAMES)
        self.assertNotIn("bili_login_check", manifest.HUMAN_ONLY_TOOL_NAMES)


class SkillManifestTests(unittest.TestCase):
    """技能清单里的写权限必须和本文件的名单一致。

    Agent 只读 ``skill.json`` 决定哪些工具算写入；这里漏了或多了，
    Agent 就会放行一个没被额度管理的写操作，或者白白拦住一个只读工具。
    """

    def setUp(self):
        self.tools = json.loads(SKILL_MANIFEST.read_text(encoding="utf-8"))["tools"]

    def test_写入类一致(self):
        self.assertEqual(set(self.tools.get("write") or ()), set(manifest.WRITE_TOOL_NAMES))

    def test_人类专用一致(self):
        self.assertEqual(
            set(self.tools.get("human_only") or ()),
            set(manifest.HUMAN_ONLY_TOOL_NAMES),
        )

    def test_私信类一致(self):
        self.assertEqual(
            set(self.tools.get("message") or ()),
            set(manifest.MESSAGE_TOOL_NAMES),
        )


if __name__ == "__main__":
    unittest.main()
