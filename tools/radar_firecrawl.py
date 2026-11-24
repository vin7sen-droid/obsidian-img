#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar_firecrawl.py —— 用 Firecrawl 替代 ai-news-radar CLI 抓取 28 个盲区源
直接替代 radar.py 中的 run_radar() 部分，产出同格式的 radar_added.json
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.path.insert(0, "/workspace/tools")
from firecrawl import search_and_scrape, search_only

MISSING_SOURCES = [
    ("site:theinformation.com AI OR artificial intelligence", 6, "The Information"),
    ("site:bloomberg.com technology AI", 6, "Bloomberg Technology"),
    ("site:ft.com technology artificial intelligence", 6, "FT Technology"),
    ("site:wsj.com tech artificial intelligence", 6, "WSJ Tech"),
    ("site:nytimes.com technology artificial-intelligence", 6, "NYT Technology"),
    ("site:cnbc.com technology artificial-intelligence", 6, "CNBC Tech"),
    ("site:axios.com artificial intelligence", 5, "Axios"),
    ("site:zdnet.com artificial intelligence", 5, "ZDNet AI"),
    ("site:venturebeat.com ai", 6, "VentureBeat AI"),
    ("site:ieee.org artificial intelligence", 5, "IEEE Spectrum"),
    ("site:theregister.com artificial intelligence", 5, "The Register"),
    ("site:wired.com business ai", 5, "Wired Business"),
    ("site:cnbeta.com.tw AI OR 人工智能", 8, "cnBeta"),
    ("site:tmtpost.com 人工智能", 8, "钛媒体"),
    ("site:geekpark.net AI OR 人工智能", 6, "极客公园"),
    ("site:zhidx.com AI OR 人工智能", 6, "智东西"),
    ("site:36kr.com AI OR 人工智能", 8, "36氪"),
    ("site:sspai.com AI OR 人工智能", 6, "少数派"),
    ("site:sina.com.cn tech AI", 6, "新浪科技"),
    ("site:anthropic.com news OR research", 5, "Anthropic"),
    ("site:research.google blog AI", 5, "Google Research"),
    ("site:aws.amazon.com blogs machine-learning", 5, "AWS ML"),
    ("site:engineering.fb.com AI OR ml", 5, "Meta Engineering"),
    ("site:developer.nvidia.com blog AI", 5, "NVIDIA Developer"),
    ("site:semianalysis.com", 4, "SemiAnalysis"),
    ("site:stratechery.com AI", 4, "Stratechery"),
    ("site:crunchbase.com artificial intelligence", 5, "Crunchbase"),
    ("site:techcrunch.com venture ai", 5, "TechCrunch Venture"),
    ("site:smol.ai", 4, "smol.ai"),
]

OUR_HOSTS = (
    "theverge.com", "techcrunch.com", "the-decoder.com", "engadget.com", "wired.com",
    "arstechnica.com", "qbitai.com", "leiphone.com", "infoq.cn", "weixin.qq.com",
    "technews.tw", "ithome.com", "openai.com", "deepmind.google", "blog.google",
    "huggingface.co", "blogs.nvidia.com", "qwenlm.github.io", "techmeme.com",
    "importai.substack.com", "interconnects.ai", "latent.space", "oneusefulthing.org",
    "sebastianraschka.com", "simonwillison.net", "lilianweng.github.io",
    "technologyreview.com", "tldr.tech"
)

JUNK = r"Deals:|Quordle|Wordle|Crossword|今日值买|喜加一|拼团|优惠券|抽奖|折扣|限时特惠|促销|秒杀|wallpapers|giveaway"

# 付费墙源：只搜不抓正文（省 scrape credit，只拿标题/链接/摘要）
PAYWALL_SOURCES = {
    "The Information",
    "Bloomberg Technology",
    "FT Technology",
    "WSJ Tech",
    "NYT Technology",
    "CNBC Tech",
    "Axios",
    "ZDNet AI",
}

def norm(s):
    return re.sub(r"\W", "", s or "")

def is_ours(it):
    name = (it.get("source") or it.get("media") or it.get("src") or "").lower()
    url = (it.get("url") or "").lower()
    if any(o in name for o in OURS_NAME):
        return True
    return any(h in url for h in OUR_HOSTS)

