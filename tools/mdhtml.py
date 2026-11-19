#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdhtml.py —— 日报 HTML 渲染单一真源（AI 日报与科技日报共用）。

用法:
  python3 mdhtml.py <input.md> [output.html] [--theme ai|tech]

两线入口都是薄转发（改实现请改本文件，别改入口）：
  tools/md2html.py            → theme=ai    （AI 日报）
  news-tools/mkhtml_sticky.py → theme=tech  （科技日报，沿用「读 out.md 写 out.html」旧习惯）

识别结构（两线版式都吃）:
  # 标题
  > 引用块（来源说明 / 时间，可多行；含「当日总结」「当日总结」的连续块渲染成 digest 卡）
  ## 当日焦点            → 段落卡片
  ## 一、分类（N 条）    → 条目列表
  1. **[标题](url)**  + 下一行摘要 + 下一行「（[来源](url)） 时间」   （两行式/三行式）
  1. **[标题](url)** — 摘要（[来源](url) 时间）                      （一行式，兼容旧稿）
"""
import html
import os
import re
import sys

ITEM_LINK = re.compile(r"^(\d+)\.\s+\*\*\[(.+?)\]\((.+?)\)\*\*\s+—\s+(.*)$")
ITEM_PLAIN = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s+—\s+(.*)$")
TITLE_LINK = re.compile(r"^(\d+)\.\s+\*\*\[(.+?)\]\((.+?)\)\*\*\s*$")
TITLE_PLAIN = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*$")
SECTION = re.compile(r"^##\s+(.+)$")
META = re.compile(r"（([^（）]*)）\s*(?:(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}))?\s*$")
SRC_BLOCK = re.compile(r"^(.*?)\s+(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}|\d{1,2}[-–]\d{2}\s+\d{1,2}:\d{2})\s*$")
LINK_ONE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
CN_NUM = "一二三四五六七八九十"

FONT_PATH = "/root/workspace/fonts/GoogleSans-latin.woff2"


# ── 主题 ────────────────────────────────────────────────────────────────
THEME_AI = {
    "name": "ai",
    "palette": ["#5B87C4", "#4E7A5B", "#B4553A", "#7A62A8", "#3D7F82", "#A8792C"],
    "nav_style": "vertical",     # 竖排胶囊（AI 定版）
    "head_style": "solid",       # h2 左边框纯色
    "digest_card": False,
    "hero_quotes": False,
    "footer_note": "共 {total} 条 · 分类导航可点击跳转",
    "nav_bg": "rgba(246,244,239,.96)",
}

THEME_TECH = {
    "name": "tech",
    "palette": ["#1a73e8", "#0f9d58", "#d9480f", "#6741d9", "#0b7285",
                "#c92a2a", "#2b8a3e", "#a61e4d", "#e67700", "#5f3dc4"],
    "nav_style": "chips",        # 横排胶囊（科技定版）
    "head_style": "gradient",    # h2 左边框渐变
    "digest_card": True,
    "hero_quotes": True,         # 顶部「> 时间：…」引用块
    "footer_note": "共 {total} 条 · 由 DSH 每日归档自动生成，条目均附原始来源链接",
    "nav_bg": "rgba(248,249,251,.94)",
    # 科技十板块的胶囊短名（AI 线用「去序号」自动短名，不需要表）
    "short_names": {
        "一、芯片与硬件": "芯片硬件", "二、消费电子与终端": "消费电子",
        "三、互联网与大厂动态": "互联网", "四、通信与网络": "通信网络",
        "五、软件、游戏与开源": "软件游戏", "六、网络安全与数据": "网络安全",
        "七、航天、能源与前沿科学": "航天能源", "八、生物科技与医疗硬件": "生物医疗",
        "九、政策、监管与商业": "政策商业", "十、融资与并购": "融资并购",
    },
}

CSS_COMMON = """
*{box-sizing:border-box}
a{color:inherit;text-decoration:none}
h1 a:hover,li .h a:hover{color:var(--accent);text-decoration:underline}
.nav{position:sticky;top:0;z-index:30;background:{nav_bg};backdrop-filter:blur(10px);
 transform:translateY(0);transition:transform .3s ease}
