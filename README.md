# bilibili-skill

B 站 MCP Server + 按需加载的技能包，共 37 个工具。支持 Claude Code、Cursor、Cline、OpenClaw 等 MCP 客户端。

## 快速开始

需要 Python 3.10+；视频上传自动截封面需要 ffmpeg。

```bash
pip install -r requirements.txt
python skills/bilibili/run_mcp.py --check   # 检查仓库根和依赖
```

登录二选一：

- 接入 MCP 后对 AI 说“登录B站”，调用 `bili_login` 扫码；
- 或运行 `python bili_login.py`。

凭证保存在本地 `bili_credential.json`，会自动续期；不要提交或分享。

可调参数优先级：环境变量 > `config.json` > 默认值。示例：

```json
{"BILI_MAX_VIDEO_MINUTES": 90, "BILI_DENSE_INTERVAL_SECONDS": 2}
```

字段见 [.env.example](.env.example)。改完要重启 Server。

想让 Agent 优先回复你的另一个账号，把 `skills/bilibili/instructions/persona.md` 最后一行两个「（留空）」换成昵称和 UID；公开仓库请保持留空。

## 安全与权限（重要）

MCP Server 本身不强制每日写入额度、白名单或投稿审批。宿主必须根据 `skills/bilibili/skill.json` 的 `limits` 和 `tools.human_only` 执行；直接接 MCP 客户端时，这些限制不会生效。

- 视频投稿工具是 `human_only`，应由人类发起；视频只能放在 `data/uploads/`（或 `BILI_UPLOAD_DIR` 指定的目录）。
- 巡检默认关闭；确认你接受自动评论/私信的风险后，再把 `skill.json` 的 `patrol.enabled` 改成 `true`。
- 本项目与 B 站官方无关。自动化账号可能违反平台规则或导致账号受限；请自行评估风险，并遵守当地法律和 B 站社区规则。

## 自定义角色（可选）

默认是空白的人设模板，不绑定任何具体角色。需要人设时：

- 编辑 `skills/bilibili/instructions/persona.md`，填写名字、性格、说话方式和兴趣；
- 把立绘放到 `data/self/character.png`（或 `.jpg` / `.jpeg` / `.webp`），外观描述写到 `data/self/character.md`，格式见 [data/self/README.md](data/self/README.md)；
- 把表情包放进 `data/self/stickers/`，文件名就是表情名，见 [说明](data/self/stickers/README.md)。

这些个人素材默认被 `.gitignore` 忽略，不会被提交。

## 接入

**OpenClaw**

```bash
npm i -g mcporter
npx mcporter config add bilibili "python /path/to/bilibili-skill/skills/bilibili/run_mcp.py"
```

