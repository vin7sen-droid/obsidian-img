# 安装 instagram-downloader（社媒下载：IG 主 + 抖音/微博/B站路由）

## 依赖

```bash
pip install instaloader gallery-dl yt-dlp     # IG 三引擎 + B站引擎；抖音/微博纯标准库
```

## 1. 装进 DSH

```bash
mkdir -p ~/.agents/skills && cp -r instagram-downloader ~/.agents/skills/
```

## 2. 装进 Claude Code

```bash
mkdir -p ~/.claude/skills && cp -r instagram-downloader ~/.claude/skills/
```

## 3. 装进 OpenClaw / OpenCode / 其它认 SKILL.md 的软件

放进它的 skills 目录，或把 SKILL.md 内容作为系统提示装载（保持 `scripts/` 同级）。

## 4. 验证

```bash
cd <本技能目录>/scripts
python3 social_dl.py "https://www.instagram.com/p/<任意公开短码>/"
# 或先看路由：python3 social_dl.py   （无参数会打印用法）
```

成功标志：`<下载根>/ins/<用户名>/` 出现 `用户名_短码_序号.jpg|mp4`。

## 5. 路径

| 项 | 默认 | 覆盖 |
|---|---|---|
| 下载根 | 手机 `/sdcard/Download`，其他设备 `~/Downloads` | `DOWNLOAD_ROOT` |
| IG 落点 | `<下载根>/ins/<用户名>/` | `--out` |
| IG cookie | `~/.config/instagram/cookies.json`（可选） | 删除即回免登录 |
| GitHub token（仅限流兜底） | `~/.config/ghclip/token`（可选，没有不影响） | — |

## 6. 卸载

删技能目录即可；下载的媒体与 `~/.config/instagram/cookies.json` 想清就一起删。
