---
name: instagram-downloader
description: 社媒媒体下载（Instagram 主）—— 下载 IG 帖子/Reel/Story 的图片视频（免登录+可选登录态+双引擎容错），统一入口 social_dl.py 还能自动识别抖音/微博/B站链接分发到对应下载器。用户发 Instagram/抖音/微博/B站 链接或问下载工具时用。
---

## 概览
下载社媒媒体到本机，按用户名/UP主归档。IG 链路：instaloader 主引擎 → gallery-dl 备胎 → yt-dlp 定性诊断；登录 cookie 存在时自动注入三引擎，可解锁受限 Reel/Story/私密帖。统一入口 `social_dl.py` 按链接自动分发四平台，各平台脚本也可独立运行。

## 命令
统一入口（推荐，自动识别平台）：
```bash
python3 <本技能目录>/scripts/social_dl.py "<链接>"
python3 <本技能目录>/scripts/social_dl.py -f urls.txt
```
单平台直用：
```bash
python3 <本技能目录>/scripts/ins_dl.py "<url> [<url> ...]"
python3 <本技能目录>/scripts/ins_dl.py -f urls.txt
```
抖音可直接甩 App 分享的整段文本（脚本自己抠短链）。

## 输出规则
- 下载根：`DOWNLOAD_ROOT` 环境变量，缺省手机 `/sdcard/Download`、其他设备 `~/Downloads`
- 目录：`<下载根>/ins|douyin|weibo|bilibili/<用户名或UP主>/`
- IG 文件名：`<用户名>_<shortcode>_<3位序号>.<jpg|mp4>`；幂等：已存在（>8KB）自动跳过；每图重试 3 次
- B站：`<下载根>/bilibili/<UP主>/<UP主>_<BV号>.mp4` + 同名弹幕 .xml

## 登录态（可选，仅 Instagram）
- cookie 文件：`~/.config/instagram/cookies.json`（600），格式 `{"sessionid":"xxx","csrftoken":"可选","ds_user_id":"可选"}`
- 存在即自动注入 instaloader/gallery-dl/yt-dlp；删除即回免登录
- 受限内容诊断：yt-dlp 报 `empty media response` = 需登录态；`not available` = 已删/私密

## 依赖
- IG 三引擎：`pip install instaloader gallery-dl yt-dlp`（douyin/weibo 纯标准库零依赖；B站依赖 yt-dlp 与 ffmpeg 合流时需要）
- 引擎升级：`python3 <本技能目录>/scripts/selfupdate.py`（升级→自检→失败自动回滚）

## 坑
- IG 页面 HTML 无 _sharedData/og:image/JSON-LD，只有登录墙；IG 风控 2026-07 起收紧（instaloader issue #2716）
- 免登录失败判据 `Fetching Post metadata failed` = 私密/已删/风控，三引擎同败时 yt-dlp 定性
- Story 链接（/stories/）被显式拦截，必须登录 cookie
- 大文件易超 60s 命令超时转后台，逐文件 urllib 需带 timeout
- 图直链下载需带 `Referer: https://www.instagram.com/`
- 抖音分享文本直接整段传入即可，不要自己抽链接

## 版本检查
各脚本启动时静默查 GitHub（`vin7sen-droid/obsidian-img` 仓库 `tools/<名>.json`，Contents API 直查），24h 缓存、同一版本只提醒一次、失败全静默，`--no-update-check` 可跳。**发版硬规则：只有 VERSION 递增才上传远端**；日常小修不抬版本不碰 GitHub。

## DSH 专属说明（其他软件装包时了解即可）
- 多机工具同步（gh_sync）与每日 09:00 自动升级定时任务是 DSH 工具链特性，**不随包分发**——包内脚本已加守卫，启动同步自动跳过，不影响下载。
- 版本检查照常可用（直查 GitHub 公开仓库，无需凭据）。
