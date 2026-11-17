#!/usr/bin/env python3
"""把网页正文（+ IT之家评论）抓成 Obsidian 剪藏笔记，落到 ReadItLater Inbox。

用法:
    python3 tools/clip.py <url> [--out DIR] [--no-comments] [--tags "a b c"] [--name 文件名]

约定（与既有剪藏一致）:
    YAML frontmatter（created/url/author/tags）→ # 标题 → 摘要 → 正文 → --- → ## 评论区
    IT之家额外抓评论区：加粗条目头 + 正文段落（字号与正文一致，不用标题/引用块）

2026-11-16 优化（备份 bak/clip.py.0516.bak，改完新旧输出对同一篇逐字节一致）:
    ① safe_name()：文件名先反转义 HTML 实体（&#183; 曾原样落进文件名）、压连续空格、
       按 240 字节截断（/sdcard 是 FUSE，255 字节上限，长中文标题会 OSError）
    ② meta()：改为一次解析整页 meta 成 dict，兼容单双引号与属性任意顺序（原实现只认双引号）
    ③ fetch()：Content-Encoding 用子串判断（原 == "gzip"，遇 "GZIP"/"gzip, br" 漏解压成乱码），
       顺带支持 deflate
    ④ _host_blocked()：探图改 HEAD + 8s 超时（原 GET + 15s，十张图的稿子白付十次下载），
       405 时回落 GET
    ⑤ clean_body() 去掉对已清洗正文的二次调用（双跑取长路径原来对 8 万字白跑一遍全量正则）
    ⑥ pubtime 解析加 try/except（格式一变就 ValueError 让整次剪藏崩）
    ⑦ tags 上限 2 → 3（标签规则本就是 1-3 个，tags[:2] 丢用户显式给的第三个）
    ⑧ --stdout 不再触发 gh_sync 后台推送（只读路径不该有写侧副作用）
    ⑨ 文件读写统一 _read_text/_write_text（with 语义；刻意不做 tmp+replace——换 inode 会动
       Obsidian 的创建时间排序）
"""
import argparse, gzip, hashlib, html as htmllib, json, os, re, shutil, subprocess, sys, time, zlib
import urllib.request
import trans
from datetime import datetime, timedelta, timezone

try:
    import trafilatura
except Exception:
    trafilatura = None

UA = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
DEFAULT_OUT = "/sdcard/Obsidian/dsh/ReadItLater Inbox"

BAK_DIR = "/root/workspace/bak/inbox"
# 第二份放在手机存储：proot 环境重置/清理 App 数据时这份还在
BAK_MIRROR = "/sdcard/Download/DSH-Go/inbox-bak"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup(path):
    """覆盖任何已存在笔记前，先存一份带时间戳的副本；返回副本路径或 None。

    安全优先：每次覆盖都存一份，不做内容去重（重复副本由 24 小时清理兜底）。
    同一秒内多次备份：加 -2/-3 后缀，避免互相覆盖。
    """
    try:
        if not os.path.exists(path):
            return None
        os.makedirs(BAK_DIR, exist_ok=True)
        base = os.path.basename(path)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = "%s__%s" % (ts, base)
        dst = os.path.join(BAK_DIR, name)
        n = 2
        while os.path.exists(dst):
            name = "%s-%d__%s" % (ts, n, base)
            dst = os.path.join(BAK_DIR, name)
            n += 1
        shutil.copy2(path, dst)
        try:
            os.makedirs(BAK_MIRROR, exist_ok=True)
            shutil.copy2(path, os.path.join(BAK_MIRROR, name))
        except Exception:
            print("警告：备份镜像写入失败（主备份已成功）：%s" % BAK_MIRROR)
        _prune_bak()
        return dst
    except Exception as e:
        print("备份失败：%s" % e)
        return None


def _backup_or_die(path):
    """覆盖前必须备份成功；备份失败直接中止，绝不静默覆盖。"""
    if not os.path.exists(path):
        return None
    dst = _backup(path)
    if not dst:
        sys.exit("备份失败，已中止写入（原文件未改动）：%s" % path)
    return dst


BAK_KEEP_HOURS = 24


def _prune_bak():
    """只清理超过 24 小时的旧备份；年龄按文件名里的时间戳算（副本 mtime 保留原文件时间）。"""
    try:
        now = datetime.now()
        for fn in os.listdir(BAK_DIR):
            m = re.match(r"(\d{8}-\d{6})(?:-\d+)?__", fn)
            if not m:
                continue
            try:
                born = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            if (now - born).total_seconds() > BAK_KEEP_HOURS * 3600:
                for d in (BAK_DIR, BAK_MIRROR):
                    try:
                        os.remove(os.path.join(d, fn))
                    except Exception:
                        pass
    except Exception:
        pass


def ascii_url(u):
    """把含中文/空格的 URL 转成可发送的 ASCII 形式（已 ASCII 的原样返回）。"""
    try:
        u.encode("ascii")
        return u
    except UnicodeEncodeError:
        pass
    try:
        p = urllib.parse.urlsplit(u)
        netloc = p.netloc
        try:
            netloc = netloc.encode("idna").decode("ascii")
        except Exception:
            pass
        return urllib.parse.urlunsplit((
            p.scheme, netloc,
            urllib.parse.quote(p.path, safe="/%:@!$&'()*+,;=-._~"),
            urllib.parse.quote(p.query, safe="=&%:/?@!$'()*+,;-._~"),
            urllib.parse.quote(p.fragment, safe="=&%:/?@!$'()*+,;-._~"),
        ))
    except Exception:
        return urllib.parse.quote(u, safe=":/?&=#%+@!$'()*,;[]~-._")


def fetch(url, referer=None):
    url = ascii_url(url)
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
         "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "gzip"}
    if referer:
        h["Referer"] = referer
        h["X-Requested-With"] = "XMLHttpRequest"
    r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40)
    d = r.read()
    # 2026-11-16：原来用 == "gzip" 严格比较，遇到 "GZIP" / "gzip, br" 会漏解压、正文变乱码；顺带支持 deflate
    enc = (r.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            d = gzip.decompress(d)
        except Exception:
            pass
    elif "deflate" in enc:
        try:
            d = zlib.decompress(d)
        except Exception:
            pass
    return d.decode("utf-8", "replace"), r.geturl()


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).strip()


# ---------- HTML → Markdown ----------

def _img(m):
    tag = m.group(0)
    src = None
    for attr in ("data-original", "data-src", "data-lazy-src", "src"):
        mm = re.search(attr + r'="([^"]+)"', tag)
        if mm and mm.group(1).strip() and "t.png" not in mm.group(1):
            src = mm.group(1).strip()
            break
    if not src:
        return ""
    if re.search(r"360buyimg|item\.jd\.com|//u\.jd\.com|img\.jd\.com|dangbei|kkeji\.com/img/\d{4}/\d{8}/\d+\.png", src):
        return ""          # 推广横幅（京东/当贝/快科技轮播位），不是文章配图
    if src.startswith("//"):
        src = "https:" + src
    return "\n\n![](%s)\n\n" % src


def to_md(fragment):
    s = fragment
    s = re.sub(r"<(script|style|noscript)\b.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<img\b[^>]*>", _img, s, flags=re.I)
    # 视频播放器锚点（IT之家把微博视频包成 <a class="ithome_super_player">）
    s = re.sub(r"<a\b[^>]*ithome_super_player[^>]*>.*?</a>",
               lambda m: "\n\n▶ 视频：%s\n\n" % (re.search(r'href="([^"]+)"', m.group(0)).group(1)),
               s, flags=re.S | re.I)
    s = re.sub(r"<video\b[^>]*>.*?</video>",
               lambda m: "\n\n▶ 视频：%s\n\n" % ((re.search(r'src="([^"]+)"', m.group(0)) or [None, ""])[1] or ""),
               s, flags=re.S | re.I)
    def _atag(m):
        # 用户 2026-11-13 定：正文里的链接保留成 [文字](URL)；非 http(s)（javascript:/mailto:/锚点）仍只留文字
        txt = m.group(1)
        hm = re.search(r'href="([^"]+)"', m.group(0))
        href = htmllib.unescape(hm.group(1)) if hm else ""
        if not href.startswith(("http://", "https://")):
            return strip_tags(txt)
        if re.search(r"!\[[^\]]*\]\(", txt):      # 图链：保留图不包链接
            return txt
        inner = strip_tags(txt).strip()
        return "[%s](%s)" % (inner, href) if inner else txt
    s = re.sub(r"<a\b[^>]*>(.*?)</a>", _atag, s, flags=re.S | re.I)
    def _table(m):
        # 用户 2026-11-17 定：HTML 表格要转成 markdown 表格（此前被 strip 压成一行，数据不可读）
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", m.group(0), flags=re.S | re.I)
        grid, has_th = [], False
        for r in rows:
            cells = re.findall(r"<(td|th)\b[^>]*>(.*?)</\1>", r, flags=re.S | re.I)
            if not cells:
                continue
            vals = []
            for tag, c in cells:
                if tag.lower() == "th":
                    has_th = True
                c = re.sub(r"<br\s*/?>", " ", c, flags=re.I)
                txt = re.sub(r"<[^>]+>", "", c)
                txt = htmllib.unescape(txt).replace("\u00a0", " ")
                txt = re.sub(r"\s+", " ", txt).strip().replace("|", "\\|")
                vals.append(txt)
            grid.append(vals)
        if not grid:
            return "\n\n"
        ncol = max(len(r) for r in grid)
        grid = [r + [""] * (ncol - len(r)) for r in grid]
        head = grid[0] if has_th or len(grid) > 1 else [""] * ncol
        body = grid[1:] if (has_th or len(grid) > 1) else []
        out = ["\n\n| " + " | ".join(head) + " |",
               "| " + " | ".join(["---"] * ncol) + " |"]
        for r in body:
            out.append("| " + " | ".join(r) + " |")
        return "\n".join(out) + "\n\n"
    s = re.sub(r"<table\b[^>]*>.*?</table>", _table, s, flags=re.S | re.I)
    s = re.sub(r"<h1\b[^>]*>(.*?)</h1>", lambda m: "\n\n## " + strip_tags(m.group(1)) + "\n\n", s, flags=re.S | re.I)
    s = re.sub(r"<h2\b[^>]*>(.*?)</h2>", lambda m: "\n\n## " + strip_tags(m.group(1)) + "\n\n", s, flags=re.S | re.I)
    s = re.sub(r"<h3\b[^>]*>(.*?)</h3>", lambda m: "\n\n### " + strip_tags(m.group(1)) + "\n\n", s, flags=re.S | re.I)
    s = re.sub(r"<h4\b[^>]*>(.*?)</h4>", lambda m: "\n\n#### " + strip_tags(m.group(1)) + "\n\n", s, flags=re.S | re.I)
    s = re.sub(r"<blockquote\b[^>]*>(.*?)</blockquote>", lambda m: "\n\n" + strip_tags(m.group(1)) + "\n\n", s, flags=re.S | re.I)
    s = re.sub(r"<p\b[^>]*>\s*<strong>(.{1,80}?)</strong>\s*</p>",
               lambda m: "\n\n### " + strip_tags(m.group(1)) + "\n\n", s, flags=re.S | re.I)
    s = re.sub(r"<(strong|b)\b[^>]*>(.*?)</\1>", r"**\2**", s, flags=re.S | re.I)
    s = re.sub(r"<(em|i)\b[^>]*>(.*?)</\1>", r"*\2*", s, flags=re.S | re.I)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</li>", "", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<p\b[^>]*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<figcaption\b[^>]*>", "\n\n", s, flags=re.I)
    s = re.sub(r"</figcaption>", "\n\n", s, flags=re.I)
    s = re.sub(r"</p>|</div>|</figure>|</h[1-6]>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s).replace("\u00a0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"\*\*[ \t]+", "**", s)
    s = re.sub(r"[ \t]+\*\*", "**", s)
    # 表格行之间不留空行，否则 Obsidian 不渲染成表格
    s = re.sub(r"(\|[^\n]*\|)\n(?:[ \t]*\n)+(?=\|)", r"\1\n", s)
    return s.strip()


