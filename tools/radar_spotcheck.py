#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""雷达日报语义抽查（2026-11-15 用户定：每次出日报后必做）

机器交叉校验（xcheck.mjs）只能抓数字/单位/漏译，抓不到语义错：
NasdaqGM→「纳斯达克通用汽车公司」、模型名 Bel→「贝尔」、Microduck→Microbuck
这类只有把英文原文和中文译文并排看才看得出来。

用法：
  python3 radar_spotcheck.py                 # 抽 20 条，今天日期
  python3 radar_spotcheck.py --n 30          # 抽 30 条
  python3 radar_spotcheck.py --date 2026-11-14
  python3 radar_spotcheck.py --md <路径> --json <路径>   # 指定文件
  python3 radar_spotcheck.py --seed 1        # 换一批随机样本

输出：终端打印中英对照，同时存 /tmp/spotcheck_<date>.json 供复查。
"""
import argparse
import json
import os
import random
import re
import sys

DIGEST_DIR = "/root/workspace/digests"
RADAR_DIR = "/sdcard/Obsidian/dsh/新闻/AI radar"


def load_items(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {it["url"]: it for it in data.get("items", [])}


def parse_md(path):
    """解析 renderFullMarkdown 产物：N. **[标题](url)** + 缩进正文行"""
    rows, cur = [], None
    with open(path, encoding="utf-8") as f:
        for ln in f.read().split("\n"):
            m = re.match(r"^(\d+)\. \*\*\[(.+?)\]\((.+?)\)\*\*$", ln)
            if m:
                cur = {"n": int(m.group(1)), "zh": m.group(2), "url": m.group(3), "body": ""}
                rows.append(cur)
                continue
            if cur is not None and ln.startswith("   "):
                cur["body"] = (cur["body"] + " " + ln.strip()).strip()
            elif ln.strip() == "":
                cur = None
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认取 md 文件名里的日期")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--md", default=None)
    ap.add_argument("--json", dest="jpath", default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    md = args.md
    jpath = args.jpath
    if not md:
        if args.date:
            md = os.path.join(RADAR_DIR, f"AI圈新闻雷达_{args.date}.md")
            jpath = jpath or os.path.join(DIGEST_DIR, f"{args.date}.json")
        else:
            cands = sorted(
                f for f in os.listdir(RADAR_DIR)
                if re.match(r"^AI圈新闻雷达_\d{4}-\d{2}-\d{2}\.md$", f)
            )
            if not cands:
                sys.exit("没找到雷达 md")
            md = os.path.join(RADAR_DIR, cands[-1])
            d = re.search(r"(\d{4}-\d{2}-\d{2})", cands[-1]).group(1)
            jpath = jpath or os.path.join(DIGEST_DIR, f"{d}.json")
    if not jpath:
        d = re.search(r"(\d{4}-\d{2}-\d{2})", md).group(1)
        jpath = os.path.join(DIGEST_DIR, f"{d}.json")

    if not os.path.exists(md):
        sys.exit(f"md 不存在：{md}")
    if not os.path.exists(jpath):
        sys.exit(f"json 不存在：{jpath}")

    by_url = load_items(jpath)
    rows = parse_md(md)
    cand = [
        r for r in rows
        if r["url"] in by_url
        and (by_url[r["url"]].get("title") or "").strip()
        and by_url[r["url"]]["title"] != r["zh"]
    ]
    if not cand:
        sys.exit("没有可抽查的「英文原文→中文译文」对")

    n = min(args.n, len(cand))
    rnd = random.Random(args.seed) if args.seed is not None else random.Random()
    pick = rnd.sample(cand, n)

    out = []
    print(f"md 条目 {len(rows)} · 已译 {len(cand)} · 抽查 {n} 条")
    for i, r in enumerate(pick, 1):
        it = by_url[r["url"]]
        item = {
            "n": r["n"],
            "en_title": it.get("title", ""),
            "zh_title": r["zh"],
            "en_sum": (it.get("summary") or "")[:600],
            "zh_body": r["body"][:600],
            "url": r["url"],
        }
        out.append(item)
        print(f"\n===== [{i}] md#{item['n']}")
        print("EN :", item["en_title"])
        print("ZH :", item["zh_title"])
        if item["en_sum"]:
            print("ENs:", item["en_sum"][:300])
        body_txt = re.sub(r"（[^（）]*·\s*\d{2}-\d{2} \d{2}:\d{2}）.*$", "", item["zh_body"]).strip()
        if body_txt:
            print("ZHs:", body_txt[:300])

    tag = os.path.basename(md).replace(".md", "")
    outp = f"/tmp/spotcheck_{tag.split('_')[-1]}.json"
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n清单已存 {outp}（共 {n} 条）")
    print("人工过一遍，重点看：人名/机构名、缩写（NasdaqGM/NYSE）、模型名、专名拼写、"
          "否定与因果是否反、'watch/all about'这类视频标题引导词。")


if __name__ == "__main__":
    main()