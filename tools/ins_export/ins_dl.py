#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instagram 帖子媒体下载器（免登录优先，可选登录态，双引擎容错）

用法:
    python3 ins_dl.py <url> [<url> ...]
    python3 ins_dl.py --out /sdcard/download/ins <url>
    python3 ins_dl.py -f urls.txt        # 每行一个链接

输出:
    <out>/<username>/<username>_<shortcode>_<3位序号>.<jpg|mp4>

登录态（可选，解锁 Story / 受限 Reel / 私密帖）:
    把 cookie 写到 ~/.config/instagram/cookies.json（权限 600），最少只需 sessionid:
    {"sessionid": "xxx", "csrftoken": "yyy", "ds_user_id": "zzz"}
    存在即自动注入 instaloader / gallery-dl / yt-dlp，删除文件即回到免登录模式。

引擎链: instaloader 失败 -> gallery-dl -> yt-dlp（仅诊断定性）
依赖: pip install instaloader gallery-dl yt-dlp（后两者缺失时自动跳过）
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import instaloader
except ImportError:
    sys.exit("缺少依赖，请先安装：pip3 install instaloader")

DEFAULT_OUT = "/sdcard/download/ins"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SHORTCODE_RE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
COOKIE_FILE = os.path.expanduser("~/.config/instagram/cookies.json")
GD_CONFIG = os.path.expanduser("~/.config/gallery-dl/config.json")

# instagram.com 侧需要登录的链接（/stories/）
STORY_RE = re.compile(r"/stories/([A-Za-z0-9_.]+)/(\d+)")

# ---------- 版本检查提示（只提示不下载；24h 最多查一次；失败静默） ----------
VERSION = "2.1.1"
UPDATE_CHECK_URL = ("https://api.github.com/repos/vin7sen-droid/obsidian-img/"
                    "contents/tools/ins_dl.json?ref=main")  # 直查 API，gh-proxy 有 CDN 缓存会滞后
GH_TOKEN_FILE = "/root/.config/ghclip/token"  # 可选：有则 5000 次/小时
CACHE_DIR = os.path.expanduser("~/.cache/ins_dl")
CHECK_INTERVAL = 86400


def _ver_tuple(s):
    return tuple(int(p) if p.isdigit() else 0
                 for p in re.split(r"[.\-+]", str(s)))


def check_update():
    """远端 tools/ins_dl.json 的 version 更高时打印 1-2 行提示；任何异常静默。"""
    cache_path = os.path.join(CACHE_DIR, "update.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}
    if time.time() - cache.get("checked_at", 0) < CHECK_INTERVAL:
        return
    try:
        hdrs = {"User-Agent": UA, "Accept": "application/vnd.github.raw"}
        try:  # 有 token 用 token（5000/h），没有走匿名（60/h 够每日一查）
            hdrs["Authorization"] = "Bearer " + open(GH_TOKEN_FILE).read().strip()
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
        if cache.get("prompted") != latest:  # 同一版本只提醒一次
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


def parse_shortcode(url):
    m = SHORTCODE_RE.search(url if url.endswith("/") else url + "/")
    return m.group(1) if m else None


def load_cookies():
    """读取登录 cookie；无文件或格式不对返回 None。"""
    try:
        with open(COOKIE_FILE, encoding="utf-8") as fh:
            ck = json.load(fh)
        if isinstance(ck, dict) and ck.get("sessionid"):
            return ck
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"WARN cookie 文件读取失败({exc})，按免登录继续", file=sys.stderr)
    return None


def apply_cookie_instaloader(ctx, ck):
    """把 cookie 注入 instaloader 的 requests session。"""
    sess = ctx._session
    for name in ("sessionid", "csrftoken", "ds_user_id", "x-ig-app-id"):
        val = ck.get(name) or ck.get(name.upper())
        if val:
            sess.cookies.set(name, val, domain=".instagram.com")
    print("  已注入登录 cookie（instaloader）")


