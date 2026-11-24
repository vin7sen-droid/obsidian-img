#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google News 跳转链 → 原站真实 URL（全机器通用，任何会话可直接调）

背景：Google News RSS 的 <link> 是 news.google.com/rss/articles/CBMi… 跳转链，
      2026-11 起新格式（AU_yq…）连 base64 都解不出原文。服务端两步法可解：
      ① /rss/articles/<id>?hl=&gl=&ceid= 页面里取 data-n-a-sg / data-n-a-ts
      ② POST /_/DotsSplashUi/data/batchexecute（f.req=[[["Fbv4je",…]]]）拿 garturlres
      实现用 PyPI 包 googlenewsdecoder（0.2.2，已全局装在
      /opt/python/lib/python3.12/site-packages，无需再 pip install）。

用法
    python3 gnews_url.py <google-news-url> [<url> ...]
    python3 gnews_url.py -f urls.txt                 # 每行一条
    python3 gnews_url.py --json digests/2026-11-13.json   # 从雷达 JSON 提全部 gnews 链
    python3 gnews_url.py --json digests/2026-11-13.json --theme model --limit 20
                                                        # 只解某主题（chips/people/funding/
                                                        # model/policy/biz/tech/other）
    python3 gnews_url.py --json digests/2026-11-13.json --match "英伟达|NVIDIA"
                                                        # 只解标题/摘要/来源命中正则的条目
    python3 gnews_url.py -f urls.txt --out map.json  # 结果落盘 {原链: 真实链}
    选项：--workers N（默认 4，建议 2-3 防限流）--quiet（只输出真实链，便于管道）

输出
    默认：原链 → 真实链（失败打印 ⨯ 原因）
    --quiet：每条一行真实链，失败跳过（直接喂 while read）
    --out：另存 JSON 映射 + 失败列表

性能（本机实测 2026-11-13）
    单条约 5–12 秒（两次 HTTPS 往返，news.google.com 国内较慢），
    workers=6 时 40 条约 63 秒、成功率 97%；但 100 条同并发只成功 57%
    （Google 限流）——批量请降到 --workers 2~3，或先用 --theme/--match 只解需要的条目。
    失败重试一次通常能过；/articles/ 路径会被 302 到 google.com/sorry，
    所以必须走 /rss/articles/ 路径（脚本内已处理）。

注意
    仅在能访问 news.google.com 的网络下可用；解码结果是发布方原站 URL，
    个别站点（FT/WSJ/彭博）会有付费墙，链接本身有效。
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from googlenewsdecoder import gnewsdecoder

# Firecrawl 集成（2026-11-24）
sys.path.insert(0, "/workspace/tools")
try:
    from firecrawl import scrape as fc_scrape
    _HAS_FIRECRAWL = True
except Exception:
    _HAS_FIRECRAWL = False

GN_MARK = "news.google.com"


def is_gn(u):
    return isinstance(u, str) and GN_MARK in u


def harvest(obj, keep=None, depth=0):
    """递归收集 JSON 里的 Google News 链接（去重保序）。

    keep: 可选判定函数。对含 url 的条目对象，若 keep(条目) 为 True 才收集它
          内部的 gnews 链（这样能按主题/关键词挑条目，而不是全量解码）。
    """
    out, seen = [], set()

    def take(u):
        if is_gn(u) and u not in seen:
            seen.add(u); out.append(u)

    def walk(o, d=0):
        if isinstance(o, str):
            take(o)
        elif isinstance(o, dict):
            if keep is not None and (o.get("url") or o.get("title")) and d > 0:
                if not keep(o):
                    return
            for v in o.values():
                walk(v, d + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, d + 1)
    walk(obj)
    return out


