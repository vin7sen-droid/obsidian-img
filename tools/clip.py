#!/usr/bin/env python3
"""把网页正文（+ IT之家评论）抓成 Obsidian 剪藏笔记，落到 ReadItLater Inbox。

用法:
    python3 tools/clip.py <url> [--out DIR] [--no-comments] [--tags "a b c"] [--name 文件名]

约定（与既有剪藏一致）:
    [[ReadItLater]] [[Article]] → YAML frontmatter → # 标题 → 标签行 → 原文行 → 正文
    IT之家额外抓评论区：加粗条目头 + 正文段落（字号与正文一致，不用标题/引用块）
"""
import argparse, gzip, hashlib, html as htmllib, json, os, re, shutil, subprocess, sys, time
import urllib.request
import trans
from datetime import datetime, timedelta

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
    if r.headers.get("Content-Encoding") == "gzip":
        d = gzip.decompress(d)
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
    blocked = False
    try:
        urllib.request.urlopen(urllib.request.Request(
            src, headers={"User-Agent": UA, "Referer": "file:///note.md"}), timeout=15)
    except Exception as e:
        blocked = "403" in str(e)
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


def meta(page, name):
    m = re.search(r'<meta\s+(?:name|property)="%s"\s+content="(.*?)"' % re.escape(name), page, re.I | re.S)
    if not m:
        m = re.search(r'<meta\s+content="(.*?)"\s+(?:name|property)="%s"' % re.escape(name), page, re.I | re.S)
    return htmllib.unescape(m.group(1)).strip() if m else None


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
    for k in (c.get("children") or []):
        kn = (k.get("userInfo") or {}).get("userNick") or "匿名"
        ru = (k.get("replyUserInfo") or {}).get("userNick")
        who = "↳ " + kn + (" 回复 " + ru if ru else "")
        kb = _ctxt(k) or "（图片评论）"
        kb = "\n".join(INDENT + ln for ln in kb.split("\n"))
        out += [INDENT + "**%s** · %s · 赞 %s" % (who, _when(k), k.get("support", 0)), "",
                kb, ""]
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
    return (ln.startswith("**")
            or ln.startswith(INDENT + "**")
            or ln.startswith(_ZOOM_OPEN + "**"))


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
    """旧评论区整段保留，把新抓到而旧里没有的条目追加进去；返回 (新markdown, 新增条数)"""
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
    seen = {_sec_fp(b) for b in items_old}
    added = [b for b in items_new if _sec_fp(b) not in seen]
    if not added:
        return None, 0
    m = re.search(r"（(\d+) 条）", header)
    old_n = int(m.group(1)) if m else len(items_old)
    add_n = sum(1 for b in added
                if not b.replace(_ZOOM_OPEN, "").lstrip().startswith("**热评 · "))
    total = old_n + add_n
    header = re.sub(r"（\d+ 条）", "（%d 条）" % total, header, count=1)
    out = [header, ""]
    if explain:
        out += [explain, ""]
    for b in items_old + added:
        out += [b, ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip(), add_n


# ---------- 组装 ----------

def build(doc, url, tags_line, summary, body, cmts, auto=False):
    name = re.sub(r'[\\/:*?"<>|]', " ", doc["title"]).strip()

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

    if cmts and not (cmts["main"] or cmts["extra"]) and cmts.get("endHint"):
        n = _news_comment_count(cmts.get("newsid"))
        if n:
            note = "文章发布时间太久，IT之家 已不再提供该文评论"
            if n:
                note += "（页面显示 %d 条）" % n
            parts = parts + ["", "---", "", "评论区：" + note + "。", ""]
    elif cmts and (cmts["main"] or cmts["extra"]):
        parts += ["", "---", "", comments_section(cmts)]
    if auto:
        parts += ["", "<!-- 待精修 -->", ""]
    return name, "\n".join(parts).rstrip() + "\n"


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
    ap.add_argument("--summary", default=None, help="1-3 句摘要；缺省用 meta description 截前两句")
    ap.add_argument("--no-translate", action="store_true", help="英文文章不自动译中（默认会译）")
    a = ap.parse_args()
    try:  # 工具集同步（gh_sync.py：本机推/跟随机拉，详见该脚本头注释）
        _gs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh_sync.py")
        if os.path.exists(_gs):
            subprocess.Popen([sys.executable, _gs, "auto", "--quiet"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        pass

    page, final = fetch(a.url)

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
        old = open(path_f, encoding="utf-8").read()
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
            open(path_f, "w", encoding="utf-8").write(head + "\n\n---\n\n" + merged + "\n")
            print(path_f)
            print("评论已增量合并：%d → %d 条（新增 %d 条，旧条目全部保留）" % (_old_n, _old_n + add_n, add_n))
            return
        if cm["main"] or cm["extra"]:
            open(path_f, "w", encoding="utf-8").write(head + "\n\n---\n\n" + comments_section(cm) + "\n")
        elif cm.get("endHint"):
            n = _news_comment_count(cm.get("newsid"))
            if n == 0:
                open(path_f, "w", encoding="utf-8").write(head + "\n")
                print("评论已更新：0 条")
                return
            note = "文章发布时间太久，IT之家 已不再提供该文评论"
            if n:
                note += "（页面显示 %d 条）" % n
            open(path_f, "w", encoding="utf-8").write(head + "\n\n---\n\n评论区：" + note + "。\n")
        else:
            open(path_f, "w", encoding="utf-8").write(head + "\n")   # 0 条时不留空评论区
        print(path_f)
        print("评论已更新：" + re.search(r"（(\d+) 条）", comments_section(cm)).group(1) + " 条")
        return

    title = (re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) and
             strip_tags(re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1)))
    title = title or meta(page, "og:title")
    if not title or title.strip() == "未命名":
        try:
            from title_fix import title_fallback
            title = title_fallback(page, current=title, site_label="Interconnects") or title
        except Exception:
            pass
    title = title or "未命名"
    title = re.sub(r"\s*[-_|]\s*(IT之家|少数派|36氪|V2EX).*$", "", title).strip()

    pub = None
    m = re.search(r'<span id="pubtime_baidu">(.*?)</span>', page)
    if m:
        pub = strip_tags(m.group(1))
    date = None
    if pub:
        y, mo, d = pub.split(" ")[0].replace("/", "-").split("-")
        date = "%s-%02d-%02d" % (y, int(mo), int(d))
    else:
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
        for k in ("article:published_time", "datePublished"):
            v = meta(page, k)
            if v and re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", v):
                date = v.replace(" ", "T")[:19]
                break
            if v and re.match(r"\d{4}-\d{2}-\d{2}$", v):
                date = v + "T00:00:00"
                break

    author = None
    m = re.search(r'<span id="author_baidu">.*?<strong>(.*?)</strong>', page, re.S)
    if m:
        author = strip_tags(m.group(1))
    author = author or meta(page, "author") or meta(page, "article:author")
    if author and (re.search(r"https?://|facebook|@", author) or len(author) > 40):
        author = None

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
    if not body and "v2ex.com" in final:
        body = extract_v2ex(page)
    if not body:
        t = extract_trafilatura(page, final) or ""
        g = extract_generic(page) or ""
        # 双跑取长：两版都先清洗再比字数（更公平）；差距很小时优先 trafilatura（结构更全）
        tc = clean_body(t, title) if t else ""
        gc = clean_body(g, title) if g else ""
        body = tc if len(tc) >= len(gc) * 0.97 else gc
        if not body:
            body = tc or gc
    body = clean_body(body, title)
    body = fix_hotlinks(body)

    # 英文文章不自动翻译：只检测并强制标记待精修，由精修队员译中（2026-11-12 用户决定）
    _need_translate = False
    if not a.no_translate and trans.is_english(body):
        _need_translate = True
        print("检测到英文文章：保留英文原文，已标记待精修（由精修队员译成中文）")

    kws = meta(page, "keywords") or ""
    tags = [k.strip() for k in re.split(r"[,，]", kws) if k.strip() and k.strip().lower() not in ("none", "null", "undefined", "na")]
    if "v2ex.com" in final:                      # V2EX 页面标签是 /tag/xxx 链接
        tags = [urllib.parse.unquote(x) for x in re.findall(r'href="/tag/([^"]+)"', page)]
        if not tags:
            nm = re.search(r'href="/go/([^"]+)"[^>]*>([^<]+)</a>', page)
            tags = [strip_tags(nm.group(2))] if nm else []
    if a.tags:
        tags = a.tags.split()
    tags_line = " ".join("#" + t.replace(" ", "").lstrip("#") for t in tags[:2] if t.strip())
    cmts = None
    if not a.no_comments:
        if "ithome.com" in final:
            cmts = ithome_comments(page)
        elif "mydrivers.com" in final:
            cmts = mydrivers_comments(final)
        elif "v2ex.com" in final:
            cmts = v2ex_comments(final)

    doc = {"title": title, "pub": pub, "date": date, "author": author, "desc": meta(page, "description")}

    summary = a.summary
    if not summary:
        paras = [re.sub(r"[*_>`]", "", x.strip()) for x in body.split("\n")
                 if x.strip() and not x.startswith(("#", "![", "原文："))
                 and not re.fullmatch(r"\[[^\]]*\]\([^)]*\)", x.strip())
                 and not x.strip().startswith(("http://", "https://"))]
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
    open(path, "w", encoding="utf-8").write(md)
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
    print(path)
    print("正文 %d 字 / 标签 %s / 评论 %s" % (
        len(body), tags_line or "无",
        ("%d 条" % (len(cmts["main"]) + len(cmts["extra"]))) if cmts else "无"))


if __name__ == "__main__":
    main()