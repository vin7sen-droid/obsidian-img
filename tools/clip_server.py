#!/usr/bin/env python3
"""剪贴板剪藏的本地入口：给 MacroDroid 之类的自动化应用一个 HTTP 触发点。

    GET /clip?url=<URL>      剪藏该链接（默认任意站点；只接受 http/https）
    GET /clip?url=<URL>&tags=%23a%20%23b&summary=...   可覆盖标签/摘要
    GET /refresh                刷新所有 IT之家 笔记的评论
    GET /ping                健康检查

只监听 127.0.0.1，外部网络访问不到。同名链接 10 分钟内不会重复剪。
"""
import io, json, http.client, os, re, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIP_PY = "/root/workspace/tools/clip.py"
PY = "/usr/local/bin/python3"
PORT = 8899
RECENT = {}
LOCK = threading.Lock()

# 剪贴板里常带「标题 + 链接」或多行文字，直接从原始 query 里捞第一个 http(s) 链接，
# 不依赖客户端是否做了 URL 编码（MacroDroid 的 {clipboard} 有时不编码，导致 400）。
URL_RE = re.compile(r"https?://[^\s\"'<>()（）「」【】，。；、]+")


def pick_url(query):
    raw = urllib.parse.unquote(query)
    seg = raw.split("url=", 1)[1] if "url=" in raw else raw
    for marker in ("&tags=", "&summary="):
        if marker in seg:
            seg = seg.split(marker, 1)[0]
    m = URL_RE.search(seg)
    return m.group(0).rstrip(".,;:!?").strip() if m else ""


