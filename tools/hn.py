#!/usr/bin/env python3
"""hn.py —— HN Algolia 高票帖补充源（唯一真源，AI 日报线与科技日报线共用）

2026-11-17 由 news-ai/hn.py（AI 线）与 news-tools/hn_algolia.py（科技线）合并收敛；
两处各留 runpy 薄转发层（news-ai/hn.py 保 AI 式调用，hn_algolia.py 保科技式调用名）。

API 匿名免 key、直连可用（无需代理）：
  https://hn.algolia.com/api/v1/search?tags=story&numericFilters=created_at_i>{T0},created_at_i<{T1},points>{N}&hitsPerPage=60

用法（两种窗口语法二选一，其余参数通用）：
  # AI 线式：窗口下界时刻 + 默认只留 AI 主体
  python3 hn.py --since "11-16 21:23" [--points 60] [--limit 40] [--og] [--dir <workdir>]
  # 科技线式：当天 0 点回溯 --window 秒 + 全量保留打 is_ai 标
  python3 hn.py --date 2026-11-16 [--min-points 100] [--window 86400] [--desc] [--dir <workdir>]
  通用：--ai filter|tag|off（默认 filter；tag=全量+is_ai 打标；off=不过滤不打标）
        --zh 标题+desc 机翻中文打底（zh.mjs，缓存命中秒回；失败兜底原文不阻塞）
        --points 与 --min-points 互为别名；--og 与 --desc 互为别名
        无 --dir 时打印 JSON 数组（供管道）；--dir 合并 <workdir>/raw.json（URL 去重、
        原子替换、另存 raw_hn.json 备份本轮结果）

输出条目（与 AI 线 pool.json 同构）：src="Hacker News", title, url, date(北京 MM-DD HH:MM),
  ts, desc, points, comments, num_comments, hnid, objectID, hn_url, is_ai

坑（11-16/11-17 实测）：
  1. epoch 用 datetime(..., tzinfo=timezone(timedelta(hours=8))).timestamp() 生成，别手算；
  2. URL 一律用返回的原始 url 字段（无 url 用 item?id=），手拼 slug 全 404；
  3. 高票常是旧文复活，created_at_i 是发帖时间非原文日期；
  4. Algolia 默认按相关性排序，points 降序要自己 sorted；
  5. hitsPerPage 默认 20 会截断——本脚本按 200/页自动翻页拉 nbHits（2026-11-17 修：
     旧版写死 60 且只取一页，--limit>60 会静默截断，实测 7 天窗口真值 330 只回 60）；
  6. daemonology hn-daily 对数据中心 IP 间歇 TLS RST，别用它，本脚本才是主通道。
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
API = "https://hn.algolia.com/api/v1/search"
# AI 词表 = news-ai 版 ∪ news-tools 版并集；一律 \b 词边界（裸 "AI" 会子串误命中 said 里的 ai）
AI_RE = re.compile(r"\b(AI|LLM|GPT|ChatGPT|Copilot|OpenAI|Anthropic|Claude|Gemini|DeepMind|"
                   r"Nvidia|GPU|chip|model|agent|robot|transformer|inference|training|"
                   r"datacenter|data center|semiconductor|TSMC|xAI|Llama|DeepSeek|Qwen|"
                   r"agentic|machine learning|deep learning|neural|"
                   r"Genie|DaisyRose|Phi-\d|MAI-|Kimi|GLM-|Mistral|Grok|Sora|"
                   r"prompt|token|benchmark|reasoning)\b", re.I)
STOP_RE = re.compile(r"^(Ask HN|Tell HN|Show HN: my (side|personal) project)", re.I)


def argval(argv, *names, default=None, cast=str):
    for n in names:
        if n in argv:
            return cast(argv[argv.index(n) + 1])
    return default


def hasflag(argv, *names):
    return any(n in argv for n in names)


def parse_window(argv):
    if "--since" in argv:
        v = argv[argv.index("--since") + 1].strip()
        m = re.match(r"(?:(\d{4})-)?(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", v)
        if not m:
            raise SystemExit(f"✗ --since 格式应为 MM-DD HH:MM 或 YYYY-MM-DD HH:MM：{v!r}")
        y = int(m.group(1)) if m.group(1) else datetime.now(CST).year
        t0 = datetime(y, int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), tzinfo=CST)
        return int(t0.timestamp()), int(datetime.now(CST).timestamp()) + 3600, t0
    date = argval(argv, "--date", default=datetime.now(CST).strftime("%Y-%m-%d"))
    y, mo, d = map(int, date.split("-"))
    win = argval(argv, "--window", default=86400, cast=int)
    t0 = datetime(y, mo, d, tzinfo=CST).timestamp() - win
    return int(t0), int(datetime.now(CST).timestamp()), datetime.fromtimestamp(t0, CST)


def fetch_hits(t0, t1, points, want, per=200, max_pages=10):
    """分页拉 Algolia hits（2026-11-17 修：hitsPerPage 硬编码 60 + 只请求一页 → --limit>60 静默截断）。
    want = 过滤后还想留的条数，原始命中按 3 倍余量拉；返回 (hits, nbHits)。"""
    need = max(60, want * 3)
    out, nb, page = [], None, 0
    while True:
        q = urllib.parse.urlencode({
            "tags": "story",
            "numericFilters": f"created_at_i>{t0},created_at_i<{t1},points>{points}",
            "hitsPerPage": per,
            "page": page,
        })
        req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        if nb is None:
            nb = d.get("nbHits") or 0
        hs = d.get("hits") or []
        if not hs:
            break
        out.extend(hs)
        page += 1
        if len(out) >= nb or len(out) >= need or page >= max_pages:
            break
    return out[:need], nb



ZH_MJS = "/root/workspace/tools/zh.mjs"


def _is_zh(t):
    if not t:
        return True
    cjk = len(re.findall(r"[\u4e00-\u9fa5]", t))
    return cjk > max(len(t) * 0.3, 0)


def translate_zh(items, timeout=240):
    """标题+desc 经 zh.mjs 机翻中文打底；行数不齐/超时/失败一律兜底原文。"""
    import subprocess, tempfile
    jobs = []  # (idx, field, text)
    for i, it in enumerate(items):
        for field in ("title", "desc"):
            t = (it.get(field) or "").strip()
            if t and not _is_zh(t):
                jobs.append((i, field, re.sub(r"\s+", " ", t)))
    if not jobs:
        print("zh: 无需翻译", file=sys.stderr)
        return
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            tmp = f.name
            f.write("\n".join(j[2] for j in jobs) + "\n")
        r = subprocess.run(["node", ZH_MJS, "-f", tmp], capture_output=True,
                           text=True, timeout=timeout)
        out = r.stdout.rstrip("\n").split("\n")
        if len(out) != len(jobs):
            print(f"zh: 行数不齐 {len(out)}/{len(jobs)}，兜底原文", file=sys.stderr)
            return
        for (i, field, src), zh in zip(jobs, out):
            zh = zh.strip()
            if zh and zh != src:
                items[i][field] = zh
        print(f"zh: 已翻译 {len(jobs)} 行（zh.mjs）", file=sys.stderr)
    except Exception as e:
        print(f"zh: 翻译失败兜底原文（{e}）", file=sys.stderr)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def main():
    argv = sys.argv
    if not hasflag(argv, "--since", "--date"):
        sys.exit(__doc__)
    t0, t1, since_dt = parse_window(argv)
    points = argval(argv, "--points", "--min-points", default=100, cast=int)
    limit = argval(argv, "--limit", default=40, cast=int)
    want_og = hasflag(argv, "--og", "--desc")
    ai_mode = argval(argv, "--ai", default="filter")
    if ai_mode not in ("filter", "tag", "off"):
        sys.exit("--ai 只接受 filter|tag|off")

    hits, nb_hits = fetch_hits(t0, t1, points, limit)
    hits.sort(key=lambda h: -h.get("points", 0))

    items = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title or STOP_RE.match(title):
            continue
        is_ai = bool(AI_RE.search(title))
        if ai_mode == "filter" and not is_ai:
            continue
        ts = h["created_at_i"]
        url0 = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        items.append({
            "src": "Hacker News", "title": title, "url": url0,
            "date": time.strftime("%m-%d %H:%M", time.gmtime(ts + 8 * 3600)), "ts": ts,
            "desc": (h.get("story_text") or "").strip(),
            "points": h.get("points") or 0,
            "comments": h.get("num_comments") or 0,
            "num_comments": h.get("num_comments") or 0,
            "hnid": h["objectID"], "objectID": h["objectID"],
            "hn_url": f"https://news.ycombinator.com/item?id={h['objectID']}",
            "is_ai": is_ai,
        })
        if len(items) >= limit:
            break

    if want_og:
        for it in items:
            if not it["url"].startswith("http") or "news.ycombinator.com" in it["url"]:
                continue
            try:
                req = urllib.request.Request(it["url"], headers={"User-Agent": "Mozilla/5.0"})
                html = urllib.request.urlopen(req, timeout=10).read(200000).decode("utf-8", "replace")
                m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                              html, re.I | re.S)
                if not m:
                    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                                  html, re.I | re.S)
                if m:
                    it["desc"] = re.sub(r"\s+", " ", m.group(1)).strip()[:600]
            except Exception:
                pass

    if hasflag(argv, "--zh"):
        translate_zh(items)

    ai_n = sum(1 for it in items if it["is_ai"])
    if hasflag(argv, "--list-ai"):
        print(f"\n[is_ai 清单（{ai_n} 条，合并前逐条确认）]", file=sys.stderr)
        for it in items:
            if it["is_ai"]:
                print(f"  [{it['points']}★] {it['title']}  {it['hn_url']}", file=sys.stderr)
    mode_txt = {"filter": "AI 主体", "tag": "全量（is_ai 打标）", "off": "全量"}[ai_mode]
    print(f"HN Algolia: 窗口 {since_dt.strftime('%m-%d %H:%M')} 起, points>{points} → {mode_txt} {len(items)} 条"
          + (f"（其中 AI {ai_n} 条）" if ai_mode != "filter" else "")
          + f" ｜窗口命中 {nb_hits} 条，分页取 {len(hits)} 条", file=sys.stderr)
    for it in items:
        mark = " [AI]" if (ai_mode != "filter" and it["is_ai"]) else ""
        print(f"  [{it['points']}★/{it['comments']}评] {it['title'][:72]}  {it['date']}{mark}", file=sys.stderr)

    if "--dir" in argv:
        d = argv[argv.index("--dir") + 1].rstrip("/")
        p = os.path.join(d, "raw.json")
        raw = []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        json.dump(items, open(os.path.join(d, "raw_hn.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        seen = {(r.get("url") or "").rstrip("/") for r in raw}
        add = [r for r in items if r["url"].rstrip("/") not in seen]
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(raw + add, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)
        print(f"已合并进 {p}：新增 {len(add)} 条（URL 去重后），共 {len(raw) + len(add)} 条", file=sys.stderr)
    else:
        print(json.dumps(items, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
