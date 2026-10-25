"""技能包清单：文件齐全、工具名单和 Server 侧一致。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tool_manifest

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "bilibili"


class SkillManifestTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((SKILL_DIR / "skill.json").read_text(encoding="utf-8"))

    def test_声明文件都在(self):
        for name in ("SKILL.md", "skill.json", "run_mcp.py", "mcp.json.example"):
            self.assertTrue((SKILL_DIR / name).is_file(), name)
        for name in self.data["instructions"].values():
            self.assertTrue((SKILL_DIR / name).is_file(), name)
        self.assertTrue(
            (SKILL_DIR / self.data["patrol"]["instructions"]).is_file()
        )

    def test_写入工具和名单一致(self):
        self.assertEqual(
            set(self.data["tools"]["write"]), set(tool_manifest.WRITE_TOOL_NAMES)
        )
        self.assertEqual(
            set(self.data["tools"]["human_only"]),
            set(tool_manifest.HUMAN_ONLY_TOOL_NAMES),
        )
        self.assertEqual(
            set(self.data["tools"]["message"]),
            set(tool_manifest.MESSAGE_TOOL_NAMES),
        )

    def test_MCP入口指向真实文件(self):
        mcp = self.data["mcp"]
        self.assertEqual(mcp["cwd"], ".")
        self.assertEqual(mcp["args"], ["run_mcp.py"])
        script = (SKILL_DIR / mcp["cwd"] / mcp["args"][0]).resolve()
        self.assertTrue(script.is_file(), script)

    def test_示例配置使用启动器(self):
        data = json.loads((SKILL_DIR / "mcp.json.example").read_text(encoding="utf-8"))
        server = data["mcpServers"]["bilibili"]
        self.assertTrue(server["args"][0].endswith("/skills/bilibili/run_mcp.py"))
        self.assertIn("BILI_SKILL_ROOT", server["env"])

    def test_巡检配置合法(self):
        patrol = self.data["patrol"]
        self.assertIsInstance(patrol["enabled"], bool)
        self.assertGreater(int(patrol["interval_minutes"]), 0)
        for tool in patrol["check_tools"]:
            self.assertIn(tool, tool_manifest.ALL_TOOL_NAMES)

    def test_组件版本字段合法(self):
        self.assertRegex(self.data.get("version", ""), r"^\d+\.\d+\.\d+$")
        self.assertGreaterEqual(int(self.data.get("api_version", 0)), 1)

    def test_激活方式可被宿主识别(self):
        activation = self.data.get("activation") or {}
        self.assertEqual(activation.get("mode"), "lazy")
        self.assertGreater(int(activation.get("idle_timeout_minutes", 0)), 0)


class LauncherTests(unittest.TestCase):
    """技能内启动器不依赖宿主 cwd，单独复制时给出可读错误。"""

    def _run(self, script: Path, *, cwd: Path, env: dict | None = None):
        child_env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        if env:
            child_env.update(env)
        return subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=str(cwd),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def test_从任意cwd都能找到仓库根(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                SKILL_DIR / "run_mcp.py", cwd=Path(tmp)
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"root: {ROOT}", result.stdout)
        self.assertIn("missing: none", result.stdout)

    def test_相对仓库根按技能目录解析(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                SKILL_DIR / "run_mcp.py",
                cwd=Path(tmp),
                env={"BILI_SKILL_ROOT": "../.."},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"root: {ROOT}", result.stdout)

    def test_单独复制技能目录时报可读错误(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bilibili"
            target.mkdir()
            shutil.copy2(SKILL_DIR / "run_mcp.py", target / "run_mcp.py")
            result = self._run(target / "run_mcp.py", cwd=target)

        self.assertEqual(result.returncode, 2)
        self.assertIn("找不到 mcp_server.py", result.stderr)
        self.assertIn("BILI_SKILL_ROOT", result.stderr)


if __name__ == "__main__":
    unittest.main()
