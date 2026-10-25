# 第三方依赖与许可证

本文件只做清单，不构成法律意见。发布或分发前，请用 `pip show <包名>` 或上游 `LICENSE` 核实实际安装版本的许可证。

| 依赖 | 用途 | 许可证（以实际版本为准） |
|---|---|---|
| `bilibili-api-python` | B 站 API 客户端 | 公开资料多标注 GPL-3.0；发布前重点核实 |
| `mcp` | MCP Python SDK | MIT |
| `httpx` | HTTP 客户端 | BSD-3-Clause |
| `Pillow` | 图片处理 | HPND |
| `qrcode` | 登录二维码 | BSD |
| `aiohttp` | 异步 HTTP（可选） | Apache-2.0 |
| `av` | 视频抽帧（可选） | BSD-3-Clause |
| `python-dotenv` | 依赖检查 | BSD-3-Clause |

## 发布前检查

1. 运行 `pip show bilibili-api-python`，确认 `License` 字段和上游仓库的 `LICENSE`。
2. 如果确实是 GPL-3.0，分发包含它的组合作品可能需要遵守 GPL-3.0；仅保留 MIT 可能不够。
3. 确认 `av` 的二进制轮子和 FFmpeg 相关依赖的再分发条款。
4. 在 README 保留“与 B 站官方无关、自动化可能违反平台规则”的声明。
