# 技能包

技能 = 一行「什么时候用」 + 按需加载的规则 + MCP Server。读清单的宿主只看 `skill.json` 的 `name` / `description`；成品 Agent 不读清单，直接加载 `SKILL.md` 或接 MCP。

```text
skills/bilibili/
├── skill.json            # 组件清单：懒激活、巡检、写入分类
├── run_mcp.py            # 启动器：找仓库根、检查依赖
├── SKILL.md              # Claude Code Skill 入口
├── mcp.json.example      # MCP 配置模板
└── instructions/         # persona / rules / tools / patrol
```

## 文件谁需要

| 文件 | 任意 MCP 客户端 | Claude Code Skill | 读清单宿主 |
|---|---|---|---|
| `run_mcp.py` | 推荐 | 推荐 | 清单 `args` 指向它 |
| `mcp.json.example` | 配置模板 | 配置模板 | 不需要 |
| `SKILL.md` | 不需要 | 需要 | 可留作文档 |
| `instructions/` | 不需要 | 需要 | 需要 |
| `skill.json` | 不需要 | 不需要 | 需要 |
| `README.md` | 文档 | 文档 | 文档 |

只接 MCP 时，技能目录真正运行时需要的只有 `run_mcp.py`（或直接指向根目录 `mcp_server.py`）。`skill.json`、`SKILL.md`、`instructions/` 都不是 Server 的运行依赖。

## 成品 Agent

**Claude Code**：把 `skills/bilibili` 放到项目的 `.claude/skills/bilibili`，再把 `mcp.json.example` 合并进项目根目录的 `.mcp.json`。

- 首次使用显示 `Pending approval`，批准一次；无头模式把 `{"enabledMcpjsonServers":["bilibili"]}` 写进 `.claude/settings.local.json`。
- Server 名用 `bilibili`，工具前缀才是 `mcp__bilibili__*`。
- Claude Code 不执行 `skill.json` 的 `activation` / `patrol` / `tools.write` / `human_only`；写入限制只能靠 `SKILL.md` 和它自己的权限设置。

**Cursor / Cline / OpenClaw**：直接用 `mcp.json.example` 接 MCP；没有 Skill 机制就把 `SKILL.md` 放进规则文件。生命周期由客户端自己决定。

## 自写宿主

- 平时只把 `description` 放进上下文；用到时才启动 MCP。
- 激活时按 `mcp` 启动、按 `instructions` 读规则，用 `tools.write` 判断写权限；退出时关闭子进程。
- `patrol.enabled` 为真时，按 `interval_minutes` 定时激活。
- `local_tools` 由宿主实现；没有实现时按 `rules.md` 自己避开，`bili_reply` 只在名单已有数据时兜底。
- `mcp.command` 默认 `python`，跨虚拟环境用 `BILI_SKILL_PYTHON` 覆盖。
- `mcp.args` 指向 `run_mcp.py`，`cwd` 是 `.`。启动器先看 `BILI_SKILL_ROOT`，再逐级向上找 `mcp_server.py`。
- 自检：`python skills/bilibili/run_mcp.py --check`。
- 子进程秒退时读 stderr，别只报 `Connection closed`；示例见 `examples/component_smoke.py`。

字段含义见 [bilibili/skill.json](bilibili/skill.json)。

`skill.json` 的 `limits` 给出宿主应执行的默认值：`reply_per_day`、`message_per_day`、`dynamic_per_day` 是每日写入额度，`whitelist_uids` 是白名单账号 UID（宿主还应把 `persona.md` 里填写的主人 UID 并入），`whitelist_exempt` 表示白名单账号不受额度限制；宿主可按自己的策略覆盖这些默认值。`limits.enforced_by` 为 `host`：每日额度、白名单和投稿的 `human_only` 都由宿主执行，MCP Server 本身不计数。直接接 MCP 客户端而不实现宿主逻辑时，这些限制不会生效。

### 本地工具契约（可选）

`local_tools` 不是 MCP 工具，需要宿主自己暴露。以 `agent_avoid` 为例：

- 参数：`scope`（`thread` / `up`）、`rpid` 或 `uid`、`bvid`、`name`、`reason`；
- 实现：调用 `bili_avoid.DEFAULT.block_thread(...)` 或 `block_up(...)`；
- 存储：默认 `data/agent.avoid.json`，可用 `BILI_AVOID_PATH` 与宿主共用。

宿主没有实现时，模型按 `rules.md` 自己避开；`bili_reply` 仍会读取已有名单。