**Claude Code**：在项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "bilibili": {
      "command": "/path/to/bilibili-skill/.venv/bin/python",
      "args": ["/path/to/bilibili-skill/skills/bilibili/run_mcp.py"],
      "env": {
        "BILI_SKILL_ROOT": "/path/to/bilibili-skill",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

Windows 的 Python 是 `.venv/Scripts/python.exe`。首次使用会显示 `Pending approval`，批准一次即可；无头模式把 `{"enabledMcpjsonServers":["bilibili"]}` 写进 `.claude/settings.local.json`。

Claude Code 不执行 `skill.json` 的懒启动、巡检和写入名单，写入限制只能靠 `SKILL.md` 和它自己的权限设置。

**Cursor / Cline**：添加 MCP Server，名称 `bilibili`，command 填项目虚拟环境的 Python，args 填 `skills/bilibili/run_mcp.py`，`BILI_SKILL_ROOT` 填仓库根。也可以直接用 [mcp.json.example](skills/bilibili/mcp.json.example)。

## 工具

| 分类 | 工具 |
|---|---|
| 登录 | `bili_login`、`bili_login_check`、`bili_check_credential` |
| 数据采集 | `bili_search`、`bili_comments`、`bili_subtitle`、`bili_danmaku`、`bili_video_info`、`bili_video_overview`、`bili_video_frames`、`bili_reply`、`bili_crawl` |
| 内容发布 | `bili_self_image`、`bili_generate_image`、`bili_send_dynamic`、`bili_upload_video`、`bili_upload_video_multi`、`bili_send_opus`、`bili_video_zones` |
| 数据分析 | `bili_hot_videos`、`bili_hot_buzzwords`、`bili_weekly_hot`、`bili_rank`、`bili_user_info`、`bili_user_videos`、`bili_space_dynamics` |
| 互动运营 | `bili_favorite_lists`、`bili_favorite_content`、`bili_favorite`、`bili_send_message`、`bili_sticker_list`、`bili_send_sticker`、`bili_unread_messages`、`bili_recent_messages`、`bili_received_replies`、`bili_received_at_and_likes`、`bili_activity_log` |

工具参数和限制见 [instructions/tools.md](skills/bilibili/instructions/tools.md)。视频超过 60 分钟直接拒绝；超过 20 分钟先征得主人同意。

## 技能包

`skills/bilibili/` 把项目封装成按需加载的技能：

- 平时宿主只知道「可以逛 B 站」，不占工具和规则上下文；
- 进入 B 站场景才加载 `persona` / `rules` / 工具；
- 退出后卸载。

支持 Claude Code Skill、标准 MCP 配置和读 `skill.json` 的自写宿主。启动器 `run_mcp.py` 会自己找仓库根，复制技能目录后仍能给出可读错误。

```text
skills/bilibili/
├── run_mcp.py            # 启动器：找仓库根、检查依赖
├── skill.json            # 组件清单：懒激活、巡检、写入分类
├── SKILL.md              # Claude Code Skill 入口
├── mcp.json.example      # MCP 配置模板
└── instructions/         # persona / rules / tools / patrol
```

## 文件清单

**按宿主保留**

| 宿主 | 至少保留 | 可以不要 |
|---|---|---|
| 任意 MCP 客户端 | Server 必需文件、`requirements.txt`、`run_mcp.py`、一份 MCP 配置 | `skill.json`、`SKILL.md`、`instructions/`、`tests/`、`examples/`、三个 CLI |
| Claude Code Skill | Server 必需文件、`requirements.txt`、`run_mcp.py`、MCP 配置、`SKILL.md`、`instructions/` | `skill.json`、`tests/`、`examples/`、三个 CLI |
| 读清单的自写宿主 | Server 必需文件、`requirements.txt`、`run_mcp.py`、MCP 配置、`skill.json`、`instructions/` | `tests/`、`examples/`、三个 CLI；`SKILL.md` 可留作文档 |

三个 CLI 指 `bili_login.py`、`bili_log.py`、`bili_stickers.py`。

**成品 Agent 大概率用不到**：`skill.json`（不读清单）、`tool_manifest.py`（只给测试）、`tests/`、`examples/`、`bili_login.py`、`bili_log.py`、`bili_stickers.py`、`run_mcp.py`（配置直接指向 `mcp_server.py` 时）、`mcp.json.example`（配置好后）、`.mcp.json`（本机配置，别发布）。

**Server 运行必需**：

```text
mcp_server.py  bili_art.py  bili_auth.py  bili_avoid.py  bili_config.py
bili_credential.py  bili_dm.py  bili_draw.py  bili_dynamic.py
bili_frames.py  bili_images.py  bili_storyboard.py  bili_time.py
bili_uploads.py  bili_utils.py  mcp_audit.py  requirements.txt
```

`Pillow`、`httpx`、`qrcode`、`bilibili-api-python`、`mcp` 是启动必需；`av`、`aiohttp` 可选。

**发布前不要带**：`.mcp.json`、`.env`、`config.json`、`bili_credential.json*`、`logs/`、`data/` 下的运行时文件。`data/self/` 里的立绘、`character.md` 和表情包属于个人素材，默认已被 `.gitignore` 忽略；如需公开，请先确认版权和隐私。

## 日志

Server 记录每次工具调用，默认写到 `logs/`：

- `activity-YYYY-MM-DD.jsonl`：机器可读；
- `activity-YYYY-MM-DD.md`：人可读。

```bash
python bili_log.py -w      # 只看写入
python bili_log.py -s      # 当天统计
```

也可以用 `bili_activity_log` 查询。日志可能包含评论/私信内容，不要提交或上传。

## 注意

- 凭证、日志、配置都只存本地。
- 出站图片只允许本地表情包（`data/self/stickers/`）和绘画 AI 产物（`data/drawn/`）。
- 投稿视频只允许 `data/uploads/`（或 `BILI_UPLOAD_DIR` 指定的目录）。
- 绘画 AI 供应商尚未接入，`bili_generate_image` 当前只会报“未配置”。
- 回复、私信、投稿请遵守 B 站社区规则。
- 本项目仅用于学习和研究。

## License

本项目自身代码采用 MIT。第三方依赖的许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；发布前务必确认 `bilibili-api-python` 的实际许可证。
