#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enrich.py —— 给派单素材补正文节选（A 层：治「素材太短、写手只能猜」）

背景：派单只给 RSS desc，站点常截断（TechNews 用 […] 收尾、CNBC 只给 2 句）。
实测 a11-17 期 pool 里 1/3 条目 desc < 100 字 —— 写手信息不足就会补因果/评价/归因，
这是 claim_check 抓到的三类越界的根因。

做法：对 prep/candidates.json 里每个候选簇的**主源**抓正文，去标签去样板，取前 N 字，
写 prep/enrich.json；dispatch.py 检测到该文件就在素材里多给一行「正文节选」。

两种输入（自动探测，--from 可强制）：
  prep 模式（AI 日报）：<workdir>/prep/candidates.json 簇结构，每簇有 sources[]，默认取主源
  flat 模式（科技日报）：<workdir>/pool_*.json 或 pack_*.json 扁平列表，每条就是一篇

用法：
  python3 enrich.py <workdir> [--workers 6] [--chars 900] [--top 0] [--all-src]
                   [--from prep|flat|auto] [--input <json>] [--out <json>]
    --top N     只抓 desc 最短的 N 条（0=全抓；短素材才是补料对象）
    --all-src   prep 模式每簇所有源都抓（默认只抓主源，省一半请求）
    --input     显式指定输入 json（如 <workdir>/pool_2026-11-17.json）
    --out       显式指定输出 json（默认 prep 模式→prep/enrich.json，flat→<workdir>/enrich.json）
统计输出：可用 / 太薄 / 失败 / 总耗时 / 正文中位字数。
真源：/root/workspace/tools/enrich.py（news-ai 与 news-tools 共用，改这里）
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
DROP = re.compile(r"(?is)<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>")
# 站点样板截断点：命中即从此处砍掉（避免「相关文章 / 关键词：」这类尾巴污染素材）
CUT = re.compile(r"(投诉水文|我要纠错|下载IT之家APP|相关文章|关键词：|责任编辑：|【本文结束】|"
                 r"Most popular|Next up|Subscribe|Read more|Share this|Sign up for|"
                 r"Follow us|Advertisement|相关阅读|热门新闻|热门文章|延伸閱讀|推薦文章)")
BLOCK = re.compile(r"(?i)</?(p|div|br|li|h[1-6]|tr|section|article)[^>]*>")


def fetch_text(url, chars):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        raw = urllib.request.urlopen(req, timeout=10).read()
        s = None
        for enc in ("utf-8", "gbk", "big5"):
            try:
                s = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if s is None:
            s = raw.decode("utf-8", "replace")
        s = DROP.sub(" ", s)
        s = BLOCK.sub("\n", s)
        txt = html.unescape(re.sub(r"<[^>]+>", " ", s))
        txt = re.sub(r"[ \t\u3000]+", " ", txt)
        txt = re.sub(r"\n\s*\n+", "\n", txt).strip()
        m = CUT.search(txt)
        if m and m.start() > 300:
            txt = txt[:m.start()]
        # 去掉页面头部导航残留：从第一段超过 60 字的行开始
        lines = txt.split("\n")
        start = next((i for i, l in enumerate(lines) if len(l) > 60), 0)
        txt = "\n".join(lines[start:])
        cjk = len(re.findall(r"[\u4e00-\u9fa5]", txt))
        lang = "zh" if cjk > len(txt) * 0.15 else "en"
        return {"ok": len(txt) >= 600, "chars": len(txt), "text": txt[:chars],
                "lang": lang, "sec": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "chars": 0, "text": "", "err": type(e).__name__,
                "sec": round(time.time() - t0, 1)}


def _desc_len(it):
    return len(re.sub(r"<[^>]+>", "", it.get("desc") or ""))


def pick_input(d, force_input=None):
    """探测输入：prep 簇结构 or 扁平 pool/pack。返回 (path, mode)。"""
    if force_input:
        p = force_input if os.path.isabs(force_input) else os.path.join(d, force_input)
        data = json.load(open(p, encoding="utf-8"))
        return p, ("prep" if data and isinstance(data[0], dict) and "sources" in data[0] else "flat")
    cand = os.path.join(d, "prep", "candidates.json")
    if os.path.exists(cand):
        return cand, "prep"
    import glob
    for pat in ("pool_*.json", "pack_*.json", "pool.json", "raw_*.json"):
        hits = sorted(glob.glob(os.path.join(d, pat)))
        if hits:
            return hits[-1], "flat"
    return None, None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    d = sys.argv[1].rstrip("/")
    arg = lambda k, dv=None: (sys.argv[sys.argv.index(k) + 1] if k in sys.argv else dv)
    workers = int(arg("--workers", 6))
    chars = int(arg("--chars", 900))
    top = int(arg("--top", 0))
    all_src = "--all-src" in sys.argv

    path, mode = pick_input(d, arg("--input"))
    if "--from" in sys.argv and sys.argv[sys.argv.index("--from") + 1] in ("prep", "flat"):
        mode = sys.argv[sys.argv.index("--from") + 1]
    if not path:
        print("找不到输入：既无 prep/candidates.json，也无 pool_*.json / pack_*.json")
        return 2

    data = json.load(open(path, encoding="utf-8"))
    targets = []
    if mode == "prep":
        cands = data
        if top:
            cands = sorted(cands, key=lambda c: _desc_len((c.get("sources") or [{}])[0]))[:top]
        for c in cands:
            srcs = c.get("sources") or []
            for s in (srcs if all_src else srcs[:1]):
                u = s.get("url")
                if u and u not in targets:
                    targets.append(u)
        label = "候选 %d 簇" % len(cands)
    else:
        rows = data
        if top:
            rows = sorted(rows, key=_desc_len)[:top]
        for r in rows:
            u = r.get("url")
            if u and u not in targets:
                targets.append(u)
        label = "条目 %d 条" % len(rows)

    print("%s（%s）→ %s · 待抓 %d · 并发 %d · 每篇取 %d 字"
          % (label, mode, os.path.basename(path), len(targets), workers, chars))
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda u: (u, fetch_text(u, chars)), targets))
    store = {u: r for u, r in res}
    ok = [r for _, r in res if r["ok"]]
    thin = [r for _, r in res if not r["ok"] and r["chars"] > 0]
    bad = [r for _, r in res if r["chars"] == 0]
    mid = sorted(r["chars"] for r in ok)[len(ok) // 2] if ok else 0
    out = arg("--out") or (os.path.join(d, "prep", "enrich.json") if mode == "prep"
                           else os.path.join(d, "enrich.json"))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(store, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("可用 %d · 太薄 %d · 失败 %d · 总耗时 %.1fs · 正文中位 %d 字"
          % (len(ok), len(thin), len(bad), time.time() - t0, mid))
    print("→ " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())