def ensure_gallerydl_config(ck):
    """把 cookie 写进 gallery-dl 配置（存在 instagram 段则更新）。"""
    os.makedirs(os.path.dirname(GD_CONFIG), exist_ok=True)
    cfg = {}
    try:
        with open(GD_CONFIG, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {}
    ig = cfg.setdefault("extractor", {}).setdefault("instagram", {})
    changed = ig.get("cookies") != ck
    ig["cookies"] = ck
    if changed:
        with open(GD_CONFIG, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.chmod(GD_CONFIG, 0o600)
    print("  已注入登录 cookie（gallery-dl）")


def write_netscape_cookies(ck):
    """把 cookie 转成 Netscape 格式临时文件，供 yt-dlp --cookies 用。"""
    lines = ["# Netscape HTTP Cookie File"]
    for name, val in ck.items():
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t4102444800\t{name}\t{val}")
    fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    fh.write("\n".join(lines) + "\n")
    fh.close()
    return fh.name


def fetch_node_url(node, post):
    is_video = bool(getattr(node, "is_video", False))
    if is_video:
        url = getattr(node, "video_url", None) or getattr(post, "video_url", None)
    else:
        url = getattr(node, "url", None) or getattr(node, "display_url", None) \
            or getattr(post, "url", None)
    return url, is_video


def http_get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://www.instagram.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_media(media_url, dst, label, retries=3):
    for attempt in range(retries):
        try:
            data = http_get(media_url)
            with open(dst, "wb") as fh:
                fh.write(data)
            print(f"  {label}  {len(data) // 1024}KB")
            return True
        except Exception as exc:
            print(f"  重试 {label} 第 {attempt + 1} 次: {exc}", file=sys.stderr)
    return False


def download_instaloader(url, out_dir, ctx, skip_existing=True):
    """主引擎。返回 (folder, saved, ok)"""
    sc = parse_shortcode(url)
    if not sc:
        print(f"跳过（不是帖子链接）: {url}", file=sys.stderr)
        return None, 0, False
    try:
        post = instaloader.Post.from_shortcode(ctx, sc)
    except Exception as exc:
        print(f"  [instaloader] 取元数据失败 {sc}: {exc}", file=sys.stderr)
        return None, 0, False

    username = getattr(post, "owner_username", None) or sc
    folder = os.path.join(out_dir, username)
    os.makedirs(folder, exist_ok=True)
    nodes = list(post.get_sidecar_nodes()) if post.typename == "GraphSidecar" else [post]

    saved = 0
    for idx, node in enumerate(nodes):
        media_url, is_video = fetch_node_url(node, post)
        if not media_url:
            continue
        ext = ".mp4" if is_video else ".jpg"
        fname = f"{username}_{sc}_{idx:03d}{ext}"
        dst = os.path.join(folder, fname)
        if skip_existing and os.path.exists(dst) and os.path.getsize(dst) > 8192:
            print(f"  已存在，跳过 {fname}")
            saved += 1
            continue
        if download_media(media_url, dst, f"{username}/{fname}"):
            saved += 1
    print(f"  [instaloader] {username} -> {folder}（{saved}/{len(nodes)} 个文件）")
    return folder, saved, saved >= 1


def download_gallerydl(url, out_dir):
    """第二引擎：gallery-dl 子进程下载到临时目录，再按命名规则归位。
    返回 (folder, saved, ok)"""
    if shutil.which("python3") is None:
        return None, 0, False
    r = subprocess.run([sys.executable, "-m", "gallery_dl", "--version"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  [gallery-dl] 未安装，跳过该引擎", file=sys.stderr)
        return None, 0, False

    tmp = tempfile.mkdtemp(prefix="igdl_")
    try:
        # filename 模板：post_shortcode_序号（num 从 1 起）；目录用 username
        cmd = [sys.executable, "-m", "gallery_dl",
               "-d", tmp,
               "-o", 'directory="{username}"',
               "-o", 'filename="{post_shortcode}_{num:03d}.{extension}"',
               url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
            print(f"  [gallery-dl] 失败: {' / '.join(tail)}", file=sys.stderr)
            return None, 0, False

        # 扫描临时目录，按 <tmp>/<username>/<shortcode>_NNN.ext 归位
        saved = 0
        folder = None
        for root, _dirs, files in os.walk(tmp):
            username = os.path.basename(root)
            for f in sorted(files):
                m = re.match(r"([A-Za-z0-9_-]+)_(\d{3})\.(jpg|jpeg|png|mp4|webp)$", f)
                if not m:
                    continue
                sc, num, ext = m.group(1), int(m.group(2)), m.group(3)
                ext = ".mp4" if ext == "mp4" else ".jpg"
                folder = folder or os.path.join(out_dir, username)
                os.makedirs(folder, exist_ok=True)
                dst = os.path.join(folder, f"{username}_{sc}_{num - 1:03d}{ext}")
                shutil.move(os.path.join(root, f), dst)
                print(f"  [gallery-dl] {username}/{os.path.basename(dst)}")
                saved += 1
        if saved:
            print(f"  [gallery-dl] 完成 {saved} 个文件")
        return folder, saved, saved >= 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def diagnose_ytdlp(url, ck=None):
    """第三步：yt-dlp -j 只定性不下载，输出失败原因分类。"""
    cmd = [sys.executable, "-m", "yt_dlp", "-j", "--no-warnings", url]
    cookie_file = None
    if ck:
        cookie_file = write_netscape_cookies(ck)
        cmd += ["--cookies", cookie_file]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("  [yt-dlp 诊断] 超时", file=sys.stderr)
        return
    finally:
        if cookie_file:
            os.unlink(cookie_file)
    err = (r.stderr or "")
    if r.returncode == 0:
        print("  [yt-dlp 诊断] yt-dlp 可解析（罕见：可尝试直接用它下载视频类）", file=sys.stderr)
    elif "empty media response" in err:
        print("  [yt-dlp 诊断] Instagram 返回空媒体响应 = 该内容需登录态；"
              f"可把 sessionid 写入 {COOKIE_FILE}", file=sys.stderr)
    elif "Sign in to confirm" in err:
        print("  [yt-dlp 诊断] 需要登录验证（同上，提供 cookie 可解）", file=sys.stderr)
    elif "not available" in err.lower() or "unavailable" in err.lower():
        print("  [yt-dlp 诊断] 帖子不可访问（可能已删除/私密）", file=sys.stderr)
    else:
        first = err.strip().splitlines()[-1] if err.strip() else "未知错误"
        print(f"  [yt-dlp 诊断] {first[:120]}", file=sys.stderr)


def download_post(url, out_dir, ctx=None, cookies=None, skip_existing=True):
    if STORY_RE.search(url):
        user = STORY_RE.search(url).group(1)
        print(f"跳过（Story 链接；配登录 cookie 后用 instaloader 的 Story 模块）: {url}",
              file=sys.stderr)
        return None, 0

    folder, saved, ok = download_instaloader(url, out_dir, ctx, skip_existing)
    if ok:
        return folder, saved

    # 第二引擎
    folder2, saved2, ok2 = download_gallerydl(url, out_dir)
    if ok2:
        return folder2, saved2

    # 诊断
    diagnose_ytdlp(url, cookies)
    return folder, saved


def _sync_bg():
    """异步触发 tools/gh_sync.py sync --quiet（方案 B：下载器启动即同步工具集），失败静默"""
    try:
        gs = "/root/workspace/tools/gh_sync.py"
        subprocess.Popen([sys.executable, gs, "sync", "--quiet"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    _sync_bg()   # 方案 B：下载器启动也触发工具同步（静默，失败不影响下载）
    ap = argparse.ArgumentParser(
        description="Instagram 帖子媒体下载器（免登录优先，双引擎容错）")
    ap.add_argument("urls", nargs="*", help="帖子 / Reel 链接，可多个")
    ap.add_argument("-f", "--file", help="从文件读链接，每行一个")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"输出根目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--force", action="store_true", help="已存在也重新下载")
    ap.add_argument("--version", action="version", version="ins_dl " + VERSION)
    ap.add_argument("--no-update-check", action="store_true", help="跳过远端版本检查")
    args = ap.parse_args()
    if not args.no_update_check:
        check_update()

    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            urls += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not urls:
        ap.print_help()
        return

    cookies = load_cookies()
    ctx = instaloader.Instaloader().context
    if cookies:
        apply_cookie_instaloader(ctx, cookies)
        ensure_gallerydl_config(cookies)
    else:
        print("（免登录模式；受限内容会失败，cookie 用法见文件头注释）", file=sys.stderr)

    total = 0
    for url in urls:
        folder, n = download_post(url, args.out, ctx=ctx, cookies=cookies,
                                  skip_existing=not args.force)
        total += n
    print(f"完成，共保存 {total} 个文件 -> {args.out}")


if __name__ == "__main__":
    main()
