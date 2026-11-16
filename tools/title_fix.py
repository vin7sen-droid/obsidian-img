# -*- coding: utf-8 -*-
"""从页面里补标题：JSON-LD headline/name、twitter:title、<title> 逐层兜底。

2026-11-13 加：substack（interconnects.ai、importai.substack.com）的
og:title / <title> 都是空壳，真标题只在 JSON-LD 的 headline 里。
"""

import json
import re

def _jsonld_titles(page):
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            # 有些站把 JSON-LD 写成 JS 对象（带尾逗号/单引号），退化到正则捞
            for k in ("headline", "name"):
                hm = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)+)"' % k, raw)
                if hm:
                    out.append(hm.group(1))
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k in ("headline", "name"):
                    v = node.get(k)
                    if isinstance(v, str) and v.strip():
                        out.append(v)
                stack.extend(node.get("@graph") or [])
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return out


def _unescape(s):
    import html as _h
    return _h.unescape(s).replace("\\/", "/").replace('\\"', '"').strip()


def title_fallback(page, current=None, site_label=None):
    """current 已有非空标题时原样返回；否则按 JSON-LD → twitter:title → <title> 找。"""
    if current and current.strip() and current.strip() != "未命名":
        return current
    cands = [_unescape(t) for t in _jsonld_titles(page)]
    tm = re.search(r'<meta\s+(?:name|property)="twitter:title"\s+content="(.*?)"', page, re.I | re.S)
    if tm:
        cands.append(_unescape(tm.group(1)))
    tm = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    if tm:
        for part in re.split(r"\s+[|·\-–—]\s+", re.sub(r"<[^>]+>", "", tm.group(1))):
            cands.append(_unescape(part))
    for c in cands:
        c = re.sub(r"\s+", " ", c or "").strip()
        if not c or len(c) < 3:
            continue
        if site_label and c.lower() == site_label.lower():
            continue
        if c.lower() in ("untitled", "无标题", "home", "homepage"):
            continue
        return c
    return None
# local drift test
# drift2


# ---------------------------------------------------------------
# 2026-11-17 加：作者与发布时间的多源兜底（defuddle 式投票链）
#
# 实测依据（10 站扫描）：英文站的 author/date 几乎都在 JSON-LD 与 meta 里，
# 而 clip.py 原来只读 meta[name=author] / article:author，JSON-LD 完全没用上
# —— 这是「作者常缺」的主因。中文站（IT之家/快科技/量子位）的 author/date
# 在页面节点里（span#author_baidu 等），meta 里是空的，多源帮不上，仍靠定制。
# ---------------------------------------------------------------

_BAD_AUTHOR = {
    "admin", "administrator", "editor", "author", "unknown", "匿名", "佚名",
    "作者", "主编", "编辑", "责任编辑", "主作者", "联合作者", "本站", "转载",
    "投稿", "原创", "综合", "文/", "图/",
}

def _meta_values(page, keys):
    """取 <meta property|name|itemprop=key content=...> 的所有值（保持出现顺序）。"""
    out = []
    for k in keys:
        for m in re.finditer(
                r'<meta[^>]+(?:property|name|itemprop)=["\']%s["\'][^>]*>' % re.escape(k),
                page, re.I):
            c = re.search(r'content=["\']([^"\']*)', m.group(0))
            if c and c.group(1).strip():
                out.append(_unescape(c.group(1).strip()))
    return out


