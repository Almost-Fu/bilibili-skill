# 表情包库

仓库不自带表情包；把图片放进这个目录后，名字才会出现在 `bili_sticker_list` 里。
这个目录里的图片属于个人素材，默认被 `.gitignore` 忽略，不会提交。

文件名就是表情名，Agent 只按名字挑图，不看图片内容。

## 规则

- 支持 `.png` / `.jpg` / `.jpeg` / `.webp`，建议 PNG、正方形、512×512。
- 名字用情绪或场景，短一点：`骄傲.png`、`开心.png`、`无语.png`。
- 名字里不要有 `/ \ : * ? " < > |`。
- 单张不要超过 8MB（B 站私信上限）。

## 加新表情

```bash
# 直接把图片放进这个目录也行，文件名就是表情名
python bili_stickers.py add 下载的图片.png 得意 --size 512
python bili_stickers.py list
```

`add` 会自动裁成正方形、缩到 512、转成 PNG。

## Agent 怎么用

- `bili_sticker_list` → 返回名字列表（纯文字，不读图）
- `bili_send_sticker(receiver_uid, sticker="骄傲", text="")` → 发送

发表情计入私信配额，和普通私信共用每日上限。