_HOST_BLOCK = {}


def _host_blocked(src):
    """站点是否对异源 Referer 返 403（这类图在 Obsidian 里必然裂）。"""
    m = re.match(r"https?://([^/]+)", src)
    if not m:
        return False
    host = m.group(1)
    if host in _HOST_BLOCK:
        return _HOST_BLOCK[host]
    def _probe(method, timeout=8):
        req = urllib.request.Request(src, headers={"User-Agent": UA, "Referer": "file:///note.md"})
        req.get_method = lambda: method
        try:
            urllib.request.urlopen(req, timeout=timeout).close()
            return None
        except Exception as e:
            return str(e)

    # 2026-11-16：原来直接用 GET 探一张图（不读 body 但连接与首包都发生了），
    # 一篇十张图的文章要多付十次下载；改 HEAD，8 秒超时，405 时再回落 GET。
    blocked = False
    err = _probe("HEAD")
    if err and "403" in err:
        blocked = True
    elif err and "405" in err:
        err2 = _probe("GET")
        blocked = bool(err2 and "403" in err2)
    _HOST_BLOCK[host] = blocked
    return blocked


def fix_hotlinks(md):
    """把防盗链站点的图片改走 wsrv.nl 代理，其余保持原样。"""
    def rep(m):
        alt, src = m.group(1), m.group(2)
        if not src.startswith("http") or "wsrv.nl" in src:
            return m.group(0)
        if src.lower().split("?")[0].endswith(".gif"):
            return m.group(0)  # GIF 保留原网址：wsrv 转 webp 会丢动画帧
        if _host_blocked(src):
            import urllib.parse as _up
            return "![%s](https://wsrv.nl/?url=%s)" % (alt, _up.quote(src, safe=""))
        return m.group(0)
    return re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", rep, md)


PROMO_RE = re.compile(r"关注|小红书|公众号|微博|抖音|视频号|订阅|扫码|App\s*下载|下载\s*App|知识星球|社群|加群|在看|点赞|转发|哔哩哔哩|B站|知乎|少数派")


def _trim_promo_block(md):
    """删掉文末的平台推广引用块（如少数派的「> 关注少数派小红书」），只动尾部连续几行。"""
    lines = md.rstrip().split("\n")
    i, cut = len(lines) - 1, None
    while i >= 0:
        t = lines[i].strip()
        if not t:
            i -= 1
            continue
        tt = t.replace("\\>", ">").lstrip()
        if tt.startswith(">") and len(t) < 120 and PROMO_RE.search(t):
            cut = i
            i -= 1
            continue
        break
    if cut is None:
        return md
    return "\n".join(lines[:cut]).rstrip()


