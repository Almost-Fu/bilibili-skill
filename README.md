# bilibili-skill

B 站 MCP Server + 按需加载技能包。通过 stdio 暴露 **37 个工具**，覆盖视频搜索/浏览/采集、评论/字幕/弹幕、动态/私信、收藏、视频投稿、本地表情包与绘画素材、操作审计。Claude Code、Cursor、Cline 等支持 stdio MCP 的客户端可以直接接入；读取 `skill.json` 的技能宿主（如 OpenClaw）可以按清单懒加载。

> 本项目与哔哩哔哩官方无关。自动化调用可能违反平台规则并带来账号风险，请遵守社区规则，仅用于学习研究。

## 目录

- [快速开始](#快速开始)
- [登录](#登录)
- [未登录能力边界](#未登录能力边界)
- [配置](#配置)
- [MCP 接入](#mcp-接入)
- [工具表](#工具表)
- [技能包](#技能包)
- [安全与权限](#安全与权限)
- [日志](#日志)
- [注意事项](#注意事项)
- [许可证](#许可证)

## 快速开始

### 环境要求

- **Python 3.10+**：`mcp_server.py` 直接使用 `X | None` 联合类型注解。
- Windows / macOS / Linux。
- 依赖见 `requirements.txt`：

  | 依赖 | 用途 |
  |---|---|
  | `bilibili-api-python>=17.0.0` | B 站 API 客户端 |
  | `mcp>=1.0.0,<2` | MCP Python SDK |
  | `httpx>=0.27` | HTTP 客户端 |
  | `aiohttp>=3.9.0` | 字幕下载等异步请求 |
  | `qrcode[pil]>=8.0` | 登录二维码 |
  | `Pillow>=10.0` | 图片处理 |
  | `av>=14.0.0` | PyAV，视频抽帧/密集联系表 |
  | `python-dotenv>=1.0` | 可选依赖（启动器检查） |

- **ffmpeg**：只有“投稿时没有提供封面、需要从视频自动截封面”时才需要，并且必须在 `PATH` 中。视频抽帧使用 PyAV（`av` 包），不需要系统安装 ffmpeg。

### 安装与自检

```bash
cd bilibili-skill
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

python -m pip install -r requirements.txt
python skills/bilibili/run_mcp.py --check
```

`--check` 只检查仓库根和依赖，不启动 Server。正常输出类似：

```text
skill: .../skills/bilibili
python: .../python
root: .../bilibili-skill
server: .../bilibili-skill/mcp_server.py
missing: none
```

如果缺少可选依赖，会额外打印 `optional-missing: ...`；缺少必需依赖时退出码为 2，并给出安装命令。

### 启动

```bash
python skills/bilibili/run_mcp.py
```

也可以直接运行 `python mcp_server.py`。两者都使用 stdio；通常不需要手工启动，由 MCP 客户端拉起子进程。

### 测试

```bash
python -B -m unittest discover -s tests
```

测试覆盖工具清单一致性、未登录公开读取、上传/出站图片白名单、凭证续期、配置优先级、抽帧与雪碧图等。

## 登录

B 站的公开视频、评论、弹幕、用户资料等大多不需要登录；**字幕、私有读取和所有写操作需要登录**。

凭证保存在仓库根目录的 `bili_credential.json`（与 `mcp_server.py` 同级）。`bili_credential.json*`、`qrcode_login.png`、`config.json`、`logs/` 都已被 `.gitignore` 忽略。

### 通过 AI 扫码

1. 让 AI 调用 `bili_login`。已登录且凭证有效时返回 `already_logged_in`；否则返回 `qrcode_ready`，其中包含 `qrcode_image`（`data:image/png;base64,...` 形式，可直接展示）、`qrcode_file`（`qrcode_login.png` 的绝对路径）和 `qrcode_url`。
2. 用 B 站 App 扫码，并在手机上确认。二维码 **180 秒**内有效。
3. 让 AI 调用 `bili_login_check` 查询状态：
   - `waiting`：等待扫码；
   - `scanning`：已扫码，等待手机确认；
   - `done`：登录成功，凭证已写入；
   - `already_logged_in`：当前已有凭证，没有进行中的扫码会话；
   - `timeout` / `error`：二维码过期或失败，重新调用 `bili_login`；
   - `no_session`：没有进行中的登录，先调用 `bili_login`。
4. 需要确认凭证是否仍有效时，调用 `bili_check_credential`。它使用需要登录态的接口验证；成功时返回 `logged_in`、`uid`、`username`，未登录或凭证失效时返回 `logged_in: false` 和原因。

### 终端扫码

```bash
python bili_login.py
# 等价于
python -m bili_auth
```

终端方式会在仓库根生成 `qrcode_login.png`，轮询 180 秒，成功后写入 `bili_credential.json`。Windows 下会尝试用系统看图工具打开；其他平台只打印文件路径。二维码在完成后删除。

### 凭证与自动续期

- 必需字段：`sessdata`、`bili_jct`、`dedeuserid`。任一项为空视为未登录。
- 续期字段：`ac_time_value`（refresh_token）；`buvid3` 会保留用于请求。
- 每次取凭证时按需刷新，检查间隔节流为 **10 分钟**：先问服务端是否需要刷新，需要才调用刷新接口。
- 刷新成功后用“临时文件 + 原子替换”写回，并保留文件里其它自定义字段；刷新失败不会阻塞调用，继续使用旧 cookie。
- 多个进程同时刷新时使用跨进程文件锁；如果发现另一个进程已经刷新过，直接复用文件里的新凭证。
- 没有 `ac_time_value` 时不会自动续期，只能重新扫码。
- 日志只记录刷新状态，不打印任何 cookie 值；POSIX 下凭证文件权限会被设为 `0600`（Windows 不设置）。

**不要提交或分享 `bili_credential.json`。** 它等同于账号登录态。

## 未登录能力边界

工具分两类取凭证：公开只读工具在未登录时使用空凭证；需要登录的工具会返回结构化的 `login_required`，而不是抛出 MCP 错误。

| 能力 | 相关工具 | 未登录时 |
|---|---|---|
| 视频搜索 | `bili_search` | 可用 |
| 视频详情 | `bili_video_info`（`bvid` 或 `aid`） | 可用；`bvid` 优先 |
| 评论、弹幕 | `bili_comments`、`bili_danmaku` | 可用 |
| 视频概览、抽帧 | `bili_video_overview`、`bili_video_frames` | 可用；概览里的字幕段会标记 `login_required` |
| 热门、热搜、每周必看、排行 | `bili_hot_videos`、`bili_hot_buzzwords`、`bili_weekly_hot`、`bili_rank` | 可用 |
| 用户资料/投稿/动态 | `bili_user_info`、`bili_user_videos`、`bili_space_dynamics` | 基础读取通常可用；`bili_user_info` 的累计数据不可用，`bili_space_dynamics` 被接口拒绝时返回 `success: false` |
| 公开收藏夹 | `bili_favorite_content`，以及 `bili_favorite_lists` 查其他 UID | `bili_favorite_content` 公开内容可读，不可见时返回错误；`bili_favorite_lists` 未登录且接口返回空时返回 `login_required` |
| 本地素材/工具 | `bili_self_image`、`bili_generate_image`、`bili_sticker_list`、`bili_video_zones`、`bili_activity_log` | 本地可用（`bili_generate_image` 未配置绘画 AI 时会返回失败） |
| 字幕 | `bili_subtitle`；`bili_video_overview` / `bili_crawl` 的字幕部分 | **需要登录** |
| 查自己的收藏夹 | `bili_favorite_lists(uid=0)` | **需要登录** |
| 私有读取 | `bili_unread_messages`、`bili_recent_messages`、`bili_received_replies`、`bili_received_at_and_likes` | **需要登录** |
| 写操作 / 投稿 | `bili_reply`、`bili_favorite`、`bili_send_dynamic`、`bili_send_opus`、`bili_send_message`、`bili_send_sticker`、`bili_upload_video`、`bili_upload_video_multi` | **需要登录**；前 6 个是 `write`，后 2 个投稿是 `human_only` |
| 登录流程 | `bili_login`、`bili_login_check`、`bili_check_credential` | 不需要已有登录；`bili_check_credential` 未登录时返回 `logged_in: false` |

需要登录时，写工具通常返回：

```json
{
  "success": false,
  "login_required": true,
  "error": "未登录B站！请先调用 bili_login 工具获取登录二维码，让用户扫码登录。"
}
```

`bili_subtitle` 的字幕接口强制要求 `sessdata`，未登录时返回：

```json
{
  "bvid": "BV...",
  "title": "...",
  "login_required": true,
  "message": "读取字幕需要先登录 B 站。请用户明确要求后，再调用 bili_login 扫码。"
}
```

`bili_user_info` 未登录时仍返回昵称、等级、粉丝等公开字段，但 `likes`、`archive_view`、`article_view` 为 `null`，并带 `stats_unavailable: true` 和 `stats_error`，不会用 `0` 冒充“没有数据”。

未登录查别人的收藏夹时，B 站可能返回 `data=null`。`bili_favorite_lists` 会区分“未登录”和“对方没有公开收藏夹”：前者返回 `login_required`，不会伪装成空列表。

## 配置

取值优先级（只适用于通过 `bili_config.py` 读取的键）：

```text
环境变量  >  仓库根目录的 config.json  >  代码默认值
```

`config.json` 与 `mcp_server.py` 同级，可选，由你手工创建或由外部工具写入；仓库不附带该文件。也可以用 `BILI_CONFIG_PATH` 指向别处。键名是扁平的，与下表中 `bili_config` 读取的键一致：

```json
{
  "BILI_MAX_VIDEO_MINUTES": 60,
  "BILI_FRAMES_MAX": 3,
  "BILI_FRAMES_MAX_HEIGHT": 1080,
  "BILI_FRAMES_MAX_AGE_SECONDS": 3600,
  "BILI_DENSE_INTERVAL_SECONDS": 3.0,
  "BILI_DENSE_MAX_FRAMES": 40,
  "BILI_DENSE_HEIGHT": 480,
  "BILI_WINDOW_SECONDS": 120,
  "BILI_STORYBOARD_MAX_FRAMES": 24
}
```

### 环境变量

| 变量 | 默认值 | 读取方式 | 说明 |
|---|---|---|---|
| `BILI_CONFIG_PATH` | `<仓库根>/config.json` | 仅环境变量 | 指定配置文件位置；建议绝对路径 |
| `BILI_MAX_VIDEO_MINUTES` | `60` | 环境变量 > `config.json` | 超长视频上限，范围 1–600；概览和抽帧超过则拒绝 |
| `BILI_FRAMES_MAX` | `3` | 环境变量 > `config.json` | 单次按时间点抽帧上限，范围 1–9 |
| `BILI_FRAMES_MAX_HEIGHT` | `1080` | 环境变量 > `config.json` | 抽帧最高高度，范围 360–2160 |
| `BILI_FRAMES_MAX_AGE_SECONDS` | `3600` | 环境变量 > `config.json` | 临时帧保留秒数，范围 60–86400 |
| `BILI_DENSE_INTERVAL_SECONDS` | `3.0` | 环境变量 > `config.json` | 密集联系表取帧间隔，范围 0.5–60 |
| `BILI_DENSE_MAX_FRAMES` | `40` | 环境变量 > `config.json` | 密集联系表最多帧数，范围 1–120 |
| `BILI_DENSE_HEIGHT` | `480` | 环境变量 > `config.json` | 密集联系表单帧高度，范围 240–2160 |
| `BILI_WINDOW_SECONDS` | `120` | 环境变量 > `config.json` | 按时间窗口查看时的单窗口秒数，范围 10–600 |
| `BILI_STORYBOARD_MAX_FRAMES` | `24` | 环境变量 > `config.json` | 雪碧图联系表最多帧数，范围 1–60 |
| `BILI_UPLOAD_DIR` | `data/uploads` | 仅环境变量 | 投稿白名单目录；相对路径按仓库根解析 |
| `BILI_AVOID_PATH` | `data/agent.avoid.json` | 仅环境变量 | 冷处理名单文件；建议绝对路径 |
| `BILI_MCP_LOG_DIR` | `logs` | 仅环境变量 | 审计日志目录；建议绝对路径 |
| `BILI_SKILL_ROOT` | 无，启动器自动向上查找 | 仅环境变量 | `run_mcp.py` 使用的仓库根；技能目录被单独复制时需要设置 |
| `BILI_SKILL_PYTHON` | 无，回落到 `python` | 由读取 `skill.json` 的宿主使用 | `skill.json` 里的 `mcp.command_env`，用于指定解释器 |

另外建议在 MCP 客户端配置里设置：

```json
"PYTHONUTF8": "1",
"PYTHONIOENCODING": "utf-8"
```

注意：

- 仓库**不会自动加载 `.env`**。`.env.example` 只是参考，环境变量必须由启动 MCP Server 的进程或 MCP 客户端配置提供。
- 空环境变量视为未设置；非法数字会回落到默认值，不会让 Server 启动失败。
- 抽帧/雪碧图相关常量在模块导入时读取，修改后需要重启 Server（在技能宿主里就是重新激活技能）才生效。
- `BILI_UPLOAD_DIR` 的相对路径按仓库根解析；`BILI_CONFIG_PATH`、`BILI_AVOID_PATH`、`BILI_MCP_LOG_DIR` 直接按路径使用，相对路径会按进程当前目录解析，建议写绝对路径。

## MCP 接入

### Claude Code

在项目根目录创建 `.mcp.json`（该文件已被 `.gitignore` 忽略，避免提交本机路径）：

```json
{
  "mcpServers": {
    "bilibili": {
      "command": "/path/to/bilibili-skill/.venv/Scripts/python.exe",
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

把示例里的 `/path/to/bilibili-skill` 换成实际仓库绝对路径。Windows 的解释器是 `.venv/Scripts/python.exe`；macOS / Linux 通常是 `.venv/bin/python`，例如：

```json
"command": "/Users/you/bilibili-skill/.venv/bin/python"
```

说明：

- 使用绝对路径；Windows 路径可以写成正斜杠，避免 JSON 转义。
- `BILI_SKILL_ROOT` 是可选的：`run_mcp.py` 会从技能目录向上查找 `mcp_server.py`；只有技能目录被单独复制时才必须设置。
- 修改 `.mcp.json` 后需要重载/重启客户端。
- 工具在 Claude Code 里通常带服务器前缀，例如 `mcp__bilibili__bili_video_info`，权限允许列表按这个形式写。

### Cursor / Cline

两者都支持 stdio MCP。把上面的 `mcpServers.bilibili` 段放进客户端的 MCP 配置即可，`command`、`args`、`env` 保持完全一致：

- Cursor：设置中的 MCP Servers，或项目级 MCP 配置（位置随版本）；
- Cline：MCP Servers 配置面板/配置文件。

不同版本的位置和字段可能略有差异，以客户端当前版本为准。关键只有三点：用虚拟环境里的 Python、启动 `skills/bilibili/run_mcp.py`、设置 `BILI_SKILL_ROOT` 和 UTF-8 环境变量。

### OpenClaw / 读取 skill.json 的宿主

这类宿主会读取 `skills/bilibili/skill.json`，按 `mcp` 段启动 Server：

- `mcp.command`：`python`；
- `mcp.command_env`：`BILI_SKILL_PYTHON`，把环境变量设成虚拟环境解释器；
- `mcp.args`：`["run_mcp.py"]`；
- `mcp.cwd`：`.`，即技能目录；
- `transport`：`stdio`。

把 `skills/bilibili` 注册为技能目录即可。`activation.mode` 是 `lazy`，空闲 10 分钟后卸载；`patrol.enabled` 默认 `false`。如果宿主把技能目录复制到别处，仍需把 `BILI_SKILL_ROOT` 指向包含 `mcp_server.py` 的仓库根，否则 `run_mcp.py` 会给出可读错误。

`examples/component_smoke.py` 是一个最小宿主示例：默认只读 `skill.json` 并打印描述，不启动 MCP；加 `--call` 才启动 stdio 子进程、列工具并调用一次：

```bash
python examples/component_smoke.py
python examples/component_smoke.py --call bili_sticker_list
python examples/component_smoke.py --call bili_search --args '{"keyword": "B站"}'
```

### Pending approval 与无头模式

- 交互式客户端在首次使用项目级 `.mcp.json` 或首次调用 MCP 工具时，可能显示 `Pending approval`，需要在客户端里批准该服务器/工具。
- 无头/后台模式没有交互确认窗口，必须在启动前把所需工具加入客户端的允许列表或权限配置，否则调用会一直等待审批。
- MCP Server 本身没有办法自动批准，也不会绕过客户端的权限层。

### Claude Code 与 skill.json

Claude Code 通过 MCP 协议加载工具，**不会强制执行 `skill.json` 里的额度、白名单和审批策略**。因此：

- `skill.json` 里的 `tools.write` / `tools.human_only` / `tools.message` 分类不会被 Claude Code 自动执行；
- 每日额度（评论 20、私信 10、动态 1）、白名单豁免、投稿审批、巡检都不会因为 `skill.json` 写了就自动生效；
- 需要在 Claude Code 的权限设置/允许工具列表里限制，或使用真正读取 `skill.json` 的宿主。

MCP Server 自身同样不执行这些策略，详见[安全与权限](#安全与权限)。

## 工具表

所有工具都返回 JSON 字符串。常见字段：

- `success`：布尔值，部分只读工具直接返回数据，不带该字段；
- `error` / `message`：错误或说明；
- `login_required`：预期内的未登录状态，**不会**作为 MCP `isError` 抛出。

部分工具（如 `bili_hot_buzzwords`、`bili_weekly_hot`、`bili_unread_messages`、`bili_received_replies`）直接返回 B 站接口的原始 JSON，字段以接口为准。

共 **37 个工具**，与 `tool_manifest.ALL_TOOL_NAMES` 一致：`write` 6 个、`read` 28 个、`human_only` 3 个；其中 `message` 2 个，是 `write` 的子集。

### 登录与凭证（3）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_login` | 生成登录二维码；已登录且有效时不重复生成 | — | 无需已有登录（扫码后写入凭证） | `human_only` |
| `bili_login_check` | 轮询扫码进度 | — | 无需（轮询当前登录会话） | `read` |
| `bili_check_credential` | 验证当前凭证是否有效 | — | 无需（未登录返回 `logged_in: false`） | `read` |

### 浏览与采集（8）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_search` | 搜索 B 站视频 | `keyword`；`num=10`；`order="totalrank"`（`totalrank`/`click`/`pubdate`/`dm`） | 公开 | `read` |
| `bili_comments` | 视频热门评论，含楼中楼和图片地址 | `bvid`；`num=30` | 公开 | `read` |
| `bili_subtitle` | 获取字幕，优先中文 AI 字幕 | `bvid` | **必须** | `read` |
| `bili_danmaku` | 获取弹幕 | `bvid`；`num=100` | 公开 | `read` |
| `bili_video_info` | 视频详情；可用 `aid` 反查 BV 号 | `bvid=""`；`aid=0`（二选一，`bvid` 优先） | 公开 | `read` |
| `bili_video_overview` | 字幕 + 密集联系表；可按时间窗口查看 | `bvid`；`page=1`；`max_frames=40`；`start=None`；`end=None` | 公开（字幕需登录） | `read` |
| `bili_video_frames` | 按时间点抽取高清帧 | `bvid`；`times`；`page=1`；`max_frames=3`；`max_height=1080` | 公开 | `read` |
| `bili_crawl` | 按关键词批量采集视频、评论、字幕 | `keyword`；`max_videos=5`；`comments_per_video=20`；`get_subtitles=true` | 公开（字幕需登录） | `read` |

补充：

- `bili_video_overview` 默认每约 3 秒一帧、最多 40 帧；没有 PyAV 或密集抽帧失败时退回 B 站雪碧图，最多 24 帧。传 `start`/`end` 时窗口默认最多 120 秒；不传则看全片。超过 `BILI_MAX_VIDEO_MINUTES`（默认 60 分钟）的视频直接拒绝。
- `bili_video_frames` 的 `max_frames` 默认 3 张，硬上限由 `BILI_FRAMES_MAX` 控制（默认 3，最大 9），传更大的值会被截断；依赖 PyAV，缺少时返回“未安装 PyAV，无法抽高清帧”。
- `bili_crawl` 未登录时评论仍会采集，每个视频的字幕部分会带 `subtitle_error`，值为登录提示。

### 互动与发布（5）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_reply` | 发表评论或回复；自己发主楼评论时最多带 1 张图 | `bvid`；`text`；`rpid=0`；`root=0`；`image=""` | **必须**（写） | `write` |
| `bili_send_dynamic` | 发布图文动态 | `text`；`images`（最多 9 张）；`topic_id=0`；`schedule_time=0` | **必须**（写） | `write` |
| `bili_send_opus` | 发布带标题的图文动态；**不是专栏文章** | `title`；`content`；`images`（最多 9 张） | **必须**（写） | `write` |
| `bili_upload_video` | 上传单 P 视频 | `video_path`；`title`；`desc=""`；`tid=124`；`tags="AI"`；`cover_path=""`；`source=""`；`dynamic_text=""`；`no_reprint=true` | **必须**（投稿，人类专用） | `human_only` |
| `bili_upload_video_multi` | 把多个视频作为分 P 上传 | `video_paths`；`page_titles`；`title`；`desc=""`；`tid=124`；`tags="AI"`；`cover_path=""`；`source=""` | **必须**（投稿，人类专用） | `human_only` |

补充：

- `bili_reply` 只有 `rpid=0`（发新评论）时能带图；回复别人的评论不能带图。图片上限 5 MB。
- `bili_send_dynamic` / `bili_send_opus` 的图片只接受本地表情包或 `data/drawn/` 产物，最多 9 张。
- `bili_send_opus` 会把标题放在正文首行（`【标题】`）；`bilibili-api` 17 没有专栏发布接口。
- 投稿标题会截到 80 字；`tags` 逗号分隔，空值回落为“视频”；`tid` 可用 `bili_video_zones` 查询。
- `source` 为空表示原创，`no_reprint` 默认 `true`；填写 `source` 表示转载，`no_reprint` 会被置为 `false`。
- `video_path` / `video_paths` 必须位于投稿白名单目录，默认 `data/uploads/`，可用 `BILI_UPLOAD_DIR` 覆盖。
- `cover_path` 只接受本地表情包或绘画产物；不填时用 ffmpeg 从视频第 3 秒（失败再试第 0 秒）截封面。

### 热门、排行与用户（8）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_video_zones` | 列出常用投稿分区 ID → 名称 | — | 本地 | `read` |
| `bili_hot_videos` | 当前热门视频 | `pn=1`；`ps=20`（最大 50） | 公开 | `read` |
| `bili_hot_buzzwords` | 热搜词 | `page_num=1`；`page_size=20` | 公开 | `read` |
| `bili_weekly_hot` | 每周必看 | `week=0`（0 返回期数列表，>0 返回该期视频） | 公开 | `read` |
| `bili_rank` | 分区排行榜 | `category="all"`；`day=3`（3 或 7） | 公开 | `read` |
| `bili_user_info` | 用户资料、粉丝数和 UP 主累计数据 | `uid` | 公开（累计数据需登录） | `read` |
| `bili_user_videos` | 用户投稿列表 | `uid`；`pn=1`；`ps=30`；`order="pubdate"`（`pubdate`/`click`/`stow`）；`keyword=""` | 公开 | `read` |
| `bili_space_dynamics` | 用户最近的图文、转发和视频动态 | `uid`；`limit=5`（最大 20） | 公开（接口可能要求登录） | `read` |

`bili_rank` 的 `category` 可取：`all`、`original`、`rookie`、`douga`、`music`、`dance`、`game`、`knowledge`、`technology`、`sports`、`car`、`life`、`food`、`animal`、`fashion`、`ent`、`cinephile`。

### 收藏夹（3）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_favorite_lists` | 获取收藏夹列表 | `uid=0`（0 表示当前登录账号） | 查自己必须；查别人可选 | `read` |
| `bili_favorite_content` | 获取收藏夹内的视频 | `media_id`；`page=1`；`keyword=""` | 可选（公开收藏夹） | `read` |
| `bili_favorite` | 收藏或取消收藏 | `bvid`；`media_id=0`（0 用默认收藏夹）；`remove=false` | **必须**（写） | `write` |

`bili_favorite` 在 `media_id=0` 时会先查收藏夹列表，优先找标题为“默认收藏夹”的项，否则用第一个；账号没有任何收藏夹时返回错误。

### 私信、表情与通知（7）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_send_message` | 发私信：只文字、只图片，或先文字后图片 | `receiver_uid`；`text=""`；`image=""` | **必须**（写） | `write` / `message` |
| `bili_send_sticker` | 从本地表情包库挑一张发私信 | `receiver_uid`；`sticker`；`text=""` | **必须**（写） | `write` / `message` |
| `bili_sticker_list` | 列出本地表情名，不读取图片内容 | — | 本地 | `read` |
| `bili_unread_messages` | 获取私信、@、回复、点赞等未读数 | — | **必须** | `read` |
| `bili_recent_messages` | 获取未读私信会话和最近消息 | `limit=5`（1–20）；`messages_per_session=8`（1–30） | **必须** | `read` |
| `bili_received_replies` | 获取最近收到的评论回复通知 | — | **必须** | `read` |
| `bili_received_at_and_likes` | 获取最近收到的 @ 和点赞通知 | — | **必须** | `read` |

补充：

- `bili_send_message` 的 `text` 和 `image` 至少要有一个；图片上限 8 MB；先发文字再发图片，图片失败时文字结果会保留在 `sent` 里。
- `bili_send_sticker` 直接接受表情名，可带或不带扩展名，大小写不敏感；不接受路径分隔符或 `..`。
- `bili_recent_messages` 只返回有未读、最后一条不是自己发的、且不是系统通知的会话。

### 本地素材与日志（3）

| 工具 | 作用 | 主要参数（默认值） | 登录 | 清单 |
|---|---|---|---|---|
| `bili_self_image` | 查看本地形象立绘和文字描述 | — | 本地 | `read` |
| `bili_generate_image` | 调用绘画 AI 生成配图 | `prompt`；`width=1024`；`height=1024`；`mood="happy"` | 本地（当前绘画 AI 未配置） | `read` |
| `bili_activity_log` | 查看 AI 最近对账号执行过哪些操作 | `count=20`（最大 200）；`date=""`；`tool=""`；`writes_only=false` | 本地 | `read` |

补充：

- `bili_self_image` 读取 `data/self/character.png`（或 `.jpg`/`.jpeg`/`.webp`）和 `data/self/character.md`/`character.txt`。原图超过约 1.2 MB 时会生成 `character.view.jpg` 缩略图。
- `bili_generate_image` 的 `width`/`height` 会被夹到 512–1600；`mood` 支持 `happy`/`calm`/`tired`/`excited`，未知值回落 `happy`。当前 `bili_draw.py` 没有接入任何供应商，`configured()` 恒为 `False`，调用会返回失败原因，不会退回本地画图。
- `bili_activity_log` 自身不会被写入审计日志，避免每看一次日志就多一条噪音。

## 技能包

### 目录结构

```text
skills/
├── README.md                       # 技能目录说明
└── bilibili/
    ├── SKILL.md                    # 技能入口（skill.json 的 entry）
    ├── skill.json                  # 技能清单：描述、激活、MCP 启动、工具分类、额度、巡检
    ├── run_mcp.py                  # 技能内 MCP 启动器，支持 --check
    ├── mcp.json.example            # 通用 MCP 客户端配置模板
    └── instructions/
        ├── persona.md              # 人设
        ├── rules.md                # 行为规则
        ├── tools.md                # 工具使用说明
        └── patrol.md               # 巡检说明
```

`run_mcp.py` 不假设宿主从哪个目录启动：优先使用 `BILI_SKILL_ROOT`，否则从技能目录向上查找 `mcp_server.py`。技能目录被单独复制、找不到 Server 时会给出可读错误，而不是只让宿主看到 `Connection closed`。

### skill.json 的关键内容

| 字段 | 值 | 说明 |
|---|---|---|
| `version` / `api_version` | `0.2.0` / `1` | 技能版本 |
| `activation.mode` | `lazy` | 按需激活 |
| `activation.idle_timeout_minutes` | `10` | 空闲卸载时间 |
| `entry` | `SKILL.md` | 技能入口 |
| `mcp.server` | `bilibili` | MCP 服务器名 |
| `mcp.transport` | `stdio` | 传输方式 |
| `mcp.command` | `python` | 默认解释器 |
| `mcp.command_env` | `BILI_SKILL_PYTHON` | 宿主可用它覆盖解释器 |
| `mcp.args` | `["run_mcp.py"]` | 启动脚本 |
| `mcp.cwd` | `.` | 技能目录 |
| `tools.write` | 6 个 | 见[工具表](#工具表) |
| `tools.human_only` | 3 个 | `bili_login`、`bili_upload_video`、`bili_upload_video_multi` |
| `tools.message` | 2 个 | `bili_send_message`、`bili_send_sticker` |
| `limits.enforced_by` | `host` | Server 不执行额度 |
| `limits.reply_per_day` | `20` | 评论/回复 |
| `limits.message_per_day` | `10` | 私信（含表情） |
| `limits.dynamic_per_day` | `1` | 动态 |
| `limits.whitelist_exempt` | `true` | 白名单账号可豁免 |
| `limits.whitelist_uids` | `[]` | 额外 UID；主人 UID 应由宿主并入 |
| `patrol.enabled` | `false` | 巡检默认关闭 |
| `patrol.interval_minutes` | `30` | 巡检间隔 |
| `patrol.check_tools` | `bili_received_replies`、`bili_received_at_and_likes`、`bili_recent_messages` | 巡检要检查的三处 |

这些额度、白名单、投稿审批和巡检都是**宿主契约**。MCP Server 不读取 `skill.json`，也不会自动执行。

### 不同宿主至少需要哪些文件

| 宿主类型 | 至少需要 | 说明 |
|---|---|---|
| 纯 MCP 客户端（Claude Code / Cursor / Cline） | 仓库根的 Python 代码 + 已安装依赖；推荐保留 `skills/bilibili/run_mcp.py` 作为启动器 | 也可以直接启动 `mcp_server.py`；不需要 `skill.json`、`SKILL.md`、`instructions/`；`mcp.json.example` 只是模板 |
| 读取 `skill.json` 的技能宿主（OpenClaw） | `skills/bilibili/skill.json`、`SKILL.md`、`instructions/*.md`、`run_mcp.py`、仓库根代码 | 宿主按 `skill.json` 的 `mcp` 段启动，并可按 `limits`/`patrol` 执行策略 |
| 维护/测试 | 上述 + `tests/`；依赖由 `requirements.txt` 安装 | `.env.example` 只是配置参考；运行 `python -B -m unittest discover -s tests` |

只复制 `skills/bilibili` 目录而不带仓库根代码是跑不起来的：`run_mcp.py` 需要找到 `mcp_server.py`。测试里也覆盖了这种错误提示。

## 安全与权限

**MCP Server 本身不执行以下策略：**

- 每日额度（评论、私信、动态）；
- 白名单豁免；
- `human_only` 投稿审批；
- 巡检调度。

这些只写在 `skill.json` 里，`limits.enforced_by` 明确是 `host`。任何能调用 MCP 工具的客户端都可以调用写工具和投稿工具；`human_only` 只是给宿主的元数据，不是 Server 侧的强制拦截。要限制，必须在宿主、客户端权限或自己的封装层实现。

Server 侧实际执行的技术保护：

- **登录检查**：需要登录的工具在本地没有凭证时返回结构化 `login_required`，不会继续调用 B 站写接口。
- **投稿目录白名单**：`bili_upload_video` / `bili_upload_video_multi` 只接受 `data/uploads/`（或 `BILI_UPLOAD_DIR`）内的已存在文件。这个检查只判断路径，不检查扩展名或内容。
- **出站图片白名单**：评论配图、动态/图文图片、私信图片、视频封面只能来自 `data/self/stickers/` 或 `data/drawn/`；网络图片、其他本地目录、路径穿越都会被拒绝。
- **超长视频保护**：`bili_video_overview` / `bili_video_frames` 对超过 `BILI_MAX_VIDEO_MINUTES`（默认 60 分钟）的视频直接拒绝。
- **评论冷处理名单**：`bili_reply` 读取 `data/agent.avoid.json`；命中楼层或 UP 主时拒绝回复。名单只增不减，想解禁直接编辑该文件删掉条目，下次读取生效，不需要重启。
- **凭证本地化**：凭证、日志、配置都只保存在本地，且默认被 `.gitignore` 忽略。

巡检默认关闭（`patrol.enabled: false`），Server 没有内置定时器；需要巡检时由宿主按 `skill.json` 的 `patrol` 段调度。

其他风险：

- 本项目与 B 站官方无关，使用的是非官方接口；接口可能变化、限流或触发风控（如 `-412`）。
- 自动化评论、私信、投稿可能违反社区规则，导致账号受限。请控制频率，不要批量骚扰。
- 不要把 `bili_credential.json`、日志或包含个人内容的配置提交到公开仓库。

## 日志

默认日志目录是仓库根目录的 `logs/`，可用 `BILI_MCP_LOG_DIR` 覆盖。每个自然日生成两个文件：

```text
logs/activity-YYYY-MM-DD.jsonl   # 一行一条 JSON，机器可读
logs/activity-YYYY-MM-DD.md      # 按会话分组的 Markdown，人可读
```

每次工具调用（`bili_activity_log` 自身除外）都会记录：

| 字段 | 内容 |
|---|---|
| `time` / `ts` / `unix` / `date` | 调用时间 |
| `session` | 进程启动时生成的会话 ID |
| `tool` | 工具名 |
| `category` | `read` / `write` / `login` / `crawl` |
| `status` | `ok` / `error` |
| `duration_ms` | 耗时 |
| `args` | 参数；单个字符串最多保留 4000 字符 |
| `result` | 结果；最多保留 8000 字符 |
| `result_chars` / `result_truncated` | 原始长度和是否截断 |
| `error` | 异常信息 |

日志写入失败会被吞掉，绝不影响工具调用。Markdown 版按会话分组，结果预览更短。

查看日志：

```bash
python bili_log.py                  # 今天的最近 30 条
python bili_log.py -n 100           # 今天最近 100 条
python bili_log.py --writes         # 只看写入操作
python bili_log.py --tool bili_reply
python bili_log.py --summary        # 今天的操作统计
python bili_log.py --date 2026-10-22
python bili_log.py --list           # 有哪些天的日志
```

也可以让 AI 调用 `bili_activity_log`：

```json
{"count": 20, "date": "", "tool": "", "writes_only": false}
```

`date` 留空或 `today` 表示今天，也接受 `yesterday` 和 `YYYY-MM-DD`；`count` 最大 200。

**日志可能包含隐私内容**：参数和结果里可能出现评论正文、私信内容、UID、图片路径等。请只保存在本地，不要公开分享或提交。`logs/` 已在 `.gitignore` 中。

## 注意事项

- **本地文件**：`bili_credential.json`、`config.json`、`logs/`、`data/` 下的运行素材都只应保存在本地。凭证等同于账号登录态，不要提交或发给他人。
- **出站图片**：只允许本地表情包（`data/self/stickers/`）和绘画 AI 产物（`data/drawn/`），格式限 `png`/`jpg`/`jpeg`/`webp`，拒绝网络图片。评论配图上限 5 MB，私信图片上限 8 MB；动态/图文/封面不额外检查 MB 数，只校验来源和格式。
- **表情包管理**：

  ```bash
  python bili_stickers.py list
  python bili_stickers.py add 图片路径 表情名 [--size 512] [--force]
  ```

  会把图片裁成正方形、默认缩放到 512×512 并保存为 PNG；超过 8 MB 时会警告“B站私信可能发不出去”。

- **投稿目录**：默认 `data/uploads/`，目录不存在时先创建并把视频放进去，或设置 `BILI_UPLOAD_DIR`。只允许上传该目录内的文件。没有提供封面时会调用系统 `ffmpeg` 自动截帧；`ffmpeg` 不可用会导致投稿失败。
- **绘画 AI 未配置**：`bili_draw.configured()` 当前恒为 `False`，`bili_generate_image` 会返回 `success: false` 和“绘画 AI 还没接入”的原因；没有本地画图兜底。需要生成图片时，先按 `bili_draw.py` 的接口接入供应商，并确保产物保存到 `data/drawn/`。
- **视频长度限制**：`bili_video_overview` 和 `bili_video_frames` 只处理不超过 `BILI_MAX_VIDEO_MINUTES`（默认 60 分钟）的视频；上传视频不受这个限制。
- **临时文件**：联系表和抽帧会写到 `data/tmp/storyboard/`、`data/tmp/frames/`；`bili_video_frames` 的结果和 `bili_video_overview` 的 `storyboard` 段会带 `cleanup_id` 和 `expires_in_seconds`。下次运行时会清理过期目录（帧默认 3600 秒，雪碧图 3600 秒）；目前没有单独的 MCP 清理工具。
- **接口稳定性**：B 站接口和风控策略可能变化，`bilibili-api` 版本升级也可能改变字段。出现异常时先看日志和 `bili_check_credential` 的结果。
- **合规使用**：遵守 B 站社区规则和当地法律；不要用自动化做垃圾评论、骚扰私信、刷量或未授权投稿。仅用于学习研究。

## 许可证

本项目自身使用 **MIT License**，见 [`LICENSE`](LICENSE)。Copyright (c) 2026 bilibili-skill contributors。

第三方依赖及许可证（以实际安装版本为准，发布前请用 `pip show <包名>` 或上游 `LICENSE` 核实）：

| 依赖 | 用途 | 许可证（以实际版本为准） |
|---|---|---|
| `bilibili-api-python` | B 站 API 客户端 | 公开资料多标注 GPL-3.0；发布前重点核实 |
| `mcp` | MCP Python SDK | MIT |
| `httpx` | HTTP 客户端 | BSD-3-Clause |
| `Pillow` | 图片处理 | HPND |
| `qrcode` | 登录二维码 | BSD |
| `aiohttp` | 异步 HTTP | Apache-2.0 |
| `av` | 视频抽帧 | BSD-3-Clause |
| `python-dotenv` | 可选依赖（启动器检查） | BSD-3-Clause |

如果 `bilibili-api-python` 实际为 GPL-3.0，分发包含它的组合作品可能需要遵守 GPL-3.0；仅保留 MIT 可能不够。详见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