OURS_NAME = (
    "the verge", "techcrunch", "the decoder", "engadget", "wired", "ars technica",
    "量子位", "雷峰网", "infoq", "新智元", "机器之心", "微软研究院",
    "technews", "ithome", "it之家", "openai", "google deepmind", "google ai",
    "google research", "hugging face", "nvidia", "qwen", "techmeme",
    "import ai", "interconnects", "latent space", "one useful thing",
    "raschka", "simon willison", "lilian weng", "mit technology review",
    "rundown", "ai radar"
)

def clamp_since(s):
    now = datetime.now()
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return (dt.replace(year=now.year - 1) if dt > now else dt).timestamp()
        except ValueError:
            pass
    try:
        dt = datetime.strptime("%d-%s" % (now.year, s), "%Y-%m-%d %H:%M")
    except ValueError:
        dt = datetime.strptime("%d-%s" % (now.year, s), "%Y-%m-%d")
    return (dt.replace(year=now.year - 1) if dt > now else dt).timestamp()

def _extract_title(markdown: str, url: str) -> str:
    """从 markdown 中提取更像标题的行"""
    lines = [l.strip() for l in markdown.split("\n") if l.strip()]
    # 跳过导航/跳转链接/图片/空行/版权/cookie/登录/市场数据等样板
    skip_prefixes = (
        "[!", "[skip", "[accessibility", "jump to", "skip to", "manage your", "![]( ",
        "跳过", "导航", "辅助功能", "无障碍", "跳至",
        "cookie", "cookies", "we use", "privacy", "terms", "登录", "注册", "登入",
        "partner content", "sponsored", "advertisement", "广告", "合作内容",
        "market", "quote", "index", "%]", "what to read", "page start", "user topics",
        "ft guides", "registered user", "nature.com", "browser v", "citation",
        "obamawhitehouse", "hpe.com", "partners.wsj", "professional.bloomberg",
        "prod.cm.bloomberg", "ieee.org", "ieee xplore", "ieee.conf",
    )
    skip_contains = (
        "cookie", "privacy policy", "terms of", "all rights", "copyright",
        "subscribe", "newsletter", "sign up", "log in", "sign in",
        "market data", "stock quote", "price chart",
    )
    for l in lines:
        low = l.lower()
        if any(low.startswith(p) for p in skip_prefixes):
            continue
        if any(p in low for p in skip_contains):
            continue
        # 过滤纯链接行、极短行、纯符号行
        if len(l) < 15:
            continue
        if low.startswith("http") or low.startswith("www."):
            continue
        if re.match(r"^[\d\W]+$", l):  # 纯数字/符号
            continue
        # 看起来像标题：有字母/汉字，不是纯导航
        if re.search(r"[a-zA-Z\u4e00-\u9fa5]", l):
            return l[:120]
    # 兜底：用 URL 最后一段
    return url.split("/")[-1].replace("-", " ").replace("_", " ")[:80]

def fetch_one_source(query, limit, src_name, since_ts, until_ts):
    try:
        # 付费墙源：只搜不抓正文（省 scrape credit）
        is_paywall = src_name in PAYWALL_SOURCES
        if is_paywall:
            results = search_only(query, limit=limit)
            out = []
            for url, item in results.items():
                title = item.get("title", "") or _extract_title(item.get("description", ""), url)
                if not title or len(title) < 10:
                    continue
                if re.search(JUNK, title, re.I):
                    continue
                out.append({
                    "src": src_name,
                    "title": title,
                    "url": url,
                    "date": datetime.now().strftime("%m-%d %H:%M"),
                    "ts": time.time(),
                    "desc": item.get("description", "")[:500],
                    "via": "radar-firecrawl:search_only",
                })
            return out
        else:
            # 普通源：搜索+抓正文
            results = search_and_scrape(query, limit=limit, chars=1200)
            out = []
            for url, r in results.items():
                if not r.get("ok"):
                    continue
                md = r.get("text", "")
                title = _extract_title(md, url)
                if not title or len(title) < 10:
                    continue
                if re.search(JUNK, title, re.I):
                    continue
                out.append({
                    "src": src_name,
                    "title": title,
                    "url": url,
                    "date": datetime.now().strftime("%m-%d %H:%M"),
                    "ts": time.time(),
                    "desc": md[:500],
                    "via": "radar-firecrawl",
                })
            return out
    except Exception as e:
        print(f"  [{src_name}] 抓取失败: {e}")
        return []

