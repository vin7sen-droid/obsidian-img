#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ReadItLater Inbox 里的图片上传到 GitHub obsidian-img/clip，替换成 gh-proxy 链接。

用法：
  python3 tools/gh_img.py --dry      # 只统计，不下载不改文件
  python3 tools/gh_img.py            # 全量扫 Inbox，只处理新增 URL
  python3 tools/gh_img.py --file "某篇.md"   # 只处理指定笔记
"""
import argparse, base64, hashlib, json, re, sys, time, urllib.error
import urllib.parse, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

INBOX = Path("/sdcard/Obsidian/dsh/ReadItLater Inbox")
TOK = Path("/root/.config/ghclip/token").read_text().strip()
OWNER, REPO, BRANCH, SUBDIR = "vin7sen-droid", "obsidian-img", "main", "clip"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
MAP_FILE = Path("/root/workspace/bak/img_map.json")

# 图片链接前缀模板（与 tools/img_prefix.py 保持同步）
CDN_TPL = "https://gh-proxy.com/https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

MD_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")

def _extract(text):
    """抽出还没托管到本仓库的图片 URL。"""
    urls = MD_IMG.findall(text) + HTML_IMG.findall(text)
    return [u for u in urls if "vin7sen-droid/obsidian-img" not in u]
HTML_IMG = re.compile(r"<img[^>]*?src=[\"']?(https?://[^\"'\s>]+)")


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method,
        headers={"Authorization": "Bearer " + TOK, "User-Agent": "clip/1.0",
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def ext_of(b):
    if b[:3] == b"\xff\xd8\xff": return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n": return ".png"
    if b[:6] in (b"GIF87a", b"GIF89a"): return ".gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return ".webp"
    if b[:2] == b"BM": return ".bmp"
    return ".jpg"


def _ascii(url):
    p = urllib.parse.urlsplit(url)
    netloc = p.netloc.encode("idna").decode() if p.netloc else p.netloc
    return urllib.parse.urlunsplit((p.scheme, netloc, urllib.parse.quote(p.path), p.query, ""))


def orig_url(u):
    """wsrv.nl 代理链接 → 源站原图 URL；其他原样返回。"""
    p = urllib.parse.urlsplit(u)
    if "wsrv.nl" in p.netloc:
        q = urllib.parse.parse_qs(p.query)
        if q.get("url"):
            return q["url"][0]
    return u


def download(url):
    """下载图片；wsrv.nl 返 400 时回退原图直连。"""
    for attempt in range(3):
        try:
            u = url
            hdr = {"User-Agent": UA}
            if "wsrv.nl" in urllib.parse.urlsplit(url).netloc:
                orig = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("url", [None])[0]
                if orig:
                    u = orig
            p = urllib.parse.urlsplit(u)
            hdr["Referer"] = p.scheme + "://" + p.netloc + "/"
            with urllib.request.urlopen(urllib.request.Request(_ascii(u), headers=hdr), timeout=45) as r:
                b = r.read()
            if len(b) < 100:
                raise RuntimeError("too small %d" % len(b))
            return b
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5)


def upload(data, name):
    path = "%s/%s" % (SUBDIR, name)
    p = "/repos/%s/%s/contents/%s" % (OWNER, REPO, urllib.parse.quote(path))
    try:
        api(p, "GET")
        return "exists"
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {"message": "add clip image %s" % name, "branch": BRANCH,
            "content": base64.b64encode(data).decode()}
    for attempt in range(3):
        try:
            api(p, "PUT", body)
            return "uploaded"
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def gif_url(name):
    """GIF 走 wsrv.nl（转 webp 动图，体积和加载都快很多）。"""
    raw = "https://raw.githubusercontent.com/%s/%s/%s/%s/%s" % (OWNER, REPO, BRANCH, SUBDIR, name)
    return "https://wsrv.nl/?url=" + urllib.parse.quote(raw, safe="") + "&output=webp"


def wsrv_orig(u):
    """GIF 不存档：直接让 wsrv 代理源站原图（转 webp 动图，体积/加载都快）。"""
    return "https://wsrv.nl/?url=" + urllib.parse.quote(u, safe="") + "&output=webp"


LIMIT = 300 * 1024


def compress_img(b, limit=LIMIT):
    """超 limit 的图压到 limit 以下。优先级：无损重编码 → 缩尺寸（保高质量）→ 才降质量，最低到 50。

    GIF 不压（交给源站原图）；带透明走 webp。
    """
    if len(b) <= limit:
        return b, "orig", ""
    try:
        import io
        from PIL import Image
    except Exception:
        return b, "nopil", ""
    try:
        im = Image.open(io.BytesIO(b))
        fmt = (im.format or "JPEG").upper()
        if fmt == "GIF":
            return b, "gif", ""
        im.load()
        if im.mode == "P":
            im = im.convert("RGBA") if "transparency" in im.info else im.convert("RGB")
        alpha = im.mode in ("RGBA", "LA")

        def enc(img, q):
            out = io.BytesIO()
            if alpha and img.mode not in ("RGBA", "LA"):
                img = img.convert("RGBA")
            if alpha:
                img.save(out, "WEBP", quality=q, method=6)
            else:
                img.convert("RGB").save(out, "JPEG", quality=q, optimize=True, progressive=True)
            return out.getvalue()

        # 1) 无损重编码（质量 95，只靠 optimize/progressive 省体积）
        d = enc(im, 95)
        if len(d) <= limit:
            return d, "optimize", ""

        # 2) 缩尺寸 + 保住画质（q 不低于 65）
        for sc in (0.85, 0.75, 0.65, 0.55, 0.45, 0.35):
            w, h = im.size
            im2 = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.LANCZOS)
            for q in (85, 75, 65):
                d = enc(im2, q)
                if len(d) <= limit:
                    return d, "scale%.2f-q%d" % (sc, q), ""

        # 3) 兜底：降到 q50
        for q in (55, 50):
            d = enc(im2, q)
            if len(d) <= limit:
                return d, "scale%.2f-q%d" % (sc, q), ""
        return d, "fallback", ""
    except Exception as e:
        return b, "err:%s" % type(e).__name__, ""


def ext_of_name(n):
    return ("." + n.rsplit(".", 1)[-1]) if "." in n else ""


def cdn(name):
    return CDN_TPL.format(owner=OWNER, repo=REPO, branch=BRANCH, path="%s/%s" % (SUBDIR, name))


def _load_cache():
    if MAP_FILE.exists():
        try:
            return json.loads(MAP_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(c):
    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAP_FILE.write_text(json.dumps(c, ensure_ascii=False, indent=1))


def _default_backup(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("clipmod", "/root/workspace/tools/clip.py")
    clip = importlib.util.module_from_spec(spec); spec.loader.exec_module(clip)
    clip._backup_or_die(path)


def process_files(files, workers=6, quiet=False, backup=None):
    """处理给定 md 文件里的图片：下载 → 上传 GitHub → 替换链接。返回 (ok, fail, changed)。"""
    urls, per_file = set(), {}
    for f in files:
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found = _extract(t)
        if found:
            per_file[f] = found
            urls.update(found)
    if not urls:
        return 0, 0, 0

    cache = _load_cache()
    todo = [u for u in urls if u not in cache or cache[u].get("error")]
    ok = fail = 0

    def work(u):
        try:
            b0 = download(orig_url(u))
            if ext_of(b0) == ".gif":
                return u, {"gif": True, "url": u, "size": len(b0)}
            b, how, _ = compress_img(b0)
            h = hashlib.sha1(b).hexdigest()[:12] + ext_of(b)
            st = upload(b, h)
            url = gif_url(h) if ext_of(b) == ".gif" else cdn(h)
            return u, {"name": h, "url": url, "status": st, "size": len(b),
                       "orig_size": len(b0), "how": how}
        except Exception as e:
            return u, {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for u, r in ex.map(work, todo):
                cache[u] = r
                if "error" in r:
                    fail += 1
                    if not quiet:
                        print("  图片失败 %s | %s" % (u[:70], r["error"]))
                elif r.get("gif"):
                    if not quiet:
                        print("  GIF 不存档，走 wsrv：%s" % u[:70])
                else:
                    ok += 1
                    if not quiet and r.get("how") not in ("orig", "", None):
                        print("  压缩 %.0f→%.0fKB (%s)" % (r.get("orig_size", 0) / 1024, r["size"] / 1024, r["how"]))
        _save_cache(cache)

    # 替换链接
    changed = 0
    for f, us in per_file.items():
        t = f.read_text(encoding="utf-8")
        new = t
        for u in us:
            r = cache.get(u) or {}
            if r.get("gif"):
                continue  # GIF 保留原网址，不上传不改写
            elif r.get("name"):
                new = new.replace(u, gif_url(r["name"]) if ext_of_name(r["name"]) == ".gif" else cdn(r["name"]))
        if new != t:
            (backup or _default_backup)(str(f))
            f.write_text(new, encoding="utf-8")
            changed += 1
    return ok, fail, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--file", action="append")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    files = [Path(x) for x in a.file] if a.file else sorted(INBOX.glob("*.md"))
    print("扫描 %d 篇笔记" % len(files))
    urls = set()
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        urls.update(_extract(t))
    print("去重图片 URL %d 个" % len(urls))
    if a.dry:
        cache = _load_cache()
        print("未缓存 %d 个" % len([u for u in urls if u not in cache]))
        return
    ok, fail, changed = process_files(files, workers=a.workers)
    print("上传成功 %d 失败 %d；替换 %d 篇笔记" % (ok, fail, changed))


if __name__ == "__main__":
    main()