def main():
    ap = argparse.ArgumentParser(add_help=True, description="Google News 跳转链解码")
    ap.add_argument("urls", nargs="*", help="Google News 链接（可多条）")
    ap.add_argument("-f", "--file", help="每行一条链接的文件")
    ap.add_argument("--json", help="从 JSON 文件里递归找所有 gnews 链接")
    ap.add_argument("--theme", action="append",
                    help="只解该主题的条目（chips/people/funding/model/policy/biz/tech/other，可多次）")
    ap.add_argument("--match", help="只解标题/摘要/来源命中该正则的条目")
    ap.add_argument("--limit", type=int, help="最多解多少条（配合 --theme/--match 抽样）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", help="结果 JSON 落盘路径")
    ap.add_argument("--quiet", action="store_true", help="只输出真实链")
    ap.add_argument("--firecrawl", action="store_true",
                    help="解码后自动用 Firecrawl 抓取正文（需 FIRECRAWL_API_KEY），结果合并进 --out")
    ap.add_argument("--fc-chars", type=int, default=900, help="Firecrawl 正文截断字数")
    a = ap.parse_args()

    urls = list(a.urls)
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            urls += [l.strip() for l in f if l.strip()]
    if a.json:
        keep = None
        if a.theme or a.match:
            themes = set(a.theme or [])
            rx = None
            if a.match:
                rx = re.compile(a.match, re.I)

            def keep(it):
                if themes and (it.get("theme") or "") not in themes:
                    return False
                if rx:
                    hay = " ".join(str(it.get(k) or "") for k in
                                   ("title", "title_zh", "summary", "summary_zh",
                                    "source", "srcId"))
                    if not rx.search(hay):
                        return False
                return True
        with open(a.json, encoding="utf-8") as f:
            got = harvest(json.load(f), keep)
        if a.limit:
            got = got[:a.limit]
        if not a.quiet and keep is not None:
            print(f"按条件筛出 {len(got)} 条 gnews 链", file=sys.stderr)
        urls += got
    urls = [u for u in dict.fromkeys(urls) if is_gn(u)]
    if not urls:
        sys.exit("没有 Google News 链接（需含 news.google.com）")

    if not a.quiet:
        print(f"解码 {len(urls)} 条（workers={a.workers}）…", file=sys.stderr)

    def _pick_proxy():
        """2026-11-24：Google News 直连必超时，自动用环境变量代理或探测本机 mihomo。"""
        import os as _os
        for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
            v = _os.environ.get(k)
            if v:
                return v
        try:
            import socket as _sock
            s2 = _sock.socket(); s2.settimeout(1)
            s2.connect(("127.0.0.1", 7890)); s2.close()
            return "http://127.0.0.1:7890"
        except Exception:
            return None

    def one(u):
        proxy = _pick_proxy()
        for attempt in range(2):
            try:
                r = gnewsdecoder(u, interval=1, proxy=proxy)
                if r.get("success") and r.get("decoded_url"):
                    return u, r["decoded_url"], None
                err = r.get("message") or "no url"
            except Exception as e:  # 网络抖动
                err = f"{type(e).__name__}: {e}"
            if attempt == 0:
                time.sleep(1.5)
        return u, None, err

    t0 = time.time()
    ok, mp, fails = 0, {}, []
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for src, real, err in ex.map(one, urls):
            if real:
                ok += 1; mp[src] = real
                print(real if a.quiet else f"{src}\n  → {real}")
            else:
                fails.append({"url": src, "error": err})
                if not a.quiet:
                    print(f"{src}\n  ⨯ {err}")

    if not a.quiet:
        print(f"\n成功 {ok}/{len(urls)}，失败 {len(fails)}，耗时 {time.time()-t0:.0f}s", file=sys.stderr)

    # Firecrawl 批量抓取正文（--firecrawl 开启时）
    fc_results = {}
    if a.firecrawl and mp:
        if not _HAS_FIRECRAWL:
            print("⚠️ firecrawl 模块不可用，跳过正文抓取", file=sys.stderr)
        else:
            print(f"Firecrawl 抓取正文 {len(mp)} 条（workers=6）…", file=sys.stderr)
            from firecrawl import batch_scrape
            fc_t0 = time.time()
            fc_results = batch_scrape(list(mp.values()), chars=a.fc_chars)
            fc_ok = sum(1 for r in fc_results.values() if r.get("ok"))
            print(f"  成功 {fc_ok}/{len(mp)}，耗时 {time.time()-fc_t0:.0f}s", file=sys.stderr)

    if a.out:
        out_data = {"map": mp, "failed": fails}
        if fc_results:
            out_data["firecrawl"] = fc_results
        json.dump(out_data, open(a.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        if not a.quiet:
            print(f"已写 {a.out}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())