def _jsonld_people(page):
    """JSON-LD 里的 author / creator 名字（支持对象、数组、@graph）。"""
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            for k in ("author", "creator"):
                for x in re.findall(r'"%s"\s*:\s*\{[^}]*?"name"\s*:\s*"((?:[^"\\]|\\.)+)"' % k, raw):
                    out.append(_unescape(x))
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k in ("author", "creator"):
                    v = node.get(k)
                    if isinstance(v, dict) and isinstance(v.get("name"), str):
                        out.append(_unescape(v["name"]))
                    elif isinstance(v, list):
                        for x in v:
                            if isinstance(x, dict) and isinstance(x.get("name"), str):
                                out.append(_unescape(x["name"]))
                            elif isinstance(x, str) and x.strip():
                                out.append(_unescape(x))
                    elif isinstance(v, str) and v.strip():
                        out.append(_unescape(v))
                stack.extend(node.get("@graph") or [])
                stack.extend(x for x in node.values() if isinstance(x, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return out


def _jsonld_dates(page):
    """JSON-LD 里的 datePublished（优先）与 dateModified（兜底）。"""
    pub, mod = [], []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            for x in re.findall(r'"datePublished"\s*:\s*"([^"]+)"', raw):
                pub.append(x)
            for x in re.findall(r'"dateModified"\s*:\s*"([^"]+)"', raw):
                mod.append(x)
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k, bucket in (("datePublished", pub), ("dateCreated", pub),
                                  ("uploadDate", pub), ("dateModified", mod)):
                    v = node.get(k)
                    if isinstance(v, str) and v.strip():
                        bucket.append(v.strip())
                stack.extend(node.get("@graph") or [])
                stack.extend(x for x in node.values() if isinstance(x, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return pub, mod


def _clean_author(s):
    """过滤明显不是人名/署名的候选。"""
    s = re.sub(r"\s+", " ", (s or "")).strip(" \t|·・,，。:：")
    s = re.sub(r"^(?:by|By|BY|作者[:：]|文[:：]|Posted by)\s*", "", s).strip()
    s = s.lstrip("@")
    if not s or len(s) < 2 or len(s) > 40:
        return ""
    if re.search(r"https?://|www\.|facebook|twitter\.com", s, re.I):
        return ""
    if s.lower() in _BAD_AUTHOR or s in _BAD_AUTHOR:
        return ""
    if re.fullmatch(r"[\d\s\-/.]+", s):
        return ""
    if re.search(r"作者|编辑|责编|主编", s) and len(s) <= 4:
        return ""
    return s


def _iso_date(s):
    """把各种日期串归一成 YYYY-MM-DDTHH:MM:SS（拿不到时间就 T00:00:00）。"""
    s = (s or "").strip()
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return ""
    y, mo, d, hh, mm, ss = m.groups()
    return "%s-%02d-%02dT%02d:%02d:%02d" % (
        y, int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))


def author_fallback(page, current=None):
    """作者多源兜底。current 非空直接返回；候选按可信度排序，取第一个干净值。

    优先级：JSON-LD(author/creator) → meta[author] → meta[article:author]
            → meta[twitter:creator] → rel=author → class*=author 节点文本
    """
    cur = _clean_author(current)
    if cur:
        return cur
    cands = []
    cands += _jsonld_people(page)
    cands += _meta_values(page, ["author", "article:author", "og:article:author", "twitter:creator"])
    for m in re.finditer(r'<a[^>]+rel=["\']author["\'][^>]*>(.*?)</a>', page, re.S | re.I):
        cands.append(re.sub(r"<[^>]+>", "", m.group(1)))
    # schema.org microdata (RDFa)：AWS blog 写成
    # <span property="author" typeof="Person"><span property="name">Nolan Chen</span></span>
    for m in re.finditer(r'<(span|div|a)[^>]+property="author"[^>]*>(.*?)</\1>', page, re.S | re.I):
        inner = m.group(2)
        nm = re.search(r'property="name"[^>]*>([^<]+)<', inner)
        cands.append(nm.group(1) if nm else re.sub(r"<[^>]+>", "", inner))
    for pat in (r'<span[^>]+class=["\'][^"\']*author[^"\']*["\'][^>]*>(.*?)</span>',
                r'<div[^>]+class=["\'][^"\']*author[^"\']*["\'][^>]*>(.*?)</div>',
                r'<p[^>]+class=["\'][^"\']*author[^"\']*["\'][^>]*>(.*?)</p>'):
        for m in re.finditer(pat, page, re.S | re.I):
            cands.append(re.sub(r"<[^>]+>", "", m.group(1)))
    for c in cands:
        v = _clean_author(c)
        if v:
            return v
    return None


def date_fallback(page, current=None):
    """发布时间多源兜底。current 非空直接返回；返回 ISO 字符串或 None。

    优先级：JSON-LD datePublished → meta[article:published_time] → meta[datePublished]
            → meta[date] / pubdate → <time datetime> → JSON-LD dateModified
            → meta[article:modified_time]
    注意：substack 的 article:modified_time 常排在 published_time 之前，
    所以严格按「发布 > 修改」取，不能按出现顺序。
    """
    cur = _iso_date(current) if current else ""
    if cur and cur[:10] != "1970-01-01":
        return cur
    pub, mod = _jsonld_dates(page)
    cands = pub
    cands += _meta_values(page, ["article:published_time", "og:article:published_time",
                                 "datePublished", "publishdate", "pubdate", "date", "dc.date"])
    for m in re.finditer(r'<time[^>]+datetime=["\']([^"\']+)', page, re.I):
        cands.append(m.group(1))
    for c in cands:
        v = _iso_date(c)
        if v:
            return v
    mod += _meta_values(page, ["article:modified_time", "og:updated_time", "lastmod"])
    for c in mod:
        v = _iso_date(c)
        if v:
            return v
    return None
