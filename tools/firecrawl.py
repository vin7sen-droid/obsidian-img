#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
firecrawl.py —— Firecrawl v2 REST API 统一封装
给 enrich.py / radar.py / gnews_url.py 等调用，零配置、自动兜底语义。
真源：/workspace/tools/firecrawl.py（所有管道共用）

2026-11-24 新增：当日去重缓存（firecrawl_cache.py），避免三条线重复抓同一 URL 浪费 credits
"""
import os
import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

# Firecrawl 当日去重缓存
from firecrawl_cache import is_scraped_today, mark_scraped_today

# ============ 配置 ============
KEY = os.environ.get("FIRECRAWL_API_KEY") or "fc-d45265f3e918497e95da52d28617ee4f"
BASE = "https://api.firecrawl.dev/v2"
DEFAULT_TIMEOUT = 30
DEFAULT_CHARS = 900
MIN_OK_CHARS = 600

# ============ 错误类型 ============
class FirecrawlError(Exception):
    """统一错误码，上层按 code 决定是否兜底"""
    def __init__(self, code: str, msg: str, http_status: Optional[int] = None):
        self.code = code          # INSUFFICIENT_CREDITS, RATE_LIMITED, API_ERROR, NETWORK, UNKNOWN
        self.msg = msg
        self.http_status = http_status
        super().__init__(f"[{code}] {msg}")

# ============ 内部请求 ============
def _post(path: str, payload: Dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            code = err.get("code", f"HTTP_{e.code}")
            msg = err.get("error", body)
        except Exception:
            code, msg = f"HTTP_{e.code}", body
        # 关键映射：让上层能识别「点数用尽 / 限流」
        if e.code == 402 or code == "INSUFFICIENT_CREDITS":
            raise FirecrawlError("INSUFFICIENT_CREDITS", msg, 402)
        if e.code == 429:
            raise FirecrawlError("RATE_LIMITED", msg, 429)
        raise FirecrawlError("API_ERROR", msg, e.code)
    except Exception as e:
        raise FirecrawlError("NETWORK", str(e))

# ============ 统一返回结构 ============
def _ok_result(markdown: str, chars: int, engine: str, t0: float) -> Dict[str, Any]:
    lang = "zh" if any("\u4e00" <= c <= "\u9fa5" for c in markdown[:200]) else "en"
    return {
        "ok": len(markdown) >= MIN_OK_CHARS,
        "chars": len(markdown),
        "text": markdown[:chars],
        "lang": lang,
        "sec": round(time.time() - t0, 1),
        "engine": engine,
    }

def _err_result(error_code: str, error_msg: str, engine: str, t0: float) -> Dict[str, Any]:
    return {
        "ok": False,
        "chars": 0,
        "text": "",
        "lang": "",
        "sec": round(time.time() - t0, 1),
        "engine": engine,
        "error_code": error_code,
        "error_msg": error_msg,
    }

# ============ 对外接口 ============
def scrape(url: str, chars: int = DEFAULT_CHARS, timeout: int = DEFAULT_TIMEOUT,
           skip_if_cached: bool = True) -> Dict[str, Any]:
    """
    单页深度抓取
    返回统一结构：{ok, chars, text, lang, sec, engine, error_code?, error_msg?}
    skip_if_cached=True 时，今日已抓过的 URL 直接返回缓存命中，不消耗 credits
    """
    # ---- 当日去重缓存检查 ----
    if skip_if_cached and is_scraped_today(url):
        return {
            "ok": False, "chars": 0, "text": "", "lang": "",
            "sec": 0, "engine": "firecrawl:cached",
            "error_code": "ALREADY_SCRAPED_TODAY", "error_msg": "Today cache hit"
        }
    t0 = time.time()
    try:
        data = _post("/scrape", {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }, timeout=timeout)
        d = data.get("data", {})
        md = d.get("markdown", "")
        result = _ok_result(md, chars, "firecrawl:scrape", t0)
        if result.get("ok"):
            mark_scraped_today(url)
        return result
    except FirecrawlError as e:
        return _err_result(e.code, e.msg, "firecrawl:scrape", t0)
    except Exception as e:
        return _err_result("UNKNOWN", str(e), "firecrawl:scrape", t0)


def search_and_scrape(query: str, limit: int = 10, chars: int = DEFAULT_CHARS,
                      timeout: int = DEFAULT_TIMEOUT,
                      skip_if_cached: bool = True) -> Dict[str, Dict[str, Any]]:
    """
    搜索 + 一次性抓正文（最省 credit）
    返回 {url: {ok, chars, text, lang, sec, engine, error_code?}}
    skip_if_cached=True 时，今日已抓过的 URL 直接标记缓存命中，不消耗额外 credits
    """
    t0 = time.time()
    try:
        data = _post("/search", {
            "query": query,
            "limit": limit,
            "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
        }, timeout=timeout)
        out = {}
        for item in data.get("data", {}).get("web", []):
            url = item["url"]
            md = item.get("markdown", "")
            if skip_if_cached and is_scraped_today(url):
                out[url] = {
                    "ok": False, "chars": 0, "text": "", "lang": "",
                    "sec": 0, "engine": "firecrawl:cached",
                    "error_code": "ALREADY_SCRAPED_TODAY", "error_msg": "Today cache hit"
                }
                continue
            res = _ok_result(md, chars, "firecrawl:search", t0)
            if res.get("ok"):
                mark_scraped_today(url)
            out[url] = res
        return out
    except FirecrawlError as e:
        err = _err_result(e.code, e.msg, "firecrawl:search", t0)
        return {url: err for url in []}
    except Exception as e:
        err = _err_result("UNKNOWN", str(e), "firecrawl:search", t0)
        return {url: err for url in []}


def search_only(query: str, limit: int = 10, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Dict[str, Any]]:
    """
    仅搜索，不抓取正文（不消耗 scrape credit，仅搜索 credit）
    返回 {url: {title, url, description, source, ...}} —— 原始搜索结果
    适用于付费墙源：只拿标题/链接/摘要，不抓正文
    """
    t0 = time.time()
    try:
        data = _post("/search", {
            "query": query,
            "limit": limit,
            # 不带 scrapeOptions，只做搜索
        }, timeout=timeout)
        out = {}
        for item in data.get("data", {}).get("web", []):
            url = item["url"]
            out[url] = {
                "title": item.get("title", ""),
                "url": url,
                "description": item.get("description", ""),
                "source": item.get("source", ""),
                "engine": "firecrawl:search_only",
                "sec": round(time.time() - t0, 1),
            }
        return out
    except FirecrawlError as e:
        return {}
    except Exception as e:
        return {}


def crawl_start(url: str, limit: int = 10, chars: int = DEFAULT_CHARS,
                timeout: int = DEFAULT_TIMEOUT) -> str:
    """启动异步爬虫，返回 crawl_id"""
    data = _post("/crawl", {
        "url": url,
        "limit": limit,
        "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
    }, timeout=timeout)
    return data["id"]


def crawl_status(crawl_id: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """轮询爬虫状态，完成时返回 {success, status, data[], creditsUsed, ...}"""
    req = urllib.request.Request(
        f"{BASE}/crawl/{crawl_id}",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ============ 便捷批量 ============
def batch_scrape(urls: List[str], chars: int = DEFAULT_CHARS,
                 timeout: int = DEFAULT_TIMEOUT,
                 skip_if_cached: bool = True) -> Dict[str, Dict[str, Any]]:
    """并发批量 scrape（上层自行控制并发，建议 workers=6-10）
    skip_if_cached=True 时自动跳过今日已抓 URL"""
    from concurrent.futures import ThreadPoolExecutor
    # 先过滤掉已缓存的
    to_fetch = [u for u in urls if not (skip_if_cached and is_scraped_today(u))]
    cached = {u: {
        "ok": False, "chars": 0, "text": "", "lang": "",
        "sec": 0, "engine": "firecrawl:cached",
        "error_code": "ALREADY_SCRAPED_TODAY", "error_msg": "Today cache hit"
    } for u in urls if u not in to_fetch}
    out = dict(cached)
    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(10, len(to_fetch))) as ex:
            for url, res in zip(to_fetch, ex.map(lambda u: scrape(u, chars, timeout, skip_if_cached=False), to_fetch)):
                out[url] = res
    return out


# ============ CLI 自测 ============
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 firecrawl.py <url> [chars]")
        sys.exit(1)
    url = sys.argv[1]
    chars = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CHARS
    r = scrape(url, chars)
    print(json.dumps(r, ensure_ascii=False, indent=2))