# -*- coding: utf-8 -*-
"""AI 日报一键抓取+过滤：

    python3 daily.py 2026-11-12                 # 默认输出到 /root/workspace/a2026-11-12
    python3 daily.py 2026-11-12 --out /root/workspace/a2
    python3 daily.py 2026-11-12 --since "11-11 18:00" --dump
    python3 daily.py 2026-11-15 --since "11-15 00:00" --until "11-16 00:00" --no-radar   # 补抓一整天
    python3 daily.py 2026-11-16 --fast        # 快速模式：雷达只复用共享池不重抓（隐含 --no-radar-force）

产出 <out>/raw.json（31 源全量 + 雷达盲区补充）与 <out>/pool.json（已过时间窗/AI 主体/查重）。

雷达步骤（radar.py，默认开）：只抓我们 17 个重叠源之外的 28 个源（The Information /
FT / Bloomberg / WSJ / NYT / CNBC / cnBeta / 钛媒体 / 极客公园 / 智东西 / Anthropic /
Google Research / AWS ML / Meta Engineering / VentureBeat / IEEE / The Register /
SemiAnalysis / Stratechery / Axios / ZDNet / Crunchbase / smol.ai / TechCrunch Venture …）
+ Google/Bing 关键词检索，结果合并进 raw.json。--no-radar 可跳过。
下一步：把 pool.json 交给各队编辑写 parts/*.md，再跑 assemble.py。

抓完会自动跑 prep.py（0.2 秒）：事件聚类 + 六类初分类 + 生成候选清单（<out>/prep/），
四队照着清单写即可；prep/families.md 列出跨类实体族，派单时整族交给同一个队员。
prep 之后自动跑 enrich.py：对每簇主源抓正文节选写 prep/enrich.json（约 30-60 秒），
dispatch 会给写手多一行「正文节选」；--no-enrich 跳过。
极速模式（--fast）走扁平派单：enrich 抓 pool.json 里 desc 最短的 40 条写 <out>/enrich.json，
派单用 `dispatch.py <out> --mode fast --teams 3`。

本流程**不含剪藏**（一天 97 篇逐条落进 Inbox 要 5 分钟，且属于独立需求）。
需要剪藏时另外跑（备用路径，2026-11-12 后未再用；日常剪藏是用户给单链接跑 clip.py）：
  python3 /root/workspace/tools/clip_daily.py <日报.md> <输出目录>
  反爬站抓不到的再跑 tools/proxy_clip.py 走 r.jina.ai 补齐。
"""
import os, sys, subprocess, json, time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        print(__doc__); sys.exit(2)
    date = sys.argv[1]
    out = "/root/workspace/a" + date[5:]
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    rest = []
    if "--since" in sys.argv:
        rest += ["--since", sys.argv[sys.argv.index("--since") + 1]]
    if "--until" in sys.argv:
        rest += ["--until", sys.argv[sys.argv.index("--until") + 1]]
    if "--dump" in sys.argv:
        rest.append("--dump")
    if "--hours" in sys.argv:
        rest += ["--hours", sys.argv[sys.argv.index("--hours") + 1]]
    fast = "--fast" in sys.argv
    FAST_ONLY = "The Verge — AI,TechCrunch — AI,The Decoder,Engadget,Techmeme,量子位,雷峰网,新智元,机器之心,TechNews 科技新报,IT之家,iThome 台湾"
    os.makedirs(out, exist_ok=True)
    manifest = {"date": date, "out": os.path.abspath(out), "started": time.time(), "stages": {}}
    def stage(name, rc, outputs=()):
        manifest["stages"][name] = {"returncode": rc, "outputs": [x for x in outputs if os.path.exists(x)], "finished": time.time()}
        with open(os.path.join(out, "run.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)

    print("=== 1/3 抓取 31 源 →", out)
    fetch_cmd = [sys.executable, os.path.join(HERE, "fetch.py")]
    if fast:
        fetch_cmd += ["--only", FAST_ONLY]
        print("  （极速模式：抓取快源白名单）")
    fetch_cmd += ["--out", out]
    r = subprocess.run(fetch_cmd)
    stage("fetch", r.returncode, [os.path.join(out, "raw_fetch.json")])
    if r.returncode:
        sys.exit("抓取失败")
    print("\n=== 2/3 雷达盲区补充（The Information / FT / Bloomberg / CNBC / cnBeta / 检索…）")
    radar_rc = 0
    if "--no-radar" not in sys.argv:
        # 2026-11-24: 改用 Firecrawl 版雷达（radar_firecrawl.py），无需 node/代理/Browserbase
        radar_cmd = [sys.executable, "/workspace/tools/radar_firecrawl.py", "--dir", out] + rest
        if fast:
            radar_cmd += ["--max-age", "99999"]   # --fast：只复用共享池，绝不重抓
            print("  （快速模式：雷达只复用共享池，4h 内有则 0 代价）")
        else:
            radar_cmd += ["--workers", "6", "--limit", "8"]
        r = subprocess.run(radar_cmd)
        radar_rc = r.returncode
        if r.returncode:
            print("  雷达补充失败（不影响主流程），继续")
    else:
        print("  跳过（--no-radar）")
    stage("radar", radar_rc, [os.path.join(out, "raw.json")])
    print("\n=== 3/3 过滤 → pool.json")
    # HN 补量（2026-11-19 用户定「完整模式也跑hn」）：AI 主体高票帖并入 raw/pool 候选
    if "--no-hn" in sys.argv:
        print("\n=== 2b/3 HN 补量：跳过（--no-hn）")
    else:
        hn_since = date + " 00:00"
        if "--since" in sys.argv:
            hn_since = sys.argv[sys.argv.index("--since") + 1]
        hn_cmd = [sys.executable, "/root/workspace/tools/hn.py",
                  "--since", hn_since, "--points", "60", "--limit", "40",
                  "--ai", "filter", "--dir", out]
        print("\n=== 2b/3 HN 补量（points>60，AI 主体，窗口自 %s）" % hn_since)
        subprocess.run(hn_cmd)

    r = subprocess.run([sys.executable, os.path.join(HERE, "filter.py"), "--dir", out] + rest)
    stage("filter", r.returncode, [os.path.join(out, "pool.json")])
    if r.returncode:
        sys.exit("过滤失败（rc=%d），停止后续派单" % r.returncode)
    print("\n产物:", out + "/raw.json", out + "/pool.json")
    print("（剪藏不在本流程内：如需要，跑 clip_daily.py + proxy_clip.py——备用路径，2026-11-12 后未再用）")
    # 预处理：聚类 + 初分类 + 候选清单
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "prep.py"), date, "--dir", out],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            print("\n" + r.stdout.strip())
            # A 层：给写手补 RSS desc 之外的正文（治「素材太短只能猜」的编造）
            if "--no-enrich" in sys.argv:
                print("  跳过正文补料（--no-enrich）")
            elif fast:
                # 极速模式：扁平池没聚类，抓 desc 最短的 40 条（领队只会挑其中二三十条）
                subprocess.run([sys.executable, os.path.join(HERE, "enrich.py"), out,
                                "--input", "pool.json", "--top", "40",
                                "--out", os.path.join(out, "enrich.json")])
            else:
                r = subprocess.run([sys.executable, os.path.join(HERE, "enrich.py"), out])
                if r.returncode:
                    sys.exit("正文补料失败（rc=%d）" % r.returncode)
            print("→ 生成派单：")
            teams = ["--teams", "3"] if fast else []
            r = subprocess.run([sys.executable, os.path.join(HERE, "dispatch.py"), out] + teams)
            if r.returncode:
                sys.exit("派单失败（rc=%d）" % r.returncode)
            print("\n队员按 dispatch/{1..3}.md 的 JSON schema 写 → json/T*.json")
            if fast:
                print("\n【快速模式作业法】交一队审一队落一队，不等三队齐：")
                print("  1. 每队交付 → python3 json2md.py --check <out>/json/TN.json")
                print("  2. 过了就 append：python3 append.py <已发md> <out>/json/TN.json --html")
                print("  3. linkcheck 用 --only-new 只查新增；首期可先落盘后补查")
                print("  4. 复核：领队自查（跨队查重+json2md --check+mustread），同天发现硬伤就地改")
                print("  5. 语义初筛：python3 news-ai/semantic_run.py <out> （需用户确认修后再重发）")
            else:
                print("然后：python3 json2md.py <out>/json/T*.json && python3 assemble.py ...")
        else:
            print("prep 跳过:", (r.stderr or "").strip()[:120])
    except Exception as e:
        print("prep 跳过:", type(e).__name__, str(e)[:80])


if __name__ == "__main__":
    main()
