# -*- coding: utf-8 -*-
"""链接核查：python3 linkcheck.py out.md [--all] [--only-new 旧版.md]

并发 12，HEAD 失败转 GET；403/405/429/503 视为站点反爬（保留），只报 404/410/超时。

--only-new 旧版.md：只查这一版里新增的链接（补抓/增补轮用——已发布的 URL 上一轮查过，
不必重查，67 条的补抓轮从 200+ 条降到 19 条、几秒出结果）。

SKIP_HOSTS 里的站点对任何 UA 都返 403/401（Cloudflare / 付费墙 / WAF），每次核查都必然
是「保留」，直接跳过不请求——省掉约三分之一请求。要连它们一起查就加 --all。
"""
import os, re, sys, json, ssl, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
SOFT = {403, 405, 429, 503, 406}

# 已知反爬/付费墙站点：请求必然返 403/401，跳过不查（结果一律按「保留」处理）
SKIP_HOSTS = (
    "openai.com", "nytimes.com", "wsj.com", "bloomberg.com", "reuters.com",
    "arstechnica.com", "thestreet.com", "stocktwits.com", "hpcwire.com",
    "marketbeat.com", "ft.com", "economist.com", "theinformation.com",
    "wsj.net", "barrons.com", "huggingface.co",
)


def skipped(u):
    host = re.sub(r"https?://([^/]+).*", r"\1", u).lower()
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def check(u):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(u, headers=UA, method=method)
            r = urllib.request.urlopen(req, timeout=20, context=ctx)
            return u, r.status, ""
        except urllib.error.HTTPError as e:
            if method == "HEAD":
                continue  # HEAD 误报多（wallstreetcn/engadget 实测 HEAD 404 GET 200），一律转 GET 复核
            return u, e.code, "dead" if e.code in (404, 410) else "soft"
        except Exception:
            if method == "GET":
                return u, 0, "unreach"  # 超时/断连：本机网络不可达（墙外站常见），保留待人工
    return u, 0, "unreach"


def main():
    md = sys.argv[1]
    t = open(md, encoding="utf-8").read()
    all_urls = sorted(set(re.findall(r"\]\((https?://[^)\s]+)\)", t)))
    if "--all" in sys.argv:
        urls = all_urls
    else:
        urls = [u for u in all_urls if not skipped(u)]
    if "--only-new" in sys.argv:   # 补抓/增补轮：只查相对旧版新增的链接
        oldp = sys.argv[sys.argv.index("--only-new") + 1]
        old = set(re.findall(r"\]\((https?://[^)\s]+)\)", open(oldp, encoding="utf-8").read()))
        before = len(urls)
        urls = [u for u in urls if u not in old]
        print("只查新增（相对 %s）：%d → %d 条" % (os.path.basename(oldp), before, len(urls)))
    skipped_n = len(all_urls) - len(urls)
    print(f"URL {len(all_urls)}（跳过已知反爬 {skipped_n}，实查 {len(urls)}）")
    bad = []
    with ThreadPoolExecutor(12) as ex:
        for u, code, tag in ex.map(check, urls):
            if 200 <= code < 300:
                continue
            if tag in ("soft", "unreach"):
                print("  SOFT %s %s" % (code or tag, u[:90]))
            else:
                print("  DEAD %s %s" % (code or tag, u[:90]))
                bad.append({"url": u, "code": code, "tag": tag})
    json.dump(bad, open("linkcheck.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("需处理(404/超时):", len(bad))
    if len(bad):
        sys.exit(1)  # 死链拦截（借鉴科技线 publish.py）：404/410/超时存在时非零退出


if __name__ == "__main__":
    main()
