# B 站工具索引

只在需要时看对应小节，不用一次读完。工具基础名都以 `bili_` 开头；宿主可能加前缀
（Claude Code 是 `mcp__bilibili__bili_*`），按当前工具列表里的实际名字调用。
写操作受每日额度限制（由宿主执行；MCP Server 本身不计数）。

## 登录

| 工具 | 用途 |
|------|------|
| `bili_login` | 生成登录二维码。只有用户明确要求登录时才调用；human_only，二维码必须由用户本人用 B 站 App 扫描 |
| `bili_login_check` | 扫码后轮询登录状态。`waiting` / `scanning` 时等几秒再查；`done` 表示凭证已保存 |

用户没要求登录时，不要自行发起登录：用 `bili_check_credential` 确认后结束这一轮即可。

未登录也能看公开内容：搜索、视频详情/概览/抽帧、弹幕、公开评论、UP 主资料和公开收藏夹内容都不要求登录，直接调用即可。例外是字幕接口和收藏夹列表——前者必须登录，未登录时 `bili_subtitle` 返回 `login_required: true`；后者未登录时 `bili_favorite_lists` 也返回 `login_required: true`。不要把它们当成工具故障。发评论、私信、发动态、投稿、查看自己的收藏夹和消息通知仍然需要登录。

## 先看这个

| 工具 | 用途 |
|------|------|
| `bili_check_credential` | 工具报未登录/凭证失效时用来确认；用户没要求登录就结束，不要主动登录 |
| `bili_unread_messages` | 未读私信、@、回复、点赞的总数 |
| `bili_recent_messages` | 未读私信会话和最近几条消息 |
| `bili_received_replies` | 收到的评论回复通知 |
| `bili_received_at_and_likes` | @ 提及和点赞通知 |

回复通知里的 `subject_id` 就是视频 av 号；只有它、没有 BV 号时，先调
`bili_video_info(aid=subject_id)` 拿到 `bvid`，再回复对应评论。

## 看内容

| 工具 | 用途 |
|------|------|
| `bili_search` | 按关键词搜视频，可按播放/最新/弹幕排序 |
| `bili_video_info` | 视频详情、播放量、分 P 时长；只有 av 号时传 `aid` 反查 BV 号 |
| `bili_video_overview` | 字幕 + 约每 3 秒一帧的联系表，看整体内容 |
| `bili_video_frames` | 按秒抽 1-3 张高清帧，读小字、代码、UI |
| `bili_subtitle` | 视频字幕（语音转文字） |
| `bili_danmaku` | 弹幕列表 |
| `bili_comments` | 热门评论，含子评论和配图 |
| `bili_crawl` | 搜索 + 评论 + 字幕一次拿完 |
| `bili_hot_videos` / `bili_hot_buzzwords` / `bili_weekly_hot` / `bili_rank` | 热门、热搜、每周必看、排行榜 |
| `bili_user_info` / `bili_user_videos` / `bili_space_dynamics` | UP 主资料、投稿、动态 |

视频超过 60 分钟会被工具直接拒绝；超过 20 分钟先私信主人征得同意。

## 写内容

| 工具 | 用途 |
|------|------|
| `bili_reply` | 发评论或回复评论；自己发主楼评论（rpid=0）有合适表情就带一张 |
| `bili_send_message` | 私信，有合适表情就带一张（也可带绘画产物） |
| `bili_send_sticker` | 私信发表情包，优先用现成的 |
| `bili_send_dynamic` | 发图文动态，每天最多 1 条 |
| `bili_send_opus` | 带标题的图文动态（标题作正文首行；当前不是专栏文章） |
| `bili_upload_video` / `bili_upload_video_multi` | 投稿；human_only，需人类发起；视频放 `data/uploads/`（或 `BILI_UPLOAD_DIR`） |
| `bili_favorite` / `bili_favorite_lists` / `bili_favorite_content` | 收藏管理 |
| `bili_sticker_list` | 列出本地表情包名字，挑表情前先看（只看名字，不要读图） |
| `bili_self_image` | 查看自己的形象立绘和描述 |
| `bili_generate_image` | 请求绘画 AI 生成配图（未配置时只发文字） |
| `bili_video_zones` | 查投稿分区 ID |
| `bili_activity_log` | 查看自己最近做过哪些操作 |

## 本机工具（不经过 MCP）

| 工具 | 用途 |
|------|------|
| `agent_avoid`（可选，宿主提供） | 把明确不欢迎你的楼层 / UP 主记进冷处理名单；没有就按 `rules.md` 自己避开 |

宿主也可以另外提供待处理清单工具；没有就按 `rules.md` 用文字或私信记录。
