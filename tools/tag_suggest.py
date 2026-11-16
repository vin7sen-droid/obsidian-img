#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按历史笔记给标签候选（karakeep curatedTags 思路，无 LLM 依赖）。

用法:
  python3 tools/tag_suggest.py "标题文本" [--summary 摘要] [-n 8]
  python3 tools/tag_suggest.py --file "<笔记路径>"

输出:
  候选标签（相似历史笔记实际用过的，按相似度加权） + 依据哪几篇
"""
import json, os, re, sys, argparse, glob

DICT = "/root/workspace/tools/tagdict.json"
EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{1,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

STOP_EN = set("the and for with that this from into over more than its you your our are was were has have "
              "will can not but all new now how why what when who out off via per on in to of a an is be by".split())
STOP_ZH = set("我们 他们 这些 那些 一个 一种 已经 正在 可以 这个 那个 但是 因为 所以 如果 就是 不是 没有 什么 怎么 如何 "
              "以及 还有 或者 而且 目前 今天 昨天 明天 消息 报道 表示 认为 现在 已经 可能 将会 需要 用户 公司 产品".split())

def build_dict(inbox="/sdcard/Obsidian/dsh/ReadItLater Inbox"):
    """扫 Inbox 全部笔记重建词典（tags YAML 块/内联/#行三种版式都认）。"""
    import collections
    docs, cnt, notag = [], collections.Counter(), []
    for root, ds, fs in os.walk(inbox):
        if "/." in root:
            continue
        for f in sorted(fs):
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            txt = open(p, encoding="utf-8", errors="replace").read()
            head = txt[:900]
            m = re.search(r"^tags:\s*\[(.*?)\]\s*$", head, re.M)
            if m:
                raw = [x.strip().strip('"\'') for x in m.group(1).split(",")]
            else:
                m = re.search(r"^tags:\s*$\n((?:[ \t]+-[^\n]*\n)+)", head, re.M)
                if m:
                    raw = [l.strip()[1:].strip().strip('"\'') for l in m.group(1).strip().split("\n")]
                else:
                    m = re.search(r"^#[^\s#].*$", head, re.M)
                    raw = [x.lstrip("#") for x in m.group(0).split() if x.startswith("#")] if m else []
            tags = [t for t in raw if t and not t.startswith("--")]
            if not tags:
                notag.append(p)
                continue
            mt = re.search(r"^#\s+(.+)$", txt, re.M)
            docs.append({"path": p, "title": (mt.group(1).strip() if mt else f[:-3]), "tags": tags})
            for t in tags:
                cnt[t] += 1
    co = collections.defaultdict(collections.Counter)
    for d in docs:
        for a1 in d["tags"]:
            for b1 in d["tags"]:
                if a1 != b1:
                    co[a1][b1] += 1
    import datetime
    json.dump({"built_at": datetime.date.today().isoformat(), "docs": docs, "counts": dict(cnt),
               "cooc": {k: dict(v) for k, v in co.items()}},
              open(DICT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"词典已重建：{len(docs)} 篇有标签 / {len(notag)} 篇无 / {len(cnt)} 个唯一标签 → {DICT}")


def toks(text):
    en = {w.lower() for w in EN_RE.findall(text) if len(w) >= 2 and w.lower() not in STOP_EN}
    zh = set()
    for seg in CJK_RE.findall(text):
        for i in range(len(seg) - 1):
            zh.add(seg[i:i+2])
        if len(seg) <= 4 and seg not in STOP_ZH:
            zh.add(seg)
    return en, zh

def sim(a_en, a_zh, b_en, b_zh):
    en_i = len(a_en & b_en)
    zh_i = len(a_zh & b_zh)
    if en_i == 0 and zh_i < 2:
        return 0.0
    # 英文/型号命中权重高（专名），中文 bigram 权重低
    return en_i * 3.0 + zh_i * 0.5

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?", default="")
    ap.add_argument("--file")
    ap.add_argument("--summary", default="")
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="扫 Inbox 重建 tagdict.json")
    a = ap.parse_args()
    if a.rebuild:
        build_dict()
        return
    text = a.title + " " + a.summary
    if a.file:
        t = open(a.file, encoding="utf-8", errors="replace").read()
        m = re.search(r"^#\s+(.+)$", t, re.M)
        text = (m.group(1) if m else os.path.basename(a.file)) + " " + a.summary
    if not text.strip():
        sys.exit("需要标题")
    d = json.load(open(DICT, encoding="utf-8"))
    q_en, q_zh = toks(text)
    scored = []
    for doc in d["docs"]:
        d_en, d_zh = toks(doc["title"])
        s = sim(q_en, q_zh, d_en, d_zh)
        if s > 0:
            scored.append((s, doc))
    scored.sort(key=lambda x: -x[0])
    counts = d["counts"]
    cand = {}
    for s, doc in scored[:40]:
        for t in doc["tags"]:
            e = cand.setdefault(t, {"w": 0.0, "n": 0, "by": []})
            e["w"] += s
            e["n"] += 1
            if len(e["by"]) < 3:
                e["by"].append((round(s, 1), doc["title"][:34]))
    ranked = sorted(cand.items(), key=lambda kv: -(kv[1]["w"] + counts.get(kv[0], 0) * 0.15))
    # 相对阈值截断：远离最高分的候选多是 bigram 噪音（2026-11-17 实测 F1 64%→需瘦身）
    if ranked:
        _top = ranked[0][1]["w"] + counts.get(ranked[0][0], 0) * 0.15
        ranked = [(t, e) for t, e in ranked if (e["w"] + counts.get(t, 0) * 0.15) >= _top * 0.45]
    if a.brief:
        _k = min(a.n, 5)
        top = "、".join(t for t, _ in ranked[: _k])
        dup = ""
        for _t, e in ranked:
            for _s, _ti in e["by"]:
                if _s >= 12:
                    dup = f" | ⚠️疑似重复: {_ti}（{_s}）"
                    break
            if dup:
                break
        print(f"标签候选: {top or '无（新主题）'}{dup}")
        return
    print(f"查询: {text.strip()[:70]}")
    print(f"命中历史笔记 {len(scored)} 篇（英文词 {len(q_en)} / 中文片 {len(q_zh)}）\n")
    if not ranked:
        print("无语料命中 —— 只能新造标签。参考全局高频：")
        print("  " + "、".join(f"{k}({v})" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:12]))
        return
    print("候选标签（越靠前越该复用）:")
    for t, e in ranked[: a.n]:
        by = "；".join(f"{s}·{tt}" for s, tt in e["by"])
        print(f"  {t:<14} 词典 {counts.get(t, 0):>2} 篇 | 本次命中 {e['n']} 篇 | {by}")
    print("\n全局高频（新造标签前先看一眼）:")
    print("  " + "、".join(f"{k}({v})" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:15]))

if __name__ == "__main__":
    main()
