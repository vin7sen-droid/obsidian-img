#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""社媒媒体下载统一入口：发什么链接就自动用哪个引擎（2026-11-14）

用法:
    python3 social_dl.py "<链接或整段分享文本>" [更多...]
    python3 social_dl.py -f urls.txt

自动识别并分发:
    instagram.com / ig      -> ins_dl.py     （双引擎+登录态，落 /sdcard/download/ins/）
    douyin.com / iesdouyin  -> douyin_dl.py  （免签名，落 /sdcard/Download/douyin/）
    weibo.com / m.weibo.cn  -> weibo_dl.py   （免登录，落 /sdcard/Download/weibo/）

抖音可直接甩 App 分享的整段文本（脚本自己抠短链）；一次传多个、混合平台均可。
各平台脚本独立运行，输出与命名规则保持各自原样，互不影响。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

# 平台 -> (匹配正则, 脚本绝对路径)
ROUTES = [
    ("instagram", r"instagram\.com", "/root/workspace/ins_export/ins_dl.py"),
    ("douyin", r"(?:v\.douyin\.com|iesdouyin\.com|[a-z]+\.douyin\.com)",
     "/root/workspace/douyin_dl.py"),
    ("weibo", r"weibo\.(?:com|cn)", "/root/workspace/weibo_dl.py"),
]
TIMEOUT = 900  # 秒；大视频下载慢

# ---------- 版本检查提示（只提示不下载；24h 最多查一次；失败静默） ----------
VERSION = "1.1.2"
UPDATE_CHECK_URL = ("https://api.github.com/repos/vin7sen-droid/obsidian-img/"
                    "contents/tools/social_dl.json?ref=main")   # 直查 API：gh-proxy 有 CDN 缓存
CACHE_DIR = os.path.expanduser("~/.cache/social_dl")
CHECK_INTERVAL = 86400


def _ver_tuple(s):
    return tuple(int(p) if p.isdigit() else 0
                 for p in re.split(r"[.\-+]", str(s)))


def check_update():
    """远端 tools/social_dl.json 的 version 更高时打印 1-2 行提示；任何异常静默。"""
    cache_path = os.path.join(CACHE_DIR, "update.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}
    if time.time() - cache.get("checked_at", 0) < CHECK_INTERVAL:
        return
    try:
        hdrs = {"User-Agent": "social_dl", "Accept": "application/vnd.github.raw"}
        try:                                   # 有 token 用 token（5000/h），没有走匿名（60/h 够每日一查）
            hdrs["Authorization"] = "Bearer " + open("/root/.config/ghclip/token").read().strip()
        except Exception:
            pass
        req = urllib.request.Request(UPDATE_CHECK_URL, headers=hdrs)
        with urllib.request.urlopen(req, timeout=3) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(info, dict):
                return
    except Exception:
        return
    cache["checked_at"] = time.time()  # 只在请求成功后写，网络抖动不锁死
    latest = str(info.get("version") or "")
    if latest and _ver_tuple(latest) > _ver_tuple(VERSION):
        if cache.get("prompted") != latest:      # 同一版本只提醒一次，避免天天打扰
            print("! 有新版本 v%s（当前 v%s）— %s" % (latest, VERSION, info.get("note") or ""))
            if info.get("url"):
                print("  取新版: %s" % info["url"])
            cache["prompted"] = latest
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    except Exception:
        pass


def detect(text):
    """返回 (平台名, 脚本路径) 或 (None, None)"""
    for name, pat, script in ROUTES:
        if re.search(pat, text):
            return name, script
    return None, None


def run_one(text):
    name, script = detect(text)
    if not name:
        print(f"[跳过] 无法识别平台: {text[:80]}")
        print("       支持: instagram.com / douyin.com(含短链) / weibo.com|m.weibo.cn")
        return 1
    print(f"—— 识别为 {name}，调用 {script.split('/')[-1]}")
    try:
        r = subprocess.run([sys.executable, script, text], timeout=TIMEOUT)
        return r.returncode
    except subprocess.TimeoutExpired:
        print(f"[超时] {name} 下载超过 {TIMEOUT}s，手动重跑该链接可续传（已下部分自动跳过）")
        return 1


def _sync_bg():
    """异步触发 tools/gh_sync.py sync --quiet（方案 B：下载器启动即同步工具集），失败静默"""
    try:
        gs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "gh_sync.py")
        subprocess.Popen([sys.executable, gs, "sync", "--quiet"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    _sync_bg()   # 方案 B：下载器启动也触发工具同步（静默，失败不影响下载）
    argv = sys.argv[1:]
    if "--version" in argv:
        print("social_dl " + VERSION)
        return
    skip_update = "--no-update-check" in argv
    argv = [a for a in argv if a != "--no-update-check"]
    if not skip_update:
        check_update()
    texts = []
    if "-f" in argv:
        i = argv.index("-f")
        try:
            path = argv[i + 1]
        except IndexError:
            sys.exit("-f 后面要跟文件路径")
        with open(path, encoding="utf-8") as fh:
            texts += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        argv = argv[:i] + argv[i + 2:]
    texts += argv

    if not texts:
        print(__doc__)
        return

    fails = 0
    for t in texts:
        fails += run_one(t)
    if len(texts) > 1:
        print(f"—— 全部完成：{len(texts) - fails}/{len(texts)} 条成功")


if __name__ == "__main__":
    main()