def clean_body(md, title=""):
    """去掉站点样板行、行首缩进与广告段。"""
    md = _trim_promo_block(md)
    lines, in_ad = [], False
    for ln in md.split("\n"):
        t = ln.strip().replace("\\*", "*").replace("\\_", "_").replace("\\#", "#")
        if re.match(r"^#{0,4}\s*(熱門新聞|热门新闻|相關新聞|相关新闻|延伸閱讀|延伸阅读|推薦閱讀|推荐阅读|熱門文章|热门文章|更多相關|更多相关)", t):
            break   # 站点侧栏/页尾推荐位，其后的缩略图都不是正文
        if re.match(r"^广告声明", t):
            in_ad = True
        if in_ad or not t:
            continue
        if re.search(r"直达链接$|无门槛红包|点此抽红包|仅供读者参考", t):
            continue
        if re.match(r"^\d{1,6}$", t):
            continue
        if re.match(r"^发布于.{0,40}(阅读|AI新闻)", t):
            continue
        if re.search(r"(icon/logo|/logo\.(png|svg|jpg))", t):
            continue
        if re.search(r"!(\[[^\]]*\])?\((https?://[^)]*?(360buyimg|item\.jd\.com|u\.jd\.com|img\.jd\.com|dangbei|img1\.kkeji\.com/img/\d{4}/\d{8})[^)]*)\)", t):
            continue
        if re.match(r"^感[谢谢].{0,80}(线索投递|投递)[！!]?$", t):
            continue
        if re.match(r"^相关阅读[：:]?-?$", t) or t == "-":
            continue
        if re.match(r"^扫码关注", t) or "版权所有" in t:
            continue
        if re.match(r"^\*?本文系.{0,30}(刊载|授权|转载)", t):
            continue
        if re.match(r"^量子位\s*\|\s*公众号", t):
            continue
        if re.match(r"^.{1,8}\s*发自\s*.{1,8}$", t):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}$", t) or re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", t):
            continue
        if re.match(r"^来源[：:]", t) or re.match(r"^扫码(分享|关注)", t):
            continue
        if re.match(r"^\*[^*]{2,10}\*$", t):
            continue
        if re.match(r"^-?\s*\S.*\*20\d{2}-\d{2}-\d{2}\*$", t):   # 相关阅读条目
            continue
        if "outdated browser" in t.lower() or "upgrade your browser" in t.lower():
            continue
        if "email protected" in t.lower() or re.match(r"^(歡迎|欢迎)(回應|回应)", t):
            continue
        if re.match(r"^\S+@\S+\.\S+$", t):
            continue
        # 快科技等站点的权限提示 + 分享按钮栏
        if re.match(r"^作者私密文章", t) or "过往内容只提供给老鸟" in t:
            continue
        if t in {"QQ空间", "复制链接", "微信扫一扫", "朋友圈", "新浪微博", "腾讯微博",
                 "QQ好友", "分享到微信", "分享到QQ"}:
            continue
        # 尾部样板：遇到即截断（快科技等）
        if re.match(r"^【本文结束】", t) or re.match(r"^责任编辑[：:]", t) or re.match(r"^Copyright\(C\)", t):
            break
        lines.append(t)

    lines = [("### " + ln[2:].strip()) if ln.startswith("# ") else ln for ln in lines]
    lines = ["### " + ln.strip("*").strip() if (re.fullmatch(r"\*\*[^*]{4,60}\*\*", ln.strip())
                                               and not re.search(r"[。！？!?]$", ln.strip("*")))
             else ln for ln in lines]
    if title:
        lines = [ln for ln in lines
                 if not (re.match(r"^#{1,4}\s+", ln) and ln.split(None, 1)[1].strip() == title.strip())]
    # 正文开头的短标题行（署名/站点名）通常是页头，不是小节
    kept = []
    for i, ln in enumerate(lines):
        if i < 4 and re.match(r"^#{2,4}\s+", ln) and len(ln.split(None, 1)[1]) <= 12:
            continue
        kept.append(ln)
    lines = kept

    # 尾部推广/版权段整块截断
    cut = None
    for i, ln in enumerate(lines):
        if re.match(r"^(#{2,4}\s*)?(本文来自|扫码查看|欢迎来到)|© ?版权所有|—+ ?由.{0,12}创作", ln):
            cut = i
            break
    if cut is not None:
        j = cut
        while j > 0 and ("。" not in lines[j-1] and "，" not in lines[j-1]
                         and not lines[j-1].startswith(("#", "!", "[", ">", "-"))):
            j -= 1
        lines = lines[:j]

    while lines:
        t = lines[-1]
        if re.search(r"avatar|head\.jpg|/logo", t) or (len(t) <= 6 and not re.search(r"[。，,、：:]", t)):
            lines.pop()
            continue
        break

    seen, uniq = set(), []
    for ln in lines:
        if ln.startswith(("![[", "![]", "#")):
            uniq.append(ln)
            continue
        key = ln.strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ln)
    lines = uniq

    out = "\n\n".join(lines)
    # clean_body 按行重组会把表格行之间塞进空行，Obsidian 就不渲染成表格了（2026-11-17）
    out = re.sub(r"(\|[^\n]*\|)\n\n(?=\|)", r"\1\n", out)
    out = re.sub(r"[*\s]*相关阅读[：:][*\s]*[-－]?\s*$", "", out, flags=re.M)
    out = re.sub(r"\n\n《[^》\n]{4,}》\s*$", "", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def balanced_div(page, start):
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", page[start:], re.I):
        depth += 1 if m.group(1) == "" else -1
        if depth == 0:
            return page[start:start + m.end()]
    return None


# ---------- 站点适配 ----------

def extract_ithome(page):
    m = re.search(r'<div class="post_content[^"]*" id="paragraph">', page)
    if not m:
        return None
    body = balanced_div(page, m.start()) or ""
    return to_md(body)


TOK = re.compile(r"^(article|post|entry|content|detail|text|article_info|article-body|"
                   r"article-content|post-content|entry-content|rich_media_content|"
                   r"content-body|main-content)$", re.I)


def _class_tokens(tag):
    m = re.search(r'class="([^"]*)"', tag)
    return m.group(1).split() if m else []


def extract_v2ex(page):
    """V2EX 主题正文：div.topic_content，到主题按钮/回复区为止。"""
    m = re.search(r'<div class="topic_content">(.*?)(?:<div class="topic_buttons"|<div id="topic-replies")', page, re.S)
    if not m:
        return ""
    return clean_body(to_md(m.group(1)))


def scrub_page(page):
    """去掉摘要块、署名/分享/相关阅读等非正文容器（两个提取器共用）。"""
    return re.sub(r'<div[^>]*class="[^"]*(?:zhaiyao|article_info|share_box|share|related|comment|'
                  r'sidebar|wx_img|top_weixin|weixin_pop|top_weibo|top_search|author)[^"]*"[^>]*>.*?</div>',
                  "", page, flags=re.S | re.I)


def extract_trafilatura(page, url):
    """通用站点首选：trafilatura（去样板、带图、结构化好）。太短则返回 None。"""
    if trafilatura is None:
        return None
    page = scrub_page(page)
    try:
        md = trafilatura.extract(page, url=url, output_format="markdown",
                                 include_images=True, include_links=True,
                                 include_tables=True, include_comments=False,
                                 favor_recall=True)
    except Exception:
        return None
    if not md or len(md) < 400:
        return None
    # 统一成 ![](url) 形式，去掉 alt 冗余
    md = re.sub(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", r"![](\1)", md)
    return md


def extract_generic(page):
    """候选容器里挑文字最多的一批，再选其中 html 最小的（=最内层正文）。"""
    page = scrub_page(page)
    cands = []
    for m in re.finditer(r'<article\b[^>]*>', page, re.I):
        b = balanced_div(page, m.start())
        if b:
            cands.append(b)
    for m in re.finditer(r'<div\b[^>]*>', page, re.I):
        if not any(TOK.match(t) for t in _class_tokens(m.group(0))):
            continue
        b = balanced_div(page, m.start())
        if b:
            cands.append(b)
    m = re.search(r'<main\b[^>]*>', page, re.I)
    if m:
        b = balanced_div(page, m.start())
        if b:
            cands.append(b)

    scored = [(len(strip_tags(b)), len(b), b) for b in cands]
    scored = [x for x in scored if 400 <= x[0] <= 80000]
    if scored:
        top = max(x[0] for x in scored)
        keep = [x for x in scored if x[0] >= 0.9 * top]
        return to_md(min(keep, key=lambda x: x[1])[2])

    ps = re.findall(r"<p\b[^>]*>.*?</p>", page, re.S | re.I)
    return to_md("\n".join(ps)) if ps else ""


_META_CACHE = {}


def _meta_map(page):
    """一次解析整页的 <meta> 为 dict；兼容属性任意顺序与单双引号。

    2026-11-16 修：原实现只认「name 在前」或「content 在前」× **双引号** 两种写法，
    用单引号写属性的站点（不少老站/小众站）title/date/author/description 会全部落空，
    表现为标题落成「未命名」、date 变今天。
    """
    hit = _META_CACHE.get(id(page))
    if hit and hit[0] is page:      # id() 会被复用，必须连同对象本身一起校验
        return hit[1]
    d = {}
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        mk = re.search(r"""(?:name|property)\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.I | re.S)
        if not mk:
            continue
        k = (mk.group(1) if mk.group(1) is not None else mk.group(2) or "").strip().lower()
        mc = re.search(r"""content\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.I | re.S)
        if not k or not mc:
            continue
        if k not in d:
            v = mc.group(1) if mc.group(1) is not None else mc.group(2)
            d[k] = htmllib.unescape(v).strip()
    _META_CACHE.clear()
    _META_CACHE[id(page)] = (page, d)
    return d


def meta(page, name):
    return _meta_map(page).get((name or "").lower())


# ---------- IT之家评论 ----------

def ithome_comments(page):
    m = re.search(r'id="post_comm"[^>]*data-id="([^"]+)"', page)
    if not m:
        return None
    sn = m.group(1)
    base = "https://cmt.ithome.com/api/webcomment/getnewscomment?sn=%s" % sn
    allc, hot = [], []
    try:
        j = json.loads(fetch(base + "&isInit=true&appver=900")[0])
        c = j.get("content") or {}
        allc = list(c.get("comments") or [])
        hot = list(c.get("hotComments") or [])
    except Exception:
        return None
    seen = {x["id"] for x in allc}
    for _ in range(20):
        last = allc[-1]["id"] if allc else 0
        try:
            j = json.loads(fetch("%s&cid=%s&isInit=false&appver=900" % (base, last))[0])
        except Exception:
            break
        cc = (j.get("content") or {}).get("comments") or []
        new = [x for x in cc if x["id"] not in seen]
        if not new:
            break
        allc += new
        seen |= {x["id"] for x in new}
        time.sleep(0.3)
    end_hint = None
    try:
        end_hint = ((json.loads(fetch(base + "&isInit=true&appver=900")[0]).get("content") or {})
                    .get("endHint"))
    except Exception:
        end_hint = None

    def _key(c):
        nick = ((c.get("userInfo") or {}).get("userNick") or "").strip()
        return (nick, _ctxt(c)[:40])

    seen_key = set()
    for c in allc:
        seen_key.add(_key(c))
        for k in (c.get("children") or []):
            seen_key.add(_key(k))
    extra = [x for x in hot if x["id"] not in seen]
    if not allc and end_hint:
        mob = ithome_comments_mobile((re.search(r'data-nid="(\d+)"', page) or [None, None])[1])
        if mob:
            return mob

    return {"main": allc, "extra": extra, "endHint": end_hint,
            "newsid": (re.search(r'data-nid="(\d+)"', page) or [None, None])[1]}


def mydrivers_comments(url):
    """快科技评论：article 页 iframe 参数 tid → ReviewAjax JSON。"""
    m = re.search(r"/1/\d+/(\d+)\.htm", url)
    if not m:
        return None
    tid = m.group(1)
    try:
        raw = fetch("https://comment8.mydrivers.com/ReviewAjax.aspx?v=2023&Tid=%s&Cid=1&IsTopN=1&hot=0" % tid,
                    referer="https://news.mydrivers.com/1/%s/%s.htm" % (tid[:4], tid))[0]
        j = json.loads(raw)
    except Exception:
        return None
    allc = j.get("All") or []
    if not allc:
        return None

    def conv(a):
        return {
            "id": a.get("ID"),
            "city": (a.get("IPAdd") or "").strip() or None,
            "postTime": (a.get("PostDate") or "").replace("T", " "),
            "support": a.get("Support", 0),
            "floorStr": None,
            "userInfo": {"userNick": a.get("UserName")},
            "elements": [{"type": 0, "content": a.get("Content") or ""}],
            "children": [conv(c) for c in (a.get("child") or [])],
        }

    main = [conv(a) for a in allc if not a.get("RevertID")]
    extra = [conv(a) for a in allc if a.get("RevertID")]
    return {"main": main, "extra": extra, "endHint": None, "source": "mydrivers"}


def v2ex_api(path, **q):
    u = "https://www.v2ex.com/api/" + path
    if q:
        u += "?" + urllib.parse.urlencode(q)
    raw = fetch(u, referer="https://www.v2ex.com/")[0]
    return json.loads(raw)


def v2ex_comments(url):
    """V2EX 主题回复：走 HTML 分页（每页 40 条）取楼层号与时间，API 无楼层字段。
    结构：div id="r_<replyId>" class="cell" / span.no 楼层 / a.dark 用户名 / div.reply_content。"""
    m = re.search(r"/t/(\d+)", url)
    if not m:
        return None
    tid = m.group(1)
    base = "https://www.v2ex.com/t/%s" % tid
    # V2EX 页面只给相对时间（「8 小时 43 分钟前」）：优先用 API 的 created 换算，拿不到再按相对时间反推
    try:
        _api = v2ex_api("replies/show.json", topic_id=tid) or []
        api_t = {str(x.get("id")): x.get("created") for x in _api if isinstance(x, dict)}
    except Exception:
        api_t = {}

    def _rel(txt):
        s = txt or ""
        sec = sum(int(n) * u for n, u in re.findall(r"(\d+)\s*(秒|分钟|小时|天)", s)
                  for u in [{"秒": 1, "分钟": 60, "小时": 3600, "天": 86400}[u]])
        if not re.search(r"\d", s):
            return ""
        return (datetime.now() - timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M:%S")

    items, seen = [], set()
    for p in range(1, 21):
        u = base if p == 1 else "%s?p=%d" % (base, p)
        try:
            page = fetch(u, referer="https://www.v2ex.com/")[0]
        except Exception:
            break
        added = 0
        for ch in page.split('<div id="r_')[1:]:
            mid = re.match(r"(\d+)", ch)
            if not mid:
                continue
            rid = mid.group(1)
            if rid in seen:
                continue
            body = ch.split('<div id="topic-replies"')[0]
            nick = (re.search(r'<a href="/member/[^"]+"[^>]*class="dark"[^>]*>([^<]+)</a>', body)
                    or re.search(r'<a href="/member/[^"]+"[^>]*>([^<]+)</a>', body))
            floor = re.search(r'<span class="no">(\d+)</span>', body)
            ts = re.search(r'title="(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', body)
            if ts:
                ptime = ts.group(1)
            elif api_t.get(rid):
                ptime = datetime.fromtimestamp(api_t[rid]).strftime("%Y-%m-%d %H:%M:%S")
            else:
                _rl = re.search(r'<span class="fade small">([^<]*)</span>', body)
                ptime = _rel(_rl.group(1) if _rl else "")
            # 感谢数只在带 heart 图标的那个 span 里（别把相对时间「8 小时前」的 8 当赞数）
            th = re.search(r'<span class="[^"]*small fade[^"]*"[^>]*>(?:(?!</span>).)*?heart'
                           r'[^>]*>\s*(\d+)', body, re.S)
            tn = int(th.group(1)) if th else 0
            core = body.split('<span class="ago"')[0]      # 正文只到时间戳之前，避免把「2h ago」带进译文
            cm = re.search(r'<div class="reply_content">(.*?)</div>', core, re.S)
            raw = cm.group(1) if cm else ""
            raw = re.sub(r'<img[^>]*class="emoji"[^>]*>', "", raw, flags=re.I)
            raw = re.sub(r'<a href="/member/[^"]*"[^>]*>(.*?)</a>', r"\1", raw, flags=re.S)
            txt = re.sub(r"\n{3,}", "\n\n", to_md(raw).strip())
            items.append({
                "id": rid,
                "floorStr": ("%s楼" % floor.group(1)) if floor else None,
                "postTime": ptime,
                "support": tn,
                "city": None,
                "userInfo": {"userNick": strip_tags(nick.group(1)) if nick else "匿名"},
                "elements": [{"type": 0, "content": txt}],
                "children": [],
            })
            seen.add(rid)
            added += 1
        if not added:            # 本页无新条目 = 已到末页
            break
    if not items:
        return None
    items.sort(key=lambda c: _floor(c) or 0)
    return {"main": items, "extra": [], "endHint": None, "source": "v2ex"}


def ithome_comments_mobile(newsid):
    """PC 接口对旧文关门时，改走移动端接口（匿名可用）。"""
    if not newsid:
        return None
    try:
        page = fetch("https://m.ithome.com/html/%s.htm" % newsid)[0]
    except Exception:
        return None
    g = lambda k: (re.search(r'%s:\s*"([^"]*)"' % k, page) or [None, ""])[1]
    newsid_des, lapin, post_date = g("NewsIDDes"), g("LapinIDDes"), g("PostDate")
    if not newsid_des:
        return None

    items, max_id = [], 0
    for _ in range(10):
        q = urllib.parse.urlencode({"NewsID": newsid_des, "LapinID": lapin, "MaxCommentID": max_id,
                                    "Latest": "0", "PostDate": post_date, "appver": 868})
        try:
            raw = fetch("https://m.ithome.com/api/comment/newscommentlistget?" + q,
                        referer="https://m.ithome.com/html/%s.htm" % newsid)[0]
            j = json.loads(raw)
        except Exception:
            break
        cl = ((j.get("Result") or {}).get("Clist")) or []
        if not cl:
            break
        for c in cl:
            m = c.get("M") or {}
            if not m.get("Ci"):
                continue
            items.append({
                "id": m.get("Ci"),
                "city": m.get("Y"),
                "postTime": m.get("T") or "",
                "support": m.get("S", 0),
                "floorStr": m.get("SF"),
                "userInfo": {"userNick": m.get("N")},
                "elements": [{"type": 0, "content": m.get("C") or ""}],
                "children": [],
            })
        nxt = cl[-1].get("M", {}).get("Ci") or 0
        if not nxt or nxt == max_id:
            break
        max_id = nxt
        time.sleep(0.3)
    if not items:
        return None
    return {"main": items, "extra": [], "endHint": None, "newsid": newsid,
            "source": "mobile"}


def _ctxt(c):
    return "".join((e.get("content") or "") for e in (c.get("elements") or []) if e.get("type") == 0).strip()


def _when(c):
    t = c.get("postTime") or ""
    return t[5:16].replace("T", " ") if len(t) >= 16 else ""


def _city(c):
    return (c.get("city") or "").replace("IT之家", "").replace("网友", "").strip()


def _floor(c):
    m = re.search(r"(\d+)", c.get("floorStr") or "")
    return int(m.group(1)) if m else 0


INDENT = "\u3000\u3000"  # 楼中楼缩进（两个全角空格，markdown 安全）
IT_MAX_INDENT = 99     # IT之家/快科技：按数据实际层数缩进、不人为截断（2026-11-18 用户定「按支持的最大层数」）


def _kid_depths(c):
    """IT之家/快科技的子楼：接口只给一层扁平 children，用 replyUserInfo 推断层级。
    某条子楼回复的对象若是同一主楼下的另一条子楼，就记为它的下一层。"""
    kids = c.get("children") or []
    main_nick = ((c.get("userInfo") or {}).get("userNick") or "").strip()
    depths = []
    for i, k in enumerate(kids):
        rt = ((k.get("replyUserInfo") or {}).get("userNick") or "").strip()
        d = 1
        if rt and rt != main_nick:
            for j in range(i - 1, -1, -1):
                nick_j = ((kids[j].get("userInfo") or {}).get("userNick") or "").strip()
                if nick_j and nick_j == rt:
                    d = depths[j] + 1
                    break
        depths.append(d)
    return list(zip(kids, depths))


def _entry(c, prefix="", zoom=False):
    """zoom=True 时整条（头 + 正文）放大一号（1.12em），用于热评。"""
    nick = (c.get("userInfo") or {}).get("userNick") or "匿名"
    fl = c.get("floorStr") or ""
    if re.search(r"\d+#", fl):        # 子楼层标记（9楼1#）不入头
        fl = ""
    segs = [prefix.strip(" ·"), fl, nick, _when(c), "赞 %s" % c.get("support", 0), _city(c)]
    head = "**%s**" % " · ".join(x for x in segs if x)
    body_txt = _ctxt(c) or "（图片评论）"
    if zoom:
        head = '<span style="font-size:1.12em">%s</span>' % head
        body_txt = '<span style="font-size:1.12em">%s</span>' % body_txt
    out = [head, "", body_txt, ""]
    for k, d in _kid_depths(c):
        kn = (k.get("userInfo") or {}).get("userNick") or "匿名"
        ru = (k.get("replyUserInfo") or {}).get("userNick")
        pad = INDENT * min(d, IT_MAX_INDENT)   # d 即实际推断层级，不截断
        mark = ""
        who = mark + "↳ " + kn + (" 回复 " + ru if ru else "")
        kb = _ctxt(k) or "（图片评论）"
        kb = "\n".join(pad + ln for ln in kb.split("\n"))
        # 紧凑：楼中楼头行与正文连续两行，条目之间不空行（用户 2026-11-14 定）
        out += [pad + "**%s** · %s · 赞 %s" % (who, _when(k), k.get("support", 0)), kb]
    return "\n".join(out).rstrip()


def _news_comment_count(newsid):
    if not newsid:
        return None
    try:
        raw = fetch("https://cmt.ithome.com/api/comment/count?newsid=%s" % newsid,
                    referer="https://www.ithome.com/")[0]
        m = re.search(r"innerHTML\s*=\s*'(\d+)'", raw)
        return int(m.group(1)) if m else None
    except Exception:
        return None


# ---------- Hacker News 评论区（2026-11-17 接入） ----------
# Algolia items 接口一次返回整棵评论树（children 递归嵌套），无需逐条请求。
HN_API = "https://hn.algolia.com/api/v1/items/%s"
HN_MAX_INDENT = 2          # 缩进最多 2 个全角（第 3 层起不再加深，改在头行标 ↳层级）
HN_DEEP_LIMIT = 200        # 楼层超过此数后只保留主楼层（用户 2026-11-17 定）
HN_PICK_MIN = 2.0          # 楼中楼入选分数阈值（2026-11-17 实测 3.0→2.0：增量仅 5% 体积多为技术澄清，上限兜底）
HN_PICK_LIMIT = 120        # 精选楼中楼上限（真正的量闸：阈值只定及格线，上限防大帖爆量）
HN_KARMA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "bak", "hn_karma.json")


def _hn_clean(s):
    """HN 评论正文清洗：HTML 实体 + <p>/<a> 处理，保留段落。"""
    s = htmllib.unescape(s or "")
    s = re.sub(r"(?i)<a\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
               lambda m: "[%s](%s)" % (re.sub(r"<[^>]+>", "", m.group(2)).strip(), m.group(1)), s)
    s = re.sub(r"(?i)</p>\s*<p[^>]*>", "\n\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    out = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    txt = "\n".join(out).strip()
    # 2026-11-17：HN 评论常写「interesting!<a>链接</a>」，转成 markdown 后成了
    # 「![文字](url)」——会被当成图片下载/上传。断开感叹号与方括号。
    txt = re.sub(r"!\[", "! [", txt)
    return txt


_HN_CACHE = {}


def _hn_get(sid, tries=3):
    """Algolia items 接口（默认 fetch 会因大响应 gzip 中断，这里用独立轻头重试）。"""
    import urllib.request as _u
    if sid in _HN_CACHE:
        return _HN_CACHE[sid]
    url = HN_API % sid
    for i in range(tries):
        try:
            r = _u.urlopen(_u.Request(url, headers={"User-Agent": "dsh-clip/1.0",
                                                   "Accept": "application/json"}), timeout=40)
            data = json.loads(r.read().decode("utf-8", "replace"))
            _HN_CACHE[sid] = data
            return data
        except Exception as e:
            if i == tries - 1:
                print("HN 评论接口失败：%s" % e)
                return None
            time.sleep(2 + 2 * i)
    return None


def _hn_count(node):
    n = 0
    for c in (node.get("children") or []):
        n += 1 + _hn_count(c)
    return n


def hn_story(hn_url):
    """HN item 帖的元信息：标题 / 指向的原文链接 / 分数 / 提交者 / 日期。"""
    m = re.search(r"[?&]id=(\d+)", hn_url)
    if not m:
        return None
    d = _hn_get(m.group(1))
    if not d:
        return None
    if d.get("type") not in (None, "story"):
        return None
    return {"id": m.group(1),
            "title": htmllib.unescape(d.get("title") or ""),
            "url": d.get("url") or "",
            "points": d.get("points") or 0,
            "by": d.get("author") or "",
            "when": (d.get("created_at") or "")[:10],
            "comments": _hn_count(d),
            "text": _hn_clean(d.get("text") or "")}


def _hn_karma(authors, budget=None):
    """批量取作者 karma（有本地缓存；预算内限次，失败静默返回空）。"""
    cache = {}
    try:
        if os.path.exists(HN_KARMA_CACHE):
            cache = json.load(open(HN_KARMA_CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    todo = [a for a in authors if a and a not in cache]
    if budget:
        todo = todo[:budget]
    for a in todo:
        try:
            d = _hn_get_soft("https://hn.algolia.com/api/v1/users/%s" % a)
            if d:
                cache[a] = int(d.get("karma") or 0)
        except Exception:
            pass          # 失败不写缓存，下次再试（写 0 会永久压低该作者的分数）
        time.sleep(0.08)
    if todo:
        try:
            os.makedirs(os.path.dirname(HN_KARMA_CACHE), exist_ok=True)
            json.dump(cache, open(HN_KARMA_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return cache


def _hn_get_soft(url):
    req = urllib.request.Request(url, headers={"User-Agent": "dsh-clip/1.0",
                                              "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _hn_score(r, story_ts, karma=None):
    """楼中楼性价比打分（HN 没有单条赞数，只能用代理信号；用户 2026-11-17 定）。"""
    t = r.get("text") or ""
    L = len(t)
    s = 0.0
    if L >= 400:
        s += 2
    elif L >= 200:
        s += 1
    elif L < 100:
        s -= 1
    d = r.get("depth") or 0
    s += 2 if d == 1 else (1 if d == 2 else (-2 if d >= 4 else 0))
    k = r.get("children") or 0
    s += 2 if k >= 2 else (1 if k == 1 else 0)
    if ("](" in t) or ("\n    " in t) or ("$ " in t):
        s += 1
    km = (karma or {}).get(r.get("author") or "", 0)
    if km >= 5000:
        s += 1
    elif km >= 1000:
        s += 0.5
    try:
        dt = datetime.fromisoformat((r.get("created") or "").replace("Z", "+00:00"))
        if (dt - story_ts).total_seconds() <= 6 * 3600:
            s += 0.5
    except Exception:
        pass
    return s


def _hn_pick(rows):
    """返回入选下标集合：主楼全收 + 楼中楼 score>=阈值（补齐父链，超上限按分数截断）。"""
    try:
        story_ts = datetime.fromisoformat((rows[0].get("created") or "").replace("Z", "+00:00"))
    except Exception:
        story_ts = datetime.now(timezone.utc)
    subs = [i for i, r in enumerate(rows) if (r.get("depth") or 0) >= 1]
    if not subs:
        return set(range(len(rows)))
    # karma 只查「有希望入选」的作者，控制耗时
    cand = [rows[i] for i in subs if len(rows[i].get("text") or "") >= 200
            or (rows[i].get("children") or 0) >= 1]
    karma = _hn_karma(sorted({c.get("author") for c in cand if c.get("author")}))
    for i in subs:
        rows[i]["score"] = _hn_score(rows[i], story_ts, karma)
    picked = [i for i in subs if rows[i]["score"] >= HN_PICK_MIN]
    picked.sort(key=lambda i: -rows[i]["score"])
    if len(picked) > HN_PICK_LIMIT:
        picked = picked[:HN_PICK_LIMIT]
    keep = {i for i, r in enumerate(rows) if (r.get("depth") or 0) == 0} | set(picked)
    parent, stack = {}, []
    for i, r in enumerate(rows):
        while stack and (rows[stack[-1]].get("depth") or 0) >= (r.get("depth") or 0):
            stack.pop()
        parent[i] = stack[-1] if stack else None
        stack.append(i)
    changed = True
    while changed:
        changed = False
        for i in list(keep):
            pi = parent.get(i)
            if pi is not None and pi not in keep:
                keep.add(pi)
                changed = True
    return keep


def hn_comments(url, deep="pick"):
    """抓 HN 帖评论（含楼中楼）。deep="pick" 主楼全收+精选楼中楼；deep="all" 全量不筛。"""
    m = re.search(r"[?&]id=(\d+)", url) or re.search(r"/item/(\d+)", url)
    if not m:
        return None
    data = _hn_get(m.group(1))
    if not data:
        return None
    rows = []

    def walk(node, depth):
        for c in (node.get("children") or []):
            txt = _hn_clean(c.get("text") or "")
            kids = c.get("children") or []
            if not txt and not kids:      # 已删除的楼层
                continue
            rows.append({"depth": depth,
                         "author": c.get("author") or "匿名",
                         "when": (c.get("created_at") or "")[:10],
                         "created": c.get("created_at") or "",
                         "children": len(kids),
                         "text": txt})
            walk(c, depth + 1)

    walk(data, 0)
    if not rows:
        return None
    _trimmed = len(rows) > HN_DEEP_LIMIT
    if _trimmed:
        rows = [r for i, r in enumerate(rows) if i < HN_DEEP_LIMIT or r["depth"] == 0]
    all_rows = rows
    raw_sub = sum(1 for r in all_rows if r["depth"] >= 1)
    main_rows, picked = all_rows, False
    if deep != "all" and raw_sub:
        keep = _hn_pick(all_rows)
        main_rows = [r for i, r in enumerate(all_rows) if i in keep]
        picked = len(main_rows) < len(all_rows)
    return {"main": main_rows, "all_rows": all_rows, "extra": [], "source": "hn",
            "endHint": None, "story": data.get("title") or "", "hn_url": url,
            "points": data.get("points") or 0, "trimmed": _trimmed,
            "picked": picked, "raw_count": len(all_rows), "raw_sub": raw_sub,
            "kept_sub": sum(1 for r in main_rows if r["depth"] >= 1)}


def hn_comments_section(cm, note_only=False):
    rows = cm.get("main") or []
    deep = sum(1 for c in rows if c["depth"] > HN_MAX_INDENT)
    if cm.get("picked"):
        head = "## 评论区（%d 条：主楼 %d + 精选楼中楼 %d）" % (
            len(rows), sum(1 for c in rows if c["depth"] == 0), cm.get("kept_sub") or 0)
    else:
        head = "## 评论区（%d 条）" % len(rows)
    note = "HN 评论 %d 条，按原站层级；缩进最多 %d 层" % (len(rows), HN_MAX_INDENT)
    if deep:
        note += "，更深的 %d 条用 ↳层级 标注" % deep
    if cm.get("trimmed"):
        note += "；超过 %d 条后只保留主楼层" % HN_DEEP_LIMIT
    if cm.get("picked"):
        note += ("。**楼中楼已筛选**：主楼层全收，楼中楼按「长度 / 深度 / 被回复数 / 作者 karma / "
                 "带链接或代码 / 发帖后 6 小时内」打分，取 score≥%.0f 并补齐父链，保留 %d 条"
                 "（原始 %d 条）；全量未筛选、未翻译的英文底稿见同目录 `_底稿/`。"
                 % (HN_PICK_MIN, cm.get("kept_sub") or 0, cm.get("raw_sub") or 0))
    link = "讨论页：[%s](%s)（%d 分）。" % (cm.get("story") or "Hacker News",
                                             cm.get("hn_url") or "", cm.get("points") or 0)
    out = [head, "", link, "", note if note.endswith("。") else note + "。", ""]
    if note_only:
        return "\n".join(out).rstrip()
    for c in rows:
        d = c["depth"]
        pad = INDENT * min(d, HN_MAX_INDENT)
        mark = "" if d <= HN_MAX_INDENT else "↳%d " % d
        head_l = "%s**%s%s · %s**" % (pad, mark, c["author"], c["when"])
        txt = c["text"] or "（无正文）"
        if d == 0:
            if out[-1] != "":
                out.append("")
            out += [head_l, ""] + txt.split("\n")
        else:
            out.append(head_l)
            out += [pad + ln if ln else ln for ln in txt.split("\n")]
    return "\n".join(out).rstrip()


def comments_section(cmts):
    main, extra = cmts["main"], cmts["extra"]
    kids = sum(len(c.get("children") or []) for c in main)
    total = len(main) + kids
    note = "；上方置顶热评 %d 条（与主楼重复，仅作突出显示）" % len(extra) if extra else ""

    src = cmts.get("source")
    if src == "mobile":
        tail = "移动端接口取回，仅主楼、楼层升序，回复未包含"
    elif src == "v2ex":
        tail = "楼层升序（V2EX 无楼中楼，@回复以正文形式出现）"
    else:
        tail = "顺序与原文页一致"
    head = note.strip("；")
    line = "主楼 %d 条 + 回复 %d 条" % (len(main), kids)
    if head:
        line += "；" + head
    line += "；" + tail
    blocks = ["## 评论区（%d 条）\n\n%s。" % (total, line)]
    for c in extra:                      # 页面顶部热评区
        blocks.append(_entry(c, prefix="热评 · ", zoom=True))
    for c in main:                       # 接口返回顺序 = 站点默认顺序，不再重排
        blocks.append(_entry(c))
    body = "\n\n".join(blocks)
    return re.sub(r"\n{3,}", "\n\n", body)



# ---------- 增量合并（旧评论只增不减） ----------

_ZOOM_OPEN = '<span style="font-size:1.12em">'


def _is_item_start(ln):
    """条目头：普通评论以 ** 开头；置顶热评整行被 span 包裹"""
    s = ln.lstrip("\u3000")
    return (s.startswith("**") or ln.startswith(_ZOOM_OPEN + "**"))


def _split_items(text):
    items, cur = [], []
    for ln in text.split("\n"):
        if _is_item_start(ln):
            if cur:
                items.append("\n".join(cur).strip("\n"))
            cur = [ln]
        elif cur:
            cur.append(ln)
    if cur:
        items.append("\n".join(cur).strip("\n"))
    return [x for x in items if x]


def _sec_fp(block):
    first = block.split("\n", 1)[0].replace(_ZOOM_OPEN, "").replace("</span>", "")
    m = re.search(r"\*\*(?:热评 · |↳ )?(.+?)\*\*", first)
    who = (m.group(1) if m else first)[:40]
    body = re.sub(r"\s+", "", block[len(first):])[:30]
    return (who.strip(), body)


def merge_comments(old_md, cmts):
    """旧评论区整段保留；新抓到而旧里没有的条目按原文页顺序插回——
    楼中楼挂回原主楼（插在其父主楼已有子楼之后），新增主楼按页面顺序插入；
    确定不了锚点的才放末尾。返回 (新markdown, 新增条数)"""
    i = old_md.find("## 评论区")
    if i < 0:
        return None, 0
    sec = old_md[i:]
    nl = sec.find("\n")
    header = sec[:nl].strip() if nl >= 0 else sec.strip()
    body = sec[nl + 1:] if nl >= 0 else ""
    k = body.find("\n**")
    explain = (body[:k] if k >= 0 else body).strip()
    items_old = _split_items(body)
    new_sec = comments_section(cmts)
    sub = new_sec.split("\n", 1)[1] if "\n" in new_sec else ""
    items_new = _split_items(sub)
    fps_old = {_sec_fp(b) for b in items_old}

    def _is_hot(b):
        return b.replace(_ZOOM_OPEN, "").lstrip().startswith("**热评 · ")

    # 锚点 = 新条目在原文页顺序里前面最近的「已存在」条目
    anchors, tail, last_seen = {}, [], None
    for b in items_new:
        fp = _sec_fp(b)
        if fp in fps_old:
            last_seen = fp
        elif last_seen:
            anchors.setdefault(last_seen, []).append(b)
        else:
            tail.append(b)
    add_n = (sum(1 for bs in anchors.values() for b in bs if not _is_hot(b))
             + sum(1 for b in tail if not _is_hot(b)))
    if add_n == 0:
        return None, 0
    m = re.search(r"（(\d+) 条）", header)
    old_n = int(m.group(1)) if m else len(items_old)
    total = old_n + add_n
    header = re.sub(r"（\d+ 条）", "（%d 条）" % total, header, count=1)
    out = [header, ""]
    if explain:
        out += [explain, ""]
    for b in items_old:
        out += [b, ""]
        fp = _sec_fp(b)
        if fp in anchors:
            for nb in anchors.pop(fp):
                out += [nb, ""]
    for bs in anchors.values():          # 兜底：同指纹锚点等边缘情况
        for nb in bs:
            out += [nb, ""]
    for b in tail:
        out += [b, ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip(), add_n


# ---------- 组装 ----------

def draft_summary(paras):
    """摘要草稿升级（2026-11-16 用户定）：正文前 3 段里第一个含数字的句子——
    IT之家核心事实句多带数字，队员从「通读自己写」变「核对压缩」；
    找不到含数字句时返回 None，调用方回落旧行为（首段前两句）。
    顺带剥掉「[IT之家](url) N 月 N 日消息，」固定开头（正文保留、摘要不必有）。"""
    for p in paras[:3]:
        for s in re.findall(r"[^。！？!?]+[。！？!?]?", p):
            s = s.strip()
            if len(s) >= 15 and re.search(r"\d", s):
                s = re.sub(r"^\[?IT之家\]?(?:\([^)]*\))?\s*\d+\s*月\s*\d+\s*日消息，?", "", s).strip()
                return s or None
    return None

def safe_name(title):
    """标题 → 安全文件名。

    2026-11-16 修三处：①HTML 实体未反转义，`&#183;` 曾原样落进文件名
    （「科莱特 &#183; 克雷斯」这类）；②非法字符替成空格后没压连续空格；
    ③没有长度上限——/sdcard 是 FUSE，单个文件名上限 255 **字节**，中文 3 字节/字，
    80 字以上的长标题会直接 OSError，整次剪藏白跑。
    """
    t = htmllib.unescape(title or "")
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    if len(t.encode("utf-8")) > 240:
        buf, acc = [], 0
        for ch in t:
            b = len(ch.encode("utf-8"))
            if acc + b > 240:
                break
            buf.append(ch)
            acc += b
        t = "".join(buf).rstrip(" .，、；:：")
    return t or "未命名"


def suggest_tags(title_text, n=3):
    """按历史笔记词典给标签候选（karakeep curatedTags 思路，2026-11-17）。
    返回 (names, dup_msg, brief)；失败返回 ([], "", "")。"""
    try:
        import subprocess as _sp
        _sg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag_suggest.py")
        _r = _sp.run([sys.executable, _sg, title_text, "--brief"],
                     capture_output=True, text=True, timeout=20)
        out = (_r.stdout or "").strip()
        if not out.startswith("标签候选:"):
            return [], "", out
        names = [x.strip() for x in out.split(":", 1)[1].split("|")[0].split("、") if x.strip()]
        names = [x for x in names if not x.startswith("无（")]
        dup = ""
        if "|" in out:
            dup = out.split("|", 1)[1].strip().replace("⚠️疑似重复", "⚠️库内已有同题")
        return names[:n], dup, out
    except Exception:
        return [], "", ""


# ─────────────────────── Reddit（2026-11-17 接入） ───────────────────────
# reddit.com 本体对本机出口 IP 全 403（.json / .rss / 网页均 Blocked），
# 改走 Photon 归档 arctic-shift（主）与 pullpush（兜底），两者都带真实赞数。
REDDIT_ARCTIC = "https://arctic-shift.photon-reddit.com/api"
REDDIT_PULLPUSH = "https://api.pullpush.io/reddit/search"
REDDIT_PICK_MIN = 2        # 楼中楼入选门槛（真实赞数，不是代理分）
REDDIT_PICK_LIMIT = 120    # 楼中楼上限
REDDIT_MAIN_MIN = 1        # 主楼门槛（负分/0 分是纯噪音，直接剔）
REDDIT_MAX_PAGES = 3       # 每帖最多翻几页评论（每页 100）

_RD_CACHE = {}


def _reddit_pid(url):
    """从各种 reddit 链接里取帖子 id（/comments/<id>、redd.it/<id>、/r/<sub>/s/<id> 短链除外）。"""
    m = re.search(r"/comments/([A-Za-z0-9]{5,10})", url)
    if m:
        return m.group(1).lower()
    m = re.search(r"redd\.it/([A-Za-z0-9]{5,10})", url)
    if m:
        return m.group(1).lower()
    return None


def _rd_get(url, tries=2, timeout=60):
    """归档接口专用：轻 UA + 重试（chunked 大响应偶发读超时）。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dsh-clip/1.0",
                                                      "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as _e:
            last = _e
            time.sleep(2 + i * 2)
    raise last


def reddit_post(pid):
    """帖元信息：title/subreddit/score/num_comments/created_utc/author/url/selftext/permalink。"""
    if pid in _RD_CACHE:
        return _RD_CACHE[pid]
    post = None
    try:
        d = _rd_get(REDDIT_ARCTIC + "/posts/ids?ids=" + pid, timeout=30)
        post = (d.get("data") or [None])[0]
    except Exception:
        post = None
    if not post:
        try:
            time.sleep(1.2)
            d = _rd_get(REDDIT_PULLPUSH + "/submission/?ids=" + pid, timeout=30)
            post = (d.get("data") or [None])[0]
        except Exception:
            post = None
    if not post:
        return None
    ts = post.get("created_utc") or 0
    when = datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S") if ts else ""
    permalink = post.get("permalink") or ""
    _RD_CACHE[pid] = {"title": html_unesc(post.get("title") or ""),
                      "sub": post.get("subreddit") or "",
                      "score": post.get("score") or 0,
                      "num_comments": post.get("num_comments") or 0,
                      "when": when,
                      "author": ("u/" + html_unesc(post.get("author"))) if post.get("author") not in (None, "[deleted]", "") else "",
                      "url": post.get("url") or "",
                      "selftext": post.get("selftext") or "",
                      "page": ("https://www.reddit.com" + permalink) if permalink else ("https://www.reddit.com/comments/" + pid)}
    return _RD_CACHE[pid]


def html_unesc(s):
    return htmllib.unescape(s or "")


def _rd_clean_body(t):
    """评论正文清洗：反转义、剔零宽、去掉 Reddit 的 >!spoiler!< 包裹但保留文字。"""
    t = html_unesc(t or "")
    t = t.replace("\u200b", "").replace("\u00a0", " ")
    t = re.sub(r">!\s*(.+?)\s*!<", r"\1", t, flags=re.S)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def reddit_comments(pid, deep="pick"):
    """抓 Reddit 评论（含楼中楼）。deep="pick" 按赞数筛选+补父链；"all" 全量不筛。"""
    raw = []
    before = None
    try:
        for _p in range(REDDIT_MAX_PAGES):
            u = REDDIT_ARCTIC + "/comments/search?link_id=%s&limit=100" % pid
            if before:
                u += "&before=%d" % before
            d = _rd_get(u, timeout=70)
            batch = d.get("data") or []
            if not batch:
                break
            raw.extend(batch)
            before = min(int(c.get("created_utc") or 0) for c in batch)
            if len(batch) < 100:
                break
            time.sleep(0.5)
    except Exception as _e:
        print("Reddit 评论抓取失败（%s），评论区留空" % _e)
    if not raw:
        return None

    by_id, rows = {}, []
    for c in raw:
        cid = c.get("id")
        body = _rd_clean_body(c.get("body"))
        if not cid or not body or body in ("[deleted]", "[removed]"):
            continue
        if cid in by_id:
            continue
        by_id[cid] = {"id": cid, "parent": (c.get("parent_id") or ""),
                      "author": html_unesc(c.get("author") or "匿名"),
                      "score": int(c.get("score") or 0),
                      "ts": int(c.get("created_utc") or 0),
                      "body": body}
        rows.append(by_id[cid])
    if not rows:
        return None

    # 层级：父在集合内则 depth=父+1，否则按顶层处理
    for r in rows:
        d, cur, guard = 0, r, 0
        while True:
            par = cur["parent"] or ""
            if not par.startswith("t1_"):
                break
            p = by_id.get(par[3:])
            if not p or guard > 30:
                break
            d += 1
            cur = p
            guard += 1
        r["depth"] = d
        r["when"] = datetime.fromtimestamp(r["ts"], timezone.utc).astimezone().strftime("%Y-%m-%d") if r["ts"] else ""

    raw_count = len(rows)
    picked = False
    kept_rows = rows
    if deep != "all":
        keep_ids = {r["id"] for r in rows
                    if ((r["depth"] == 0 and r["score"] >= REDDIT_MAIN_MIN) or r["score"] >= REDDIT_PICK_MIN)}
        subs = sorted([r for r in rows if r["depth"] >= 1 and r["id"] in keep_ids],
                      key=lambda r: -r["score"])[:REDDIT_PICK_LIMIT]
        keep_ids = {r["id"] for r in rows if r["depth"] == 0 and r["score"] >= REDDIT_MAIN_MIN} | {r["id"] for r in subs}
        changed = True
        while changed:            # 补齐父链，防断链
            changed = False
            for r in rows:
                if r["id"] in keep_ids and r["parent"].startswith("t1_"):
                    pid2 = r["parent"][3:]
                    if pid2 in by_id and pid2 not in keep_ids:
                        keep_ids.add(pid2)
                        changed = True
        kept_rows = [r for r in rows if r["id"] in keep_ids]
        picked = len(kept_rows) < len(rows)

    # 排序：主楼按赞数降序，子楼紧跟各自父楼（树序 = 先序遍历）
    kids = {}
    tops = []
    for r in rows:
        par = r["parent"] or ""
        if par.startswith("t1_") and par[3:] in by_id:
            kids.setdefault(par[3:], []).append(r)
        else:
            tops.append(r)
    for arr in kids.values():
        arr.sort(key=lambda r: -r["score"])
    tops.sort(key=lambda r: -r["score"])
    lead = reddit_post(pid) or {}
    keepset = {x["id"] for x in kept_rows}
    ordered, all_ordered = [], []

    def walk(node, out, keep):
        if keep is not None and node["id"] not in keep:
            return
        out.append(node)
        for k in kids.get(node["id"], []):
            walk(k, out, keep)

    for t in tops:
        walk(t, ordered, keepset)
        walk(t, all_ordered, None)
    return {"main": ordered, "all_rows": all_ordered, "extra": [], "source": "reddit", "endHint": None,
            "story": lead.get("title") or "", "rd_url": lead.get("page") or "",
            "points": lead.get("score") or 0, "sub": lead.get("sub") or "",
            "picked": picked, "raw_count": raw_count,
            "kept": len(ordered)}


def reddit_comments_section(cm, note_only=False):
    rows = cm.get("main") or []
    deep = sum(1 for c in rows if c["depth"] > HN_MAX_INDENT)
    if cm.get("picked"):
        head = "## 评论区（%d 条：主楼 %d + 精选楼中楼 %d）" % (
            len(rows), sum(1 for c in rows if c["depth"] == 0),
            sum(1 for c in rows if c["depth"] >= 1))
    else:
        head = "## 评论区（%d 条）" % len(rows)
    note = "Reddit 评论 %d 条，按赞数排序；缩进最多 %d 层" % (len(rows), HN_MAX_INDENT)
    if deep:
        note += "，更深的 %d 条用 ↳层级 标注" % deep
    if cm.get("picked"):
        note += ("。**楼中楼已筛选**：主楼取 ≥%d 赞，楼中楼按赞数取 ≥%d 赞并补齐父链，保留 %d 条"
                 "（原始 %d 条）；全量未筛选的原文另存 `_底稿/`。"
                 % (REDDIT_MAIN_MIN, REDDIT_PICK_MIN,
                    sum(1 for c in rows if c["depth"] >= 1), cm.get("raw_count") or 0))
    link = "讨论页：[%s](%s)（%d 赞）。" % (cm.get("story") or "Reddit", cm.get("rd_url") or "",
                                             cm.get("points") or 0)
    out = [head, "", link, "", note if note.endswith("。") else note + "。", ""]
    if note_only:
        return "\n".join(out).rstrip()
    for c in rows:
        d = c["depth"]
        pad = INDENT * min(d, HN_MAX_INDENT)
        mark = "" if d <= HN_MAX_INDENT else "↳%d " % d
        head_l = "%s**%s%s · %s · %d 赞**" % (pad, mark, c["author"], c["when"], c["score"])
        txt = c["body"] or "（无正文）"
        if d == 0:
            if out[-1] != "":
                out.append("")
            out += [head_l, ""] + txt.split("\n")
        else:
            out.append(head_l)
            out += [pad + ln if ln else ln for ln in txt.split("\n")]
    return "\n".join(out).rstrip()


def build(doc, url, tags_line, summary, body, cmts, auto=False):
    name = safe_name(doc["title"])

    # 2026-11-13 用户定版：元信息进 frontmatter（created/url/author/tags），正文不再有标签行与原文行
    created = doc.get("date") or datetime.now().strftime("%Y-%m-%d")
    fm = ["---", "created: %s" % created, "url: %s" % url]
    if doc.get("author"):
        fm.append("author: %s" % doc["author"])
    tag_list = [t.lstrip("#").strip() for t in (tags_line or "").split() if t.lstrip("#").strip()]
    if tag_list:
        fm.append("tags:")
        fm += ["  - %s" % t for t in tag_list]
    fm += ["---", ""]

    parts = fm + ["# %s" % doc["title"], ""]
    if summary:
        parts += ["摘要：" + summary, ""]
    parts += [body]
    if auto:
        # 标记必须在评论区之前（正文末尾、--- 分隔线之前）——否则 refresh/merge
        # 按 "## 评论区" 切 head 时会把它切进评论区段，被挪到条目中间甚至丢掉
        parts += ["", "<!-- 待精修 -->", ""]

    if cmts and not (cmts["main"] or cmts["extra"]) and cmts.get("endHint"):
        n = _news_comment_count(cmts.get("newsid"))
        if n:
            note = "文章发布时间太久，IT之家 已不再提供该文评论"
            if n:
                note += "（页面显示 %d 条）" % n
            parts = parts + ["", "---", "", "评论区：" + note + "。", ""]
    elif cmts and cmts.get("source") == "hn":
        parts += ["", "---", "", hn_comments_section(cmts)]
    elif cmts and cmts.get("source") == "reddit":
        parts += ["", "---", "", reddit_comments_section(cmts)]
    elif cmts and (cmts["main"] or cmts["extra"]):
        parts += ["", "---", "", comments_section(cmts)]
    return name, "\n".join(parts).rstrip() + "\n"


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_text(path, text):
    """写笔记（2026-11-16 统一）：一律 with 语义，写完立即关句柄。

    刻意不做「临时文件 + os.replace」的原子替换——replace 会换 inode，
    Obsidian 里该笔记的创建时间/排序会跟着变（日报线踩过这个坑）。
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-comments", action="store_true")
    ap.add_argument("--tags", default=None, help="覆盖自动标签，空格分隔")
    ap.add_argument("--name", default=None)
    ap.add_argument("--keep-remote", action="store_true", help="（默认行为，保留此参数兼容旧命令）")
    ap.add_argument("--local-images", action="store_true", help="下载图片到 attachments/ 并改用相对路径")
    ap.add_argument("--auto", action="store_true", help="按钮剪藏：末尾加待精修标记")
    ap.add_argument("--stdout", action="store_true", help="只抓取并打印正文，不写文件（精修时安全读取）")
    ap.add_argument("--refresh-comments", action="store_true",
                    help="只重抓评论区并替换已有笔记里的 ## 评论区 段，其余内容不动")
    ap.add_argument("--summary", default=None, help="摘要；缺省取正文前 3 段第一个含数字句（2026-11-16 定），找不到回落首段前两句")
    ap.add_argument("--no-translate", action="store_true", help="英文文章不自动译中（默认会译）")
    ap.add_argument("--no-tag-suggest", action="store_true", help="不打印标签候选")
    ap.add_argument("--deep", default="pick", choices=["pick", "all"],
                    help="楼中楼筛选（HN／Reddit）：pick=精选（默认，主楼全收+筛选+另存全量底稿）／all=全量不筛")
    a = ap.parse_args()
    # 工具集同步（gh_sync.py：本机推/跟随机拉，详见该脚本头注释）
    # 2026-11-16：--stdout 是只读路径（精修/核对原文用），不该有推送副作用
    if not a.stdout:
        try:
            _gs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh_sync.py")
            if os.path.exists(_gs):
                subprocess.Popen([sys.executable, _gs, "auto", "--quiet"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # Reddit：reddit.com 本体对本机 IP 全 403，帖元信息与评论都走 Photon 归档
    # （arctic-shift，兜底 pullpush）。self post 正文直接用归档里的 selftext（保 markdown），
    # link post 则抓它指向的外链页当正文，标题/时间/作者仍用 Reddit 帖。
    _rd_pid = _reddit_pid(a.url)
    _rd_meta = reddit_post(_rd_pid) if _rd_pid else None
    if not _rd_meta:
        page, final = fetch(a.url)
    else:
        _rd_st = (_rd_meta.get("selftext") or "").strip()
        if _rd_st:
            page, final = "<html></html>", _rd_meta["page"]
        else:
            try:
                page, final = fetch(_rd_meta.get("url") or _rd_meta["page"])
            except Exception as _e:
                print("Reddit 外链正文抓取失败（%s），正文留空" % _e)
                page, final = "<html></html>", _rd_meta["page"]

    # HN 讨论帖：正文用帖子指向的原文页（HN 页本身只有列表），评论走 Algolia
    _hn_url = None
    _hn_meta = None
    if "news.ycombinator.com" in final:
        _hn_meta = hn_story(final)
        if _hn_meta:
            _hn_url = final
            if _hn_meta["url"]:
                try:
                    page, final = fetch(_hn_meta["url"])
                except Exception as _e:
                    print("HN 原文页抓取失败（%s），正文改为讨论帖摘要" % _e)
            if not _hn_meta["url"] or "news.ycombinator.com" in final:
                page, final = "<html></html>", _hn_url

    if a.refresh_comments:
        path_f = os.path.join(a.out, a.name + ".md") if a.name else None
        if not path_f or not os.path.exists(path_f):
            sys.exit("--refresh-comments 需要 --name 指定已存在的笔记名")
        if "mydrivers.com" in final:
            cm = mydrivers_comments(final)
        elif "v2ex.com" in final:
            cm = v2ex_comments(final)
        else:
            cm = ithome_comments(page)
        if not cm:
            sys.exit("该页没有评论接口")
        _bk = _backup_or_die(path_f)
        old = _read_text(path_f)
        head = old.split("## 评论区")[0].rstrip() if "## 评论区" in old else old.rstrip()
        head = re.sub(r"\n+-{3,}\s*$", "", head).rstrip()
        _mo = re.search(r"## 评论区（(\d+) 条）", old)
        _old_n = int(_mo.group(1)) if _mo else 0
        _new_n = len(cm["main"]) + len(cm["extra"])
        if _old_n and _new_n < _old_n:
            merged, add_n = merge_comments(old, cm)
            if merged is None:
                print("评论变少（%d → %d 条）且无新增条目，已保留原文件不覆盖" % (_old_n, _new_n))
                return
            _write_text(path_f, head + "\n\n---\n\n" + merged + "\n")
            print(path_f)
            print("评论已增量合并：%d → %d 条（新增 %d 条，旧条目全部保留）" % (_old_n, _old_n + add_n, add_n))
            return
        if cm["main"] or cm["extra"]:
            _write_text(path_f, head + "\n\n---\n\n" + comments_section(cm) + "\n")
        elif cm.get("endHint"):
            n = _news_comment_count(cm.get("newsid"))
            if n == 0:
                _write_text(path_f, head + "\n")
                print("评论已更新：0 条")
                return
            note = "文章发布时间太久，IT之家 已不再提供该文评论"
            if n:
                note += "（页面显示 %d 条）" % n
            _write_text(path_f, head + "\n\n---\n\n评论区：" + note + "。\n")
        else:
            _write_text(path_f, head + "\n")   # 0 条时不留空评论区
        print(path_f)
        print("评论已更新：" + re.search(r"（(\d+) 条）", comments_section(cm)).group(1) + " 条")
        return

    title = (re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) and
             strip_tags(re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1)))
    title = title or meta(page, "og:title")
    if _rd_meta and _rd_meta.get("title"):
        title = _rd_meta["title"]
    if not title or title.strip() == "未命名":
        try:
            from title_fix import title_fallback
            title = title_fallback(page, current=title, site_label="Interconnects") or title
        except Exception:
            pass
    title = title or "未命名"
    title = re.sub(r"\s*[-_|]\s*(IT之家|少数派|36氪|V2EX).*$", "", title).strip()
    if _hn_meta and _hn_meta["title"]:
        title = _hn_meta["title"]

    pub = None
    m = re.search(r'<span id="pubtime_baidu">(.*?)</span>', page)
    if m:
        pub = strip_tags(m.group(1))
    date = None
    if pub:
        try:
            y, mo, d = pub.split(" ")[0].replace("/", "-").split("-")
            date = "%s-%02d-%02d" % (y, int(mo), int(d))
        except Exception:
            pub = None            # 2026-11-16：pubtime 格式变了不该让整次剪藏崩，退回 meta 取时间
    if not date:
        for k in ("article:published_time", "datePublished", "pubdate", "date"):
            v = meta(page, k)
            if v and re.match(r"\d{4}-\d{2}-\d{2}", v):
                date = v[:10]
                break
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    # 2026-11-13 用户定：created 带发布时间（IT之家 pubtime「2026-11-07 20:11」→ ISO datetime）
    hm = None
    if pub:
        hm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", pub.strip())
    if hm:
        date = "%sT%02d:%02d:%02d" % (date, int(hm.group(1)), int(hm.group(2)), int(hm.group(3) or 0))
    elif pub:
        date += "T00:00:00"
    else:
        # 非 IT之家：从 meta/JSON-LD 里取完整 ISO 时间
        _hit = False
        for k in ("article:published_time", "datePublished"):
            v = meta(page, k)
            if v and re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", v):
                date = v.replace(" ", "T")[:19]
                _hit = True
                break
            if v and re.match(r"\d{4}-\d{2}-\d{2}$", v):
                date = v + "T00:00:00"
                # 只有日期算弱命中：不阻断多源兜底（JSON-LD 常有完整时间）
                break
        # 2026-11-17：多源兜底（JSON-LD datePublished → 各类 meta → <time datetime>
        # → dateModified 兜底）。substack 的 modified 常排在 published 前面，故不能按出现顺序取。
        if not _hit:
            try:
                from title_fix import date_fallback
                _fb = date_fallback(page)
                if _fb:
                    date = _fb
            except Exception:
                pass

    if _hn_meta and _hn_meta["when"]:
        date = _hn_meta["when"]
    if _rd_meta and _rd_meta.get("when"):
        date = _rd_meta["when"]
    author = None
    m = re.search(r'<span id="author_baidu">.*?<strong>(.*?)</strong>', page, re.S)
    if m:
        author = strip_tags(m.group(1))
    author = author or meta(page, "author") or meta(page, "article:author")
    if author and (re.search(r"https?://|facebook|@", author) or len(author) > 40):
        author = None
    # 2026-11-17：多源兜底（JSON-LD author → meta 各键 → rel=author → class*=author）。
    # 英文站的作者基本都在 JSON-LD 里，原来只读 meta 所以常缺。
    if not author:
        try:
            from title_fix import author_fallback
            author = author_fallback(page)
        except Exception:
            pass

    if _rd_meta and _rd_meta.get("author"):
        author = _rd_meta["author"]

    # 中文站定制：作者/时间在页面节点或 JS 变量里，meta 全空（2026-11-17 实测）
    #   量子位：<span class="author">…<a rel="author">思邈</a> + <span class="date"> + <span class="time">
    #   快科技：<span style="color:#666">责任编辑：若风</span> + var g_PostDateStr = new Date('2026-11-09 08:53:26.877')
    #   少数派/title_fix 的 JSON-LD 兜底已覆盖，不在此列
    if "qbitai.com" in final:
        if not author:
            m = re.search(r'class="author".*?rel="author"[^>]*>([^<]+)<', page, re.S)
            if m:
                author = strip_tags(m.group(1)).strip() or None
        m = re.search(r'<span class="date">(\d{4}-\d{2}-\d{2})</span>', page)
        if m:
            t = re.search(r'<span class="time">(\d{2}:\d{2}(?::\d{2})?)</span>', page)
            date = m.group(1) + "T" + (t.group(1) if t else "00:00:00")
    if "mydrivers.com" in final:
        if not author:
            m = re.search(r'责任编辑[：:]\s*([^<\s]{1,20})', page)
            if m:
                author = m.group(1)
        m = re.search(r"g_PostDateStr\s*=\s*new Date\('(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", page)
        if m:
            date = m.group(1) + "T" + m.group(2)

    # V2EX：时间与作者只在 API 里（页面无 meta 发布时间）
    if "v2ex.com" in final:
        try:
            tm = re.search(r"/t/(\d+)", final)
            tp = v2ex_api("topics/show.json", id=tm.group(1)) if tm else None
            tp = tp[0] if isinstance(tp, list) and tp else tp
            if tp:
                if tp.get("created"):
                    date = datetime.fromtimestamp(tp["created"]).strftime("%Y-%m-%dT%H:%M:%S")
                if (tp.get("member") or {}).get("username"):
                    author = tp["member"]["username"]
        except Exception:
            pass

    body = extract_ithome(page) if "ithome.com" in final else None
    cleaned = False          # 该 body 是否已经过 clean_body（见下方 2026-11-16 说明）
    if not body and "v2ex.com" in final:
        body = extract_v2ex(page)        # extract_v2ex 内部已 clean_body
        cleaned = bool(body)
    if not body:
        t = extract_trafilatura(page, final) or ""
        g = extract_generic(page) or ""
        # 双跑取长：两版都先清洗再比字数（更公平）；差距很小时优先 trafilatura（结构更全）
        tc = clean_body(t, title) if t else ""
        gc = clean_body(g, title) if g else ""
        body = tc if len(tc) >= len(gc) * 0.97 else gc
        if not body:
            body = tc or gc
        cleaned = True                   # tc/gc 已是 clean_body 的产物
    if not cleaned:
        # 2026-11-16：只有 extract_ithome 的原始 md 需要这一步；双跑取长路径原来会
        # 对已经清洗过的正文再跑一次全量正则（8 万字的稿子白跑一遍，且二次去重/截断有副作用）
        body = clean_body(body, title)
    if _hn_meta and not body.strip():
        # HN 帖没有外链正文（Ask HN / 抓不到原文）：退化为帖内说明 + 原文链接
        _t = _hn_meta["title"]
        body = "HN 讨论帖：%s（%d 分，%d 条评论）。" % (_t, _hn_meta["points"], _hn_meta.get("comments") or 0)
        if _hn_meta["url"]:
            body += "\n\n原文：[%s](%s)" % (_t, _hn_meta["url"])
        if _hn_meta["text"]:
            body += "\n\n" + _hn_meta["text"]
    if _rd_meta and not (_rd_meta.get("selftext") or "").strip() and not (body or "").strip():
        # link post 的外链抓不到（付费墙/反爬，如 Reuters 401、CAPTCHA）时不要留空正文，
        # 退化为「讨论帖说明」：原文链接 + Reddit 分数与评论数 + 讨论页，与 HN Ask HN 的处理一致。
        _rd_host = re.sub(r"^https?://(www\.)?", "", (_rd_meta.get("url") or "")).split("/")[0] or "原站"
        body = "\n".join([
            "原文（%s）全文抓取受限，未能取回正文；此处保留 Reddit 讨论与元信息。" % _rd_host,
            "",
            "- 原文链接：%s" % (_rd_meta.get("url") or ""),
            "- Reddit 讨论：r/%s · %d 赞 · %d 条评论"
            % (_rd_meta.get("sub") or "", _rd_meta.get("score") or 0, _rd_meta.get("num_comments") or 0),
            "- 讨论页：%s" % (_rd_meta.get("page") or ""),
        ])
    if _rd_meta and (_rd_meta.get("selftext") or "").strip():
        body = html_unesc(_rd_meta["selftext"]).strip()
        # link post 的外链页首段常是图片/卡片列表，摘要改用它自己的自述（若有 20 字以上）
        _rd_st_txt = body.strip()
        if len(_rd_st_txt) >= 20:
            summary = _rd_st_txt.split("\n\n")[0].strip()
    body = fix_hotlinks(body)

    # 英文文章不自动翻译：只检测并强制标记待精修，由精修队员译中（2026-11-12 用户决定）
    _need_translate = False
    if not a.no_translate and trans.is_english(body):
        _need_translate = True
        print("检测到英文文章：保留英文原文，已标记待精修（由精修队员译成中文）")

    kws = meta(page, "keywords") or ""
    _JUNK_TAG = {"业界动态", "IT资讯", "科技", "新闻", "数码", "手机", "电脑", "软件", "硬件", "互联网", "人工智能技术"}
    # 快科技的 meta keywords 常直接等于标题（实测「国际油价创2020年来…」整句被当标签），
    # 所以 keywords 兜底还要剔除与标题相同/是标题子串的项（2026-11-17）
    _tkey = re.sub(r"\W+", "", title or "")
    tags = [k.strip() for k in re.split(r"[,，]", kws) if k.strip() and k.strip().lower() not in ("none", "null", "undefined", "na")
            and k.strip() not in _JUNK_TAG
            and re.sub(r"\W+", "", k.strip()) not in (_tkey, "")
            and len(k.strip()) <= 20]
    if "v2ex.com" in final:                      # V2EX 页面标签是 /tag/xxx 链接
        tags = [urllib.parse.unquote(x) for x in re.findall(r'href="/tag/([^"]+)"', page)]
        if not tags:
            nm = re.search(r'href="/go/([^"]+)"[^>]*>([^<]+)</a>', page)
            tags = [strip_tags(nm.group(2))] if nm else []
    _sg_msg = ""
    if a.tags:
        tags = a.tags.split()
    elif not getattr(a, "no_tag_suggest", False):
        # 2026-11-17：词典只做重复检测，不再出标签。
        # 实测词典基于字符 n-gram，只认库内见过的词——库外新标题（高通骁龙/小米 17 Ultra/
        # 比亚迪/红米 K90）四条全错（分别给成 马斯克、OPPO手机、储能、苹果）。
        # 标签改为直接调 LLM（GLM-5.3-flash 主 / laguna-xs-2.1 备），它按语义判断。
        _names, _dup, _brief = suggest_tags(title)   # 仅取 dup（⚠️库内已有同题）
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from tag_llm import suggest as _llm_suggest
            _lt = _llm_suggest(title)
            if _lt:
                tags = _lt
                _sg_msg = "标签候选（LLM 生成，精修复核）: " + "、".join(_lt)
        except Exception as _e:
            pass
        if not tags:
            # LLM 不可用时的兜底：退回词典候选（总比站点 keywords 强）
            if _names:
                tags = _names
                _sg_msg = "标签候选（LLM 不可用·词典兜底，精修复核）: " + "、".join(_names)
        if _dup:
            _sg_msg = (_sg_msg + " | " if _sg_msg else "") + _dup
    # 2026-11-16：上限从 2 提到 3——标签规则本就是 1-3 个，tags[:2] 会把用户显式给的第三个标签丢掉
    tags_line = " ".join("#" + t.replace(" ", "").lstrip("#") for t in tags[:3] if t.strip())
    cmts = None
    if not a.no_comments:
        if _rd_pid:
            cmts = reddit_comments(_rd_pid, deep=getattr(a, "deep", "pick"))
        elif "ithome.com" in final:
            cmts = ithome_comments(page)
        elif "mydrivers.com" in final:
            cmts = mydrivers_comments(final)
        elif "v2ex.com" in final:
            cmts = v2ex_comments(final)
        elif _hn_url or "news.ycombinator.com" in final:
            cmts = hn_comments(_hn_url or final, deep=getattr(a, "deep", "pick"))

    doc = {"title": title, "pub": pub, "date": date, "author": author, "desc": meta(page, "description")}

    summary = a.summary
    if not summary:
        # 2026-11-17：先剥掉行内图片（外链页首段常是"图片+标题"卡片列表，摘约会抓成图链）
        _b2 = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        paras = [re.sub(r"[*_>`]", "", x.strip()) for x in _b2.split("\n")
                 if x.strip() and not x.startswith(("#", "![", "原文：", "- ", "* ", "|", ">"))
                 and not re.fullmatch(r"\[[^\]]*\]\([^)]*\)", x.strip())
                 and not x.strip().startswith(("http://", "https://"))
                 # 含 URL 且剥掉链接后剩不到 20 字的行不能当摘要（列表里的「原文链接：…」）
                 and not (re.search(r"https?://", x) and
                          len(re.sub(r"https?://\S+", "", x).strip(" -：:·|")) < 20)]
        draft = draft_summary(paras)
        if draft:
            summary = draft
        else:
            src = next((p for p in paras if len(p) >= 30), paras[0] if paras else (doc["desc"] or ""))
            sents = re.findall(r"[^。！？!?]+[。！？!?]?", src)
            summary = "".join(sents[:2]).strip()
        if len(summary) > 160:
            summary = summary[:157].rstrip("，、；") + "…"

    name, md = build(doc, final, tags_line, summary, body, cmts, auto=a.auto or _need_translate)
    if _need_translate:
        md = md.replace("<!-- 待精修 -->", "<!-- 待精修：英文原文，请全文译成中文 -->")
    if a.stdout:
        print(md)
        return
    if a.name:
        name = a.name
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, name + ".md")
    _bk = _backup_or_die(path)
    _write_text(path, md)
    # HN 精选模式下另存全量底稿（未筛选、未翻译），放 _底稿/ 子目录，不混进笔记列表
    if cmts and cmts.get("source") == "reddit" and cmts.get("picked"):
        try:
            _c2 = dict(cmts)
            _c2["main"] = cmts.get("all_rows") or []
            _c2["picked"] = False
            _sec = reddit_comments_section(_c2)
            _fm = ["---", "created: %s" % (doc.get("date") or datetime.now().strftime("%Y-%m-%d")),
                   "url: %s" % (_rd_meta.get("page") if _rd_meta else final), "---", ""]
            tag_list = [t.lstrip("#").strip() for t in (tags_line or "").split() if t.lstrip("#").strip()]
            if tag_list:
                _fm += ["tags:"]
                _fm += ["  - %s" % t for t in tag_list]
                _fm += [""]
            _ddir = os.path.join(a.out, "_底稿")
            os.makedirs(_ddir, exist_ok=True)
            _dp = os.path.join(_ddir, name + "_评论全文.md")
            _backup_or_die(_dp)
            _write_text(_dp, "\n".join(_fm) + "# %s（评论全文，%d 条）\n\n"
                         % (doc.get("title") or name, len(_c2["main"])) + _sec + "\n")
            print("评论全文底稿：%s（%d 条）" % (_dp, len(_c2["main"])))
        except Exception as _e:
            print("评论全文底稿写入失败：%s" % _e)
    if cmts and cmts.get("source") == "hn" and cmts.get("picked"):
        try:
            _rows = cmts.get("all_rows") or []
            _c2 = dict(cmts)
            _c2["main"] = _rows
            _c2["picked"] = False
            _sec = hn_comments_section(_c2)
            _fm = ["---", "created: %s" % (doc.get("date") or datetime.now().strftime("%Y-%m-%d")),
                   "url: %s" % final, "---", ""]
            tag_list = [t.lstrip("#").strip() for t in (tags_line or "").split() if t.lstrip("#").strip()]
            if tag_list:
                _fm = _fm[:-1] + ["tags:"] + ["  - %s" % t for t in tag_list] + ["---", ""]
            _note = ["> 评论全量底稿：%d 条评论全部英文原文，未筛选、未翻译。" % len(_rows),
                     "> 阅读版（主楼全收 + 精选楼中楼 %d 条，中文）见 `%s.md`。" % (cmts.get("kept_sub") or 0, name), ""]
            _draft = "\n".join(_fm + ["# %s" % doc["title"], ""] + _note + [_sec]) + "\n"
            _ddir = os.path.join(a.out, "_底稿")
            os.makedirs(_ddir, exist_ok=True)
            _dpath = os.path.join(_ddir, name + "_评论全量.md")
            _backup_or_die(_dpath)
            _write_text(_dpath, _draft)
            print("评论全量底稿 → %s" % _dpath)
        except Exception as _e:
            print("底稿写入失败（不影响笔记）：%s" % _e)
    if not a.local_images:
        # 图片托管到 GitHub（gh-proxy 加速），失败不阻断剪藏
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from pathlib import Path as _Path
            import gh_img
            _ok, _fail, _ = gh_img.process_files([_Path(path)], quiet=True, backup=_backup_or_die)
            if _ok or _fail or _:
                print("图片托管：新上传 %d / 失败 %d / 改写 %d 篇" % (_ok, _fail, _))
        except Exception as _e:
            print("图片托管失败（保留原链接）：%s" % _e)
    if a.local_images:
        try:
            from localize_images import localize
            localize(path)
        except Exception as e:
            print("图片本地化失败（保留远程链接）:", e)
    # 标签候选提示（候选计算在 tags 段已做，这里只打印，避免重复跑）
    if _sg_msg:
        print(_sg_msg)
    print(path)
    print("正文 %d 字 / 标签 %s / 评论 %s" % (
        len(body), tags_line or "无",
        ("%d 条" % (len(cmts["main"]) + len(cmts["extra"]))) if cmts else "无"))


if __name__ == "__main__":
    main()