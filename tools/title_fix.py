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