def run_firecrawl_radar(since_ts, until_ts, workers=6, max_per_source=8):
    print(f"Firecrawl 雷达：28 源并发抓取（workers={workers}, limit={max_per_source}/源）…")
    t0 = time.time()
    queries = [(q, min(max_per_source, lim), name) for q, lim, name in MISSING_SOURCES]
    all_items = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_one_source, q, lim, name, since_ts, until_ts)
                   for q, lim, name in queries]
        for fut in futures:
            items = fut.result()
            all_items.extend(items)
            print(f"  累计 {len(all_items)} 条")
    print(f"Firecrawl 雷达完成：{len(all_items)} 条原始结果，耗时 {time.time()-t0:.1f}s")
    return all_items

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Firecrawl 雷达盲区补充（替代 node radar CLI）")
    parser.add_argument("--dir", required=True, help="工作目录（含 raw.json）")
    parser.add_argument("--since", help="起始时间，如 11-12 18:00")
    parser.add_argument("--hours", type=float, help="回溯小时数")
    parser.add_argument("--days", type=int, default=1, help="回溯天数")
    parser.add_argument("--workers", type=int, default=6, help="并发数")
    parser.add_argument("--limit", type=int, default=8, help="单源上限")
    parser.add_argument("--dry", action="store_true", help="仅预览不写盘")
    parser.add_argument("--out", default="radar_added.json", help="输出文件名")
    # 兼容 daily.py 传入的旧参数（忽略）
    parser.add_argument("--max-age", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--reuse", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-news", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-bing", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-x", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-hn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-reddit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--x", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reddit", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    d = os.path.abspath(args.dir)
    raw_path = os.path.join(d, "raw.json")
    if not os.path.exists(raw_path):
        sys.exit("缺 raw.json，先跑 fetch.py：" + raw_path)

    raw = json.load(open(raw_path, encoding="utf-8"))
    urls = {e["url"] for e in raw}
    tks = {norm(e["title"])[:18] for e in raw}

    now = datetime.now()
    if args.hours:
        since_ts = (now - timedelta(hours=args.hours)).timestamp()
    elif args.since:
        since_ts = clamp_since(args.since)
    else:
        since_ts = (now - timedelta(days=args.days)).replace(hour=0, minute=0, second=0).timestamp()
    until_ts = now.timestamp() + 3600

    items = run_firecrawl_radar(since_ts, until_ts, workers=args.workers, max_per_source=args.limit)

    added, pending_gn = [], []
    skip = {"gnews": 0, "time": 0, "dup": 0, "junk": 0, "bad": 0, "ours": 0}

    for it in items:
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        if not url or not title:
            skip["bad"] += 1; continue
        ts = it.get("ts") or 0
        if not (since_ts <= ts <= until_ts):
            skip["time"] += 1; continue
        if "news.google.com" in url:
            pending_gn.append(it)
            skip["gnews"] += 1; continue
        if is_ours(it):
            skip["ours"] += 1; continue
        k = norm(title)[:18]
        if url in urls or k in tks:
            skip["dup"] += 1; continue
        urls.add(url); tks.add(k)
        added.append(it)

    print(f"剔除: {skip} | 新增 {len(added)}")

    if pending_gn:
        gp = [{"title": it["title"], "source": it["src"], "theme": "",
               "time": datetime.fromtimestamp(it["ts"]).strftime("%Y-%m-%d %H:%M"),
               "url": it["url"]} for it in pending_gn]
        with open(os.path.join(d, "gnews_pool.json"), "w", encoding="utf-8") as f:
            json.dump(gp, f, ensure_ascii=False, indent=1)
        print(f"Google News 线索池 gnews_pool.json {len(gp)} 条")

    if not added:
        print("无新增")
        return

    out_path = os.path.join(d, args.out)
    json.dump(added, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"→ {out_path}")

    if args.dry:
        for e in added[:30]:
            print(f"  {e['date']} | {e['src'][:20]} | {e['title'][:60]}")

if __name__ == "__main__":
    main()