def run_clip(url, tags=None, summary=None):
    cmd = [PY, CLIP_PY, url, "--auto"]
    if tags:
        cmd += ["--tags", tags]
    if summary:
        cmd += ["--summary", summary]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        return False, (p.stderr or "").strip().split("\n")[-1][:200]
    lines = (p.stdout or "").strip().split("\n")
    return True, lines[0] if lines else ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # 宽容解析请求行：MacroDroid 把剪贴板原样拼进 URL 时，中文和空格会直接出现在
    # 请求行里（「11 月 9 日」），标准解析按空格切分会判成语法错误 400。
    # 这里改为：第一个空白前是 method、末尾可选的 HTTP/x.y 是版本、中间整段都是 path。
    def parse_request(self):
        self.command = None
        self.request_version = "HTTP/1.0"
        self.close_connection = True
        # handle_one_request 已把请求行读进 raw_requestline，这里不要再 readline
        line = getattr(self, "raw_requestline", None) or self.rfile.readline(65537)
        if not line:
            return False
        self.raw_requestline = line
        parts = [line.decode("utf-8", "replace").rstrip("\r\n")]
        # 抖音/小红书等分享文本含换行：HTTP 请求行在第一个换行处就结束，后续行会被
        # 当成 header 行 → BaseHTTP 解析失败 400。这里把后续行续接进请求行，直到拿到
        # 版本号 HTTP/x.y 或遇到看起来像 header 的行（含冒号）为止。
        guard = 0
        pre = None
        while guard < 30 and not re.search(r"\s+HTTP/\d(?:\.\d)?\s*$", parts[-1]):
            guard += 1
            nxt = self.rfile.readline(65537)
            if not nxt:
                break
            seg = nxt.decode("utf-8", "replace").rstrip("\r\n")
            if not seg or (re.match(r"^[A-Za-z][A-Za-z0-9-]*:", seg)
                           and not re.match(r"^https?://", seg)):
                pre = seg
                break
            parts.append(seg)
        text = " ".join(parts).strip()
        self.requestline = text
        m = re.match(r"^(\S+)\s+(.*?)(?:\s+(HTTP/\d(?:\.\d)?))?\s*$", text)
        if not m:
            self.send_error(400, "Bad request syntax (%r)" % text)
            return False
        self.command = m.group(1)
        path = m.group(2)
        if m.group(3):
            self.request_version = m.group(3)
        self.path = urllib.parse.quote(path, safe="/?&=%:@!$'()*+,;-._~[]#")
        # 剩余 headers：把续接时吞掉的那行放回去，再读完剩余直到空行
        hbuf = (pre + "\r\n").encode("utf-8") if pre else b""
        while True:
            ln = self.rfile.readline(65537)
            if not ln or ln in (b"\r\n", b"\n"):
                break
            hbuf += ln
        self.headers = http.client.parse_headers(io.BytesIO(hbuf))
        return True

    def do_POST(self):
        """POST /clip，body 为纯文本分享内容——换行不会破坏请求行，比 GET 稳。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        body = self.rfile.read(n).decode("utf-8", "replace") if n > 0 else ""
        if not body.strip():
            return self._json(400, {"ok": False, "error": "空的请求体",
                                    "hint": "MacroDroid 的 POST 请求体里应填 {shared_text}"})
        self.path = "/clip?url=" + urllib.parse.quote(body, safe="")
        return self.do_GET()

    def _json(self, code, obj):
        try:
            if getattr(self, "path", "") != "/ping":
                with open("/tmp/clip_server.log", "a", encoding="utf-8") as f:
                    f.write("%s   ↳ %s %s\n" % (time.strftime("%m-%d %H:%M:%S"), code,
                                                json.dumps(obj, ensure_ascii=False)[:220]))
        except Exception:
            pass
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/ping":
            return self._json(200, {"ok": True, "service": "clip", "port": PORT})
        # 记一条请求日志，排查「到底有没有触发到服务」用
        try:
            with open("/tmp/clip_server.log", "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"),
                                     urllib.parse.unquote(self.path)[:300]))
        except Exception:
            pass
        if u.path == "/refresh":
            try:
                p = subprocess.run([PY, "/root/workspace/tools/refresh_all_comments.py"],
                                   capture_output=True, text=True, timeout=600)
                tail = [l for l in (p.stdout or "").strip().split("\n") if "有变化" in l]
                return self._json(200 if p.returncode == 0 else 500,
                                  {"ok": p.returncode == 0, "detail": (tail or [""])[-1]})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:200]})

        if u.path == "/douyin":
            text = urllib.parse.unquote(u.query)
            if not text.strip():
                return self._json(400, {"ok": False, "error": "缺少抖音链接",
                                        "hint": "用法：/douyin?url=<分享链接或含链接的文本>"})
            if "{" in text or "*" in text:
                return self._json(400, {"ok": False, "error": "收到的是未替换的变量",
                                        "got": text[:120],
                                        "hint": "MacroDroid 的「测试」按钮不解析变量；分享触发器的动作 URL 应写 /douyin?url={shared_text}，且必须真的从抖音分享过来才生效"})
            if "v.douyin.com" not in text:
                return self._json(400, {"ok": False, "error": "文本里没有抖音短链",
                                        "got": text[:120],
                                        "hint": "需要含 v.douyin.com/xxxx 的完整分享文本"})
            try:
                p = subprocess.run([PY, "/root/workspace/douyin_dl.py", text],
                                   capture_output=True, text=True, timeout=900)
                lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
                ok = p.returncode == 0 and any(l.startswith("「") for l in lines)
                detail = lines[-12:] or [(p.stderr or "").strip()[-300:]]
                return self._json(200 if ok else 500, {"ok": ok, "detail": detail})
            except Exception as e:
                return self._json(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})

        if u.path == "/last":
            try:
                with open("/tmp/clip_server.log", encoding="utf-8", errors="ignore") as f:
                    tail = [l.rstrip("\n") for l in f.readlines()[-6:]]
            except Exception:
                tail = []
            return self._json(200, {"ok": True, "recent": tail})

        if u.path != "/clip":
            return self._json(404, {"ok": False, "error": "not found"})

        url = pick_url(u.query)
        if not url:
            raw = urllib.parse.unquote(u.query)[:200]
            hint = "MacroDroid 变量未替换——请检查该宏的触发器类型与变量名是否匹配"
            if "{" not in raw and "*" not in raw:
                hint = "请求里没有可识别的 http(s) 链接"
            return self._json(400, {"ok": False, "error": "缺少或非法的 url",
                                    "hint": hint, "got": raw})

        # 抖音链接：不剪藏，转下载器（分享文本里带长链或短链都能处理）
        if re.search(r'(v\.douyin\.com|douyin\.com|iesdouyin\.com)', url):
            try:
                raw_text = urllib.parse.unquote(u.query)
                p = subprocess.run([PY, "/root/workspace/douyin_dl.py", raw_text],
                                   capture_output=True, text=True, timeout=900)
                lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
                skipped = any("已下载过" in l for l in lines)
                ok = p.returncode == 0 and (any(l.startswith("「") for l in lines) or skipped)
                return self._json(200 if ok else 500,
                                  {"ok": ok, "kind": "douyin", "skipped": skipped,
                                   "detail": lines[-12:] or [(p.stderr or "").strip()[-300:]]})
            except Exception as e:
                return self._json(500, {"ok": False, "kind": "douyin",
                                        "error": "%s: %s" % (type(e).__name__, str(e)[:200])})

        # Instagram：不剪藏，转图片/视频下载器（按用户名建文件夹）
        if re.search(r'instagram\.com/(?:p|reel|reels|tv)/', url):
            HINT = {
                "login_required": "Instagram 要求登录才能看这条——匿名会话被挡，需配置登录 cookie",
                "private": "私密账号，未关注者匿名下载不到",
                "not_found": "帖子不存在或已删除",
                "rate_limited": "请求太频繁被限流，等几分钟再试",
                "network": "连不上 Instagram（网络/封锁）",
                "media": "帖子拿到了，但图片 CDN 下载失败",
                "other": "未知错误，看 errors 字段",
            }
            try:
                p = subprocess.run([PY, "/root/workspace/instagram_downloader_final.py", url],
                                   capture_output=True, text=True, timeout=300)
                lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
                fails = [l.split("\t") for l in (p.stderr or "").splitlines() if l.startswith("FAIL\t")]
                ok = p.returncode == 0 and any(" files)" in l for l in lines)
                kind = fails[0][2] if fails and len(fails[0]) > 2 else ("other" if not ok else "")
                if not ok:
                    rec = "%s | %s | %s\n" % (time.strftime("%m-%d %H:%M:%S"), url, kind)
                    try:
                        with open("/sdcard/Download/ins/_failed.log", "a", encoding="utf-8") as f:
                            f.write(rec)
                    except Exception:
                        pass
                out = {"ok": ok, "kind": "instagram", "detail": lines[-6:]}
                if not ok:
                    out["error"] = kind
                    out["hint"] = HINT.get(kind, HINT["other"])
                    out["errors"] = ["\t".join(x) for x in fails][:4]
                return self._json(200 if ok else 502, out)
            except Exception as e:
                return self._json(500, {"ok": False, "kind": "instagram",
                                        "error": "%s: %s" % (type(e).__name__, str(e)[:200])})

        key = url
        now = time.time()
        with LOCK:
            last = RECENT.get(key, 0)
            if now - last < 600:
                return self._json(200, {"ok": True, "skipped": "10 分钟内已剪过", "url": url})
            RECENT[key] = now

        tags = (q.get("tags") or [None])[0]
        summary = (q.get("summary") or [None])[0]
        try:
            ok, info = run_clip(url, tags, summary)
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)[:200]})
        name = os.path.basename(info).replace(".md", "") if ok else ""
        self._json(200 if ok else 500,
                   {"ok": ok, "note": name, "detail": info if ok else info})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("clip server on 127.0.0.1:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()