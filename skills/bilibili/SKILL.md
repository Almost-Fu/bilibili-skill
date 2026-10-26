---
name: bilibili
description: 逛 B 站：搜索和观看视频、读评论/字幕/弹幕、发评论与私信、管理收藏。只在用户明确提到 B 站、bilibili、UP 主、看视频、评论、私信、投稿时使用；平时不要加载。
---

# 逛 B 站

这是一个按需加载的能力。没进入这个场景时，只需要知道「有逛 B 站的能力」，不需要读下面的规则和工具说明。

## 进入场景

1. 确认 MCP Server `bilibili` 已连接。未连接时，按 `mcp.json.example` 配置后重试；启动脚本用 `run_mcp.py`，它会在启动前检查仓库根和依赖（`python skills/bilibili/run_mcp.py --check`）。不要自行发起登录；只有用户明确要求登录时，才按 `instructions/tools.md` 的「登录」一节调用 `bili_login`，二维码必须由用户本人扫描。未登录也能读公开内容（搜索、视频详情/概览/抽帧、弹幕、评论、UP 主资料、公开收藏夹内容）；字幕、收藏夹列表、互动、发布、私信和消息通知才需要登录。
2. 读取这两份文件，之后按它们行事：
   - `instructions/persona.md` —— 在这个账号里你是谁、怎么说话
   - `instructions/rules.md` —— 完整行为规则（必须遵守）
3. 需要查工具名/参数时读 `instructions/tools.md`；巡检唤醒读 `instructions/patrol.md`。
4. 工具基础名都以 `bili_` 开头；宿主可能加命名空间前缀（Claude Code 是 `mcp__bilibili__bili_*`），按当前工具列表里的实际名字调用。写入类工具受每日额度限制；额度、白名单和投稿审批由宿主按 `skill.json` 的 `limits` / `tools.human_only` 执行，MCP Server 本身不计数。

## 退出场景

不再调用 `bili_*` 工具，也不再引用规则细节。上下文里只保留「有这个能力」，等下次需要时再重新加载。
