"""工具分类清单。

`mcp_server.py` 注册工具，技能清单通过 `skills/bilibili/skill.json` 的
`tools.write` / `tools.message` / `tools.human_only` 声明哪些算写入、哪些只能由
人类发起。两边必须对同一批名字达成一致，所以名单只写在这里一份——
`tests/test_tool_manifest.py` 会核对技能清单与本文件是否一致。

新增工具时：先在 `mcp_server.py` 注册，再把它放进下面三个集合之一。
`tests/test_tool_manifest.py` 会逐项核对注册列表，漏分类会直接测试失败，
不会出现“新写的写工具被当成只读、绕过额度”的静默问题。
"""

from __future__ import annotations

# 会改变账号状态的工具：评论、私信、动态、收藏
WRITE_TOOL_NAMES = frozenset(
    {
        "bili_reply",
        "bili_favorite",
        "bili_send_message",
        "bili_send_sticker",
        "bili_send_dynamic",
        "bili_send_opus",
    }
)

# 只读工具：查询、采集、本地生图，不改变账号状态
READ_ONLY_TOOL_NAMES = frozenset(
    {
        "bili_check_credential",
        "bili_login_check",
        "bili_search",
        "bili_comments",
        "bili_subtitle",
        "bili_danmaku",
        "bili_video_info",
        "bili_video_overview",
        "bili_video_frames",
        "bili_crawl",
        "bili_self_image",
        "bili_generate_image",
        "bili_video_zones",
        "bili_hot_videos",
        "bili_hot_buzzwords",
        "bili_weekly_hot",
        "bili_rank",
        "bili_user_info",
        "bili_user_videos",
        "bili_space_dynamics",
        "bili_favorite_lists",
        "bili_favorite_content",
        "bili_sticker_list",
        "bili_unread_messages",
        "bili_recent_messages",
        "bili_received_replies",
        "bili_received_at_and_likes",
        "bili_activity_log",
    }
)

# 必须由人类发起的工具：扫码登录会生成二维码并建立登录会话；
# 视频投稿发布前需要人类确认，因此也不由 Agent 自主调用。
# `bili_login_check` 只是轮询扫码结果，不建立会话，属于只读工具——
# 否则宿主拦下它，用户扫码后登录流程会卡住。
HUMAN_ONLY_TOOL_NAMES = frozenset(
    {
        "bili_login",
        "bili_upload_video",
        "bili_upload_video_multi",
    }
)

# 私信类写入（发表情也算一次私信），单独一套配额
MESSAGE_TOOL_NAMES = frozenset({"bili_send_message", "bili_send_sticker"})

ALL_TOOL_NAMES = WRITE_TOOL_NAMES | READ_ONLY_TOOL_NAMES | HUMAN_ONLY_TOOL_NAMES
