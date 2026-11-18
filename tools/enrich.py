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
    --proxy URL          全程走该代理（默认 None=直连/跟随环境变量）
    --fallback-proxy URL 仅对「网络层失败」条目用代理兜底重试一次（推荐：直连优先，代理只兜底）
    --retries N          网络类失败重试次数（默认 2；401/403/404/410 一律不重试）
统计输出：可用 / 太薄 / 失败 / 总耗时 / 正文中位字数。
真源：/root/workspace/tools/enrich.py（news-ai 与 news-tools 共用，改这里）
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# Playwright / 云浏览器集成：仅对必须 JS 渲染的站点按需启用
# 优先级：Browserbase（需 REST 建 session）> Browserless（直接 CDP）> urllib
BROWSERBASE_API_KEY = os.environ.get("BROWSERBASE_API_KEY")       # browserbase.com API key
BROWSERBASE_PROJECT = os.environ.get("BROWSERBASE_PROJECT", "")   # projectId（可选）
PLAYWRIGHT_CDP_ENDPOINT = os.environ.get("PLAYWRIGHT_CDP_ENDPOINT")  # 如 wss://chrome.browserless.io?token=xxx
PLAYWRIGHT_HOSTS = {
    "mp.weixin.qq.com",
    "www.engadget.com",
    "www.theverge.com",
    "www.wired.com",
    "news.google.com",
    "www.toutiao.com",
}
DROP = re.compile(r"(?is)<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>")
# 站点样板截断点：命中即从此处砍掉（避免「相关文章 / 关键词：」这类尾巴污染素材）
CUT = re.compile(r"(投诉水文|我要纠错|下载IT之家APP|相关文章|关键词：|责任编辑：|【本文结束】|"
                 r"Most popular|Next up|Subscribe|Read more|Share this|Sign up for|"
                 r"Follow us|Advertisement|相关阅读|热门新闻|热门文章|延伸閱讀|推薦文章)")
BLOCK = re.compile(r"(?i)</?(p|div|br|li|h[1-6]|tr|section|article)[^>]*>")


_OPENERS = {}


def _opener(proxy):
    """proxy=None 时沿用环境变量（http_proxy/https_proxy），与旧版 urlopen 行为一致。"""
    key = proxy or ""
    if key not in _OPENERS:
        if proxy:
            _OPENERS[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            _OPENERS[key] = urllib.request.build_opener()
    return _OPENERS[key]


# 无重试价值的 HTTP 状态：反爬/不存在，重试只烧时间（2026-11-17 实测 403 站点换代理仍 403）
NO_RETRY = (401, 403, 404, 410)


def fetch_text(url, chars, proxy=None, retries=2, timeout=12):
    """抓正文。2026-11-17 修：原版无代理参数、无重试、timeout 固定 10s。
    实测直连 technologyreview 抛 URLError（网络层失败），走 7890 能拿到内容；
    403/404 类站点（theinformation、openai）换代理照样 403，不重试。
    2026-11-18：对 PLAYWRIGHT_HOSTS 域名若配了 CDP 端点，改用 Playwright 抓取。"""
    t0 = time.time()
    
    # 云浏览器分流：Browserbase 优先（REST 建 session），其次 Browserless（直接 CDP）
    host = urllib.parse.urlparse(url).netloc.lower()
    pw_endpoint = None
    bb_sid = None
    if BROWSERBASE_API_KEY and any(h in host for h in PLAYWRIGHT_HOSTS):
        try:
            pw_endpoint, bb_sid = browserbase_cdp_url(BROWSERBASE_API_KEY)
        except Exception as e:
            pw_endpoint = None  # 建 session 失败回落 Browserless / urllib
    elif PLAYWRIGHT_CDP_ENDPOINT and any(h in host for h in PLAYWRIGHT_HOSTS):
        pw_endpoint = PLAYWRIGHT_CDP_ENDPOINT
    if pw_endpoint:
        import asyncio
        # websocket 走代理（connect.browserbase.com / chrome.browserless.io 本机直连不稳）
        os.environ.setdefault("https_proxy", "http://127.0.0.1:7890")
        os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                fetch_text_playwright(url, chars, pw_endpoint, timeout=timeout))
            result["sec"] = round(time.time() - t0, 1)
            if bb_sid:
                result["engine"] = f"browserbase:{bb_sid[:8]}"
            return result
        finally:
            loop.close()
    
    opener = _opener(proxy)
    err = ""
    tries = 0
    while True:
        tries += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
            raw = opener.open(req, timeout=timeout).read()
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
                    "lang": lang, "sec": round(time.time() - t0, 1), "tries": tries}
        except urllib.error.HTTPError as e:
            err = "HTTP%d" % e.code
            if e.code in NO_RETRY or tries > retries:
                break
            time.sleep(0.6 * tries)
        except Exception as e:
            err = type(e).__name__
            if tries > retries:
                break
            time.sleep(0.6 * tries)
    return {"ok": False, "chars": 0, "text": "", "err": err, "sec": round(time.time() - t0, 1),
            "tries": tries}