.nav.hide{transform:translateY(-115%)}
.nav a{flex:0 0 auto;border:1px solid var(--line);background:var(--card);color:#5F6672;
 text-align:center;transition:color .15s,border-color .15s,background .15s}
.nav a:hover{color:var(--accent);border-color:var(--accent);background:#FFF}
h2{font-weight:650;letter-spacing:-.01em;scroll-margin-top:20px}
ul{list-style:none;padding:0;margin:0}
li{background:var(--card);border:1px solid var(--line);transition:border-color .18s ease,box-shadow .18s ease}
li .h{font-weight:650;line-height:1.44;letter-spacing:-.01em}
li .h a{color:#1A73E8}
li .h a:hover{color:#174EA6;text-decoration:underline}
li .s{color:#3C4043}
li .s strong{color:#111827;font-weight:700}
li .m{color:#5F6368;display:flex;flex-wrap:wrap;gap:5px;align-items:center}
li .m a.tag{background:#E8F0FE;color:#174EA6;padding:2px 9px;border-radius:11px;text-decoration:none;font-size:12.5px}
li .m a.tag:hover{background:#D2E3FC;text-decoration:none}
li .m a .hn{background:#ff6600;color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600}
li .m .dot{color:#C3C9D2;margin:0 1px}
li .m .time{color:#5F6368;margin-left:2px}
footer{color:#9AA0A9;text-align:center;letter-spacing:.02em}
"""

CSS_AI = """
:root{--bg:#F6F4EF;--card:#FFFFFF;--ink:#1F2430;--muted:#636B78;--line:#E6E2DA;--accent:#2F6FD0}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
 line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:22px 16px 72px}
.hero{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 22px 18px;
 box-shadow:0 1px 2px rgba(31,36,48,.04)}
h1{font-size:22px;line-height:1.35;margin:0 0 14px;letter-spacing:-.01em;font-weight:650}
blockquote{margin:0;padding:11px 13px;border-left:3px solid var(--line);background:#F8F9FA;color:var(--muted);
 font-size:13px;border-radius:0 10px 10px 0;line-height:1.65}
footer blockquote{margin:8px 0;text-align:left}
.nav{display:flex;flex-wrap:nowrap;gap:7px;padding:9px 8px 10px;margin:0 0 13px;
 border-bottom:1px solid var(--line);overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav a{display:flex;align-items:center;justify-content:center;width:40px;padding:9px 4px;
 border-radius:16px;font-size:11.5px;line-height:1.3;letter-spacing:.06em;
 writing-mode:vertical-rl;text-orientation:upright}
.nav a i{font-style:normal;font-size:10.5px;color:#9AA0A9;margin-top:3px;letter-spacing:0}
h2{font-size:16.5px;margin:24px 0 10px;padding-left:11px;border-left:4px solid}
.focus{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 17px;display:grid;gap:15px;
 font-size:14px;color:#3A4150;line-height:1.78}
.focus p{margin:0}
.focus p+p{padding-top:14px;border-top:1px dashed #E6E2DA}
.focus p>b:first-child,.focus p>strong:first-child{color:var(--accent);letter-spacing:.02em}
h2 .cnt{font-size:12px;color:var(--muted);font-weight:400;margin-left:7px}
ul{display:grid;gap:9px}
li{border-radius:14px;padding:13px 14px 11px;box-shadow:0 1px 1px rgba(31,36,48,.03)}
li:hover{border-color:#D6D0C4;box-shadow:0 6px 16px rgba(31,36,48,.06)}
li .h .n{display:none}
li .h{font-size:17.5px;margin-bottom:5px}
li .s{font-size:15px;line-height:1.72;letter-spacing:.002em}
li .m{font-size:13px;margin-top:9px;line-height:1.5}
footer{margin-top:36px;font-size:12px}
@media(max-width:480px){h1{font-size:18.5px}.wrap{padding:14px 12px 56px}
 li{padding:15px 14px 13px}li .h{font-size:18px}li .s{font-size:15px}.hero{padding:18px 16px}}
"""

CSS_TECH = """
:root{--bg:#f8f9fb;--card:#fff;--ink:#1f2328;--muted:#6b7280;--line:#e3e6ea;--accent:#1a73e8}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif;line-height:1.6}
.wrap{max-width:960px;margin:0 auto;padding:20px 14px 80px}
h1{font-size:22px;margin:6px 0 10px;color:#111827}
blockquote{margin:0 0 14px;padding:10px 12px;border-left:3px solid var(--accent);background:#eef2f7;
 color:#57606a;font-size:13px;border-radius:0 8px 8px 0}
.digest{margin:0 0 18px;padding:12px 14px;border-left:3px solid #6741d9;background:#f3f1fa;color:#374151;
 font-size:13.5px;border-radius:0 8px 8px 0}
.digest .f{margin:8px 0}.digest b{color:#3d2e7c}
.nav{display:flex;flex-wrap:wrap;gap:4px;padding:6px 8px;margin:0 0 18px;border-bottom:1px solid var(--line)}
.nav a{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11.5px}
h2{font-size:17px;margin:26px 0 10px;padding-left:9px;border-left:4px solid;color:#111827}
ul{margin:0}
li{border-radius:10px;padding:10px 12px;margin-bottom:8px;transition:background .15s,border-color .15s}
li:hover{background:#f1f3f4;border-color:#dadce0}
li .h .n{display:none}
li .h{font-size:16.5px;margin-bottom:4px}
li .s{font-size:15px;color:#374151}
li .s strong{background:linear-gradient(transparent 62%,#fff3bf 62%)}
li .m{font-size:13px;margin-top:5px}
footer{margin-top:28px;font-size:12px}
@media(max-width:480px){h1{font-size:20px}.nav{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch}
 li .h{font-size:17.5px}li .s{font-size:16.5px;line-height:1.75}li .m{font-size:14px}
 .digest{font-size:15.5px}blockquote{font-size:14px}}
"""

NAV_HIDE_JS = """
(function(){
 var n=document.querySelector('.nav');if(!n)return;
 var last=-1,raf=0,timer=0;
 function show(){n.classList.remove('hide');}
 function hide(){n.classList.add('hide');}
 function upd(){
  var y=window.pageYOffset||0;
  if(y===last)return;
  var up=y<last;last=y;
  if(y<=60){clearTimeout(timer);show();return;}
  if(up){show();clearTimeout(timer);
   timer=setTimeout(function(){if((window.pageYOffset||0)>60)hide();},1900);
  }else{clearTimeout(timer);hide();}
 }
 upd();
 window.addEventListener('scroll',function(){
  if(raf)return;
  raf=requestAnimationFrame(function(){raf=0;upd();});
 },{passive:true});
 window.addEventListener('touchend',function(){if((window.pageYOffset||0)<=60)show();},{passive:true});
})();
"""


# ── 解析 ────────────────────────────────────────────────────────────────
def parse(lines):
    """→ {title, quotes[], digest[], sections[{head, kind, items[], focus[]}]}"""
    doc = {"title": "", "quotes": [], "digest": [], "sections": []}
    cur = None
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if not doc["title"] and s.startswith("# "):
            doc["title"] = s[2:].strip()
            continue
        if s.startswith(">"):
            q = s.lstrip(">").strip()
            doc["quotes"].append(q)
            continue
        if s in ("---", "***", "___"):
            continue
        if "分类导航" in s and "[[" in s:
            continue
        if re.match(r"^共\s*\d+\s*条\s*[·・]", s):
            continue
        m = SECTION.match(s)
        if m:
            h = m.group(1)
            if any(k in h for k in ("今日导读", "目录")):
                cur = None
                continue
            if "当日焦点" in h or "当日总结" in h or "当日风向小结" in h:
                cur = {"head": h, "kind": "focus", "items": [], "focus": []}
            else:
                cur = {"head": h, "kind": "items", "items": [], "focus": []}
            doc["sections"].append(cur)
            continue
        for rx in (ITEM_LINK, ITEM_PLAIN):
            m = rx.match(s)
            if m and cur is not None:
                cur["items"].append([int(m.group(1)), m.group(2),
                                     m.group(3) if rx is ITEM_LINK else None, m.group(4)])
                break
        else:
            m = None
            for rx in (TITLE_LINK, TITLE_PLAIN):
                m = rx.match(s)
                if m and cur is not None:
                    cur["items"].append([int(m.group(1)), m.group(2),
                                         m.group(3) if rx is TITLE_LINK else None, ""])
                    break
            if m and cur is not None:
                continue
            if cur is not None and cur["items"]:
                it = cur["items"][-1]
                it[3] = (it[3] + " " + s).strip() if it[3] else s
                continue
            if cur is not None and cur["kind"] == "focus":
                cur["focus"].append(s)
    # 把「> 当日总结 / 当日总结」连续块抽成 digest，并从 quotes 里剔除
    if doc["quotes"]:
        keep, buf, in_digest = [], [], False
        for q in doc["quotes"]:
            head = ("当日总结" in q or "当日风向小结" in q) and len(q) < 40 and not q.startswith("时间")
            if head:
                in_digest = True
                buf = [q]
                continue
            if in_digest:
                if q.startswith("时间") or q.startswith("来源") or q.startswith("说明"):
                    in_digest = False
                    keep.append(q)
                else:
                    buf.append(q)
                continue
            keep.append(q)
        if buf:
            doc["digest"] = buf
            doc["quotes"] = keep
    return doc


# ── 渲染辅助 ────────────────────────────────────────────────────────────
def sec_id(head):
    m = re.match(r"^([一二三四五六七八九十]+)、", head)
    if m:
        return "sec-" + str(CN_NUM.index(m.group(1)) + 1)
    return "sec-focus"


def short_name(head, theme):
    raw = re.sub(r"[（(]\d+\s*条[）)]\s*$", "", head).strip()
    tb = theme.get("short_names") or {}
    if raw in tb:
        return tb[raw]
    return re.sub(r"^[一二三四五六七八九十]+、", "", raw).strip()


def inline(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))


def split_meta(meta):
    srcs, times = [], []
    for b in [x.strip() for x in re.split(r"\s*、\s*", meta or "") if x.strip()]:
        m = SRC_BLOCK.match(b)
        name, tm = (m.group(1).strip(), m.group(2)) if m else (b, None)
        if tm:
            times.append(tm)
        lm = LINK_ONE.match(name)
        if lm:
            srcs.append((lm.group(1), lm.group(2)))
        elif name:
            srcs.append((name, None))
    return srcs, times


def tkey(t):
    m = re.search(r"(?:(\d{4})-)?(\d{1,2})[-–](\d{1,2})\s+(\d{1,2}):(\d{2})", t or "")
    return (int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))) if m else (99, 99, 99, 99)


def short_time(t):
    m = re.search(r"(?:(\d{4})-)?(\d{1,2})[-–](\d{1,2})\s+(\d{1,2}:\d{2})", t or "")
    return f"{int(m.group(2)):02d}-{int(m.group(3)):02d} {m.group(4)}" if m else (t or "")


def sans_css():
    if os.environ.get("DS_SANS") != "1" or not os.path.exists(FONT_PATH):
        return ""
    import base64
    b64 = base64.b64encode(open(FONT_PATH, "rb").read()).decode()
    return ("@font-face{font-family:'Google Sans';font-style:normal;font-weight:400 700;"
            "font-display:swap;src:url(data:font/woff2;base64," + b64 + ") format('woff2')}"
            "body{font-family:'Google Sans',-apple-system,BlinkMacSystemFont,"
            "\"PingFang SC\",\"Noto Sans CJK SC\",\"Microsoft YaHei\",sans-serif}")


def darken(hx, n=0.72):
    hx = hx.lstrip("#")
    r, g, b = (int(hx[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * n), int(g * n), int(b * n))


def render(doc, theme):
    total = sum(len(c["items"]) for c in doc["sections"]) or 1
    css = (CSS_COMMON.replace("{nav_bg}", theme["nav_bg"])
           + (CSS_TECH if theme["name"] == "tech" else CSS_AI))
    o = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{html.escape(doc['title'])}</title>",
         f"<style>{sans_css()}{css}</style></head><body><div class='wrap'>"]
    if theme["hero_quotes"]:
        o.append(f"<h1>{html.escape(doc['title'])}</h1>")
        for q in doc["quotes"]:
            o.append(f"<blockquote>{inline(q)}</blockquote>")
    else:
        o.append("<div class='hero'>")
        o.append(f"<h1>{html.escape(doc['title'])}</h1>")
        o.append("</div>")
    if doc["digest"] and theme["digest_card"]:
        parts = [inline(t) for t in doc["digest"] if t]
        head = re.sub(r"<[^>]+>", "", parts[0]).lstrip("- ")
        o.append("<blockquote class='digest'><b>" + head + "</b>"
                 + "".join("<div class='f'>" + re.sub(r"^\s*-\s*", "• ", p) + "</div>" for p in parts[1:])
                 + "</blockquote>")
    o.append("<nav class='nav'>")
    for c in doc["sections"]:
        label = html.escape(short_name(c["head"], theme))
        cnt = "" if c["kind"] == "focus" else f"<i>{len(c['items'])}</i>"
        if theme["nav_style"] == "vertical":
            o.append(f"<a href='#{sec_id(c['head'])}'>{label}{cnt}</a>")
        else:
            o.append(f"<a href='#{sec_id(c['head'])}'>{label} {len(c['items'])}</a>")
    o.append("</nav>")
    for i, c in enumerate(doc["sections"]):
        col = theme["palette"][i % len(theme["palette"])]
        n = len(c["items"])
        head_txt = c["head"]
        m = re.match(r"^(.*?)[（(](\d+)\s*条[）)]$", head_txt)
        if m:
            head_txt = (f"{m.group(1)}<span class='cnt'>{n} 条</span>"
                        if theme["name"] == "ai" else f"{m.group(1)}（{n} 条）")
        style = (f"border-image:linear-gradient(180deg,{col},{darken(col)}) 1"
                 if theme["head_style"] == "gradient" else f"border-color:{col}")
        o.append(f"<h2 id='{sec_id(c['head'])}' style='{style}'>{head_txt}</h2>")
        if c["kind"] == "focus":
            o.append("<div class='focus'>")
            for para in c["focus"]:
                o.append(f"<p style='margin:0'>{inline(para)}</p>")
            o.append("</div>")
            continue
        o.append("<ul>")
        for num, t_, url, body in c["items"]:
            mm = META.search(body)
            meta = (mm.group(1) + (" " + mm.group(2) if mm.group(2) else "")) if mm else ""
            txt = body[: mm.start()] if mm else body
            srcs, times = split_meta(meta)
            head = (f"<a href='{html.escape(url)}' target='_blank' rel='noopener'>{html.escape(t_)}</a>"
                    if url else html.escape(t_))
            chips = []
            for name, u in srcs:
                if u:
                    inner = (f"<span class='hn'>{html.escape(name)}</span>"
                             if name.startswith("Hacker News") else html.escape(name))
                    chips.append(f"<a class='tag' href='{html.escape(u)}' target='_blank' rel='noopener'>{inner}</a>")
                else:
                    chips.append(f"<span class='tag'>{html.escape(name)}</span>")
            m_html = "<span class='dot'>·</span>".join(chips)
            if times:
                if theme["name"] == "tech" and m_html:
                    m_html += "<span class='dot'>·</span>"
                m_html += f"<span class='time'>{html.escape(short_time(min(times, key=tkey)))}</span>"
            o.append(f"<li><div class='h'><span class='n'>{num}.</span> {head}</div>"
                     f"<div class='s'>{inline(txt.strip())}</div>"
                     f"<div class='m'>{m_html}</div></li>")
        o.append("</ul>")
    o.append("<footer>")
    if theme["name"] == "tech":
        o.append(theme["footer_note"].format(total=total))
    else:
        for q in doc["quotes"]:
            o.append(f"<blockquote>{inline(q)}</blockquote>")
        o.append(theme["footer_note"].format(total=total))
    o.append(f"</footer><script>{NAV_HIDE_JS}</script></div></body></html>")
    return "".join(o)


def convert(src, dst=None, theme_name="ai"):
    dst = dst or src.rsplit(".", 1)[0] + ".html"
    theme = THEME_TECH if theme_name == "tech" else THEME_AI
    doc = parse(open(src, encoding="utf-8").read().split("\n"))
    total = sum(len(c["items"]) for c in doc["sections"])
    open(dst, "w", encoding="utf-8").write(render(doc, theme))
    return total, dst


def main():
    argv = sys.argv[1:]
    theme = "ai"
    if "--theme" in argv:
        i = argv.index("--theme")
        theme = argv[i + 1] if i + 1 < len(argv) else "ai"
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    total, dst = convert(args[0], args[1] if len(args) > 1 else None, theme)
    print(f"OK {total} 条 -> {dst}")


if __name__ == "__main__":
    main()