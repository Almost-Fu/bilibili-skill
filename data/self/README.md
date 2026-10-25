# 自己的形象（可选）

这个目录里的素材属于个人内容，默认被 `.gitignore` 忽略，不会提交。

## 立绘

放一张图片，文件名必须是 `character`，支持 `.png` / `.jpg` / `.jpeg` / `.webp`：

```text
data/self/character.png
```

原图超过 1.2 MB 时，`bili_self_image` 会自动压一份 `character.view.jpg` 给模型看。

## 文字描述

新建 `character.md`（或 `character.txt`），写角色的外观、服装、画风等，供：

- `bili_self_image` 返回给模型；
- `bili_generate_image` 拼接进绘画提示词。

参考模板：

```markdown
（在这里写角色外观：发色、瞳色、服装、画风、气质等）
```

没有立绘和描述时，相关工具会提示未配置，其他功能不受影响。