async def fetch_text_playwright(url, chars, cdp_endpoint, timeout=60):
    """用 Playwright 通过 CDP 连接云浏览器抓正文（仅 JS 必需站点）。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "chars": 0, "text": "", "err": "PlaywrightNotInstalled", "sec": 0}

    t0 = time.time()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_endpoint, timeout=30000)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            await page.wait_for_timeout(5000)  # 等 JS 渲染完成

            article = await page.query_selector("article")
            if article:
                text = await article.inner_text()
            else:
                text = await page.inner_text("body")
            await browser.close()

            text = re.sub(r"[ \t\u3000]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n", text).strip()
            cjk = len(re.findall(r"[\u4e00-\u9fa5]", text))
            lang = "zh" if cjk > len(text) * 0.15 else "en"
            return {"ok": len(text) >= 600, "chars": len(text), "text": text[:chars],
                    "lang": lang, "sec": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "chars": 0, "text": "", "err": f"Playwright:{type(e).__name__}",
                "sec": round(time.time() - t0, 1)}


def browserbase_cdp_url(api_key, timeout_s=300):
    """创建 Browserbase session 并返回 CDP URL（wss://connect.browserbase.com?apiKey=…&sessionId=…）。
    REST 建会话需要代理（api.browserbase.com 本机 SSL 握手超时，走 mihomo 7890）。"""
    import urllib.request as _ur
    proxy = _ur.ProxyHandler({"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
    opener = _ur.build_opener(proxy)
    body = json.dumps({"projectId": BROWSERBASE_PROJECT}).encode() if BROWSERBASE_PROJECT else b'{}'
    req = _ur.Request("https://api.browserbase.com/v1/sessions", method="POST",
                      headers={"x-bb-api-key": api_key, "Content-Type": "application/json"}, data=body)
    with opener.open(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    sid = data["id"]
    return f"wss://connect.browserbase.com?apiKey={api_key}&sessionId={sid}", sid


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
    proxy = arg("--proxy") or os.environ.get("ENRICH_PROXY") or None
    fb_proxy = arg("--fallback-proxy") or os.environ.get("ENRICH_FALLBACK_PROXY") or None
    retries = int(arg("--retries", 2))

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

    print("%s（%s）→ %s · 待抓 %d · 并发 %d · 每篇取 %d 字%s"
          % (label, mode, os.path.basename(path), len(targets), workers, chars,
             (" · 代理 " + proxy if proxy else " · 直连") + (f" · 重试 {retries}" if retries else "")))
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda u: (u, fetch_text(u, chars, proxy=proxy, retries=retries)), targets))
    # 直连网络层失败的条目用代理兜底（实测：国内直连 technologyreview 抛 URLError 而走 7890 可通；
    # 反之 NYT 类站点直连可抓、走美国机房 IP 反而 403——所以代理只做兜底，不做默认）
    NET_FAIL = ("URLError", "TimeoutError", "socket.timeout", "RemoteDisconnected",
                "ConnectionResetError", "SSLError", "OSError")
    if fb_proxy:
        net_bad = [u for u, r in res if not r["ok"] and r.get("err") in NET_FAIL]
        if net_bad:
            print("网络层失败 %d 条，用兜底代理 %s 重试" % (len(net_bad), fb_proxy))
            with ThreadPoolExecutor(workers) as ex:
                fixed = list(ex.map(lambda u: (u, fetch_text(u, chars, proxy=fb_proxy, retries=1)), net_bad))
            store = {u: r for u, r in res}
            store.update(dict(fixed))
            res = [(u, store[u]) for u, _ in res]
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