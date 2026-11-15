#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘要数字校验（2026-11-16 定，精修收尾自查用）

用法：
  python3 tools/summary_check.py "<笔记路径>" [更多路径...]

规则：笔记「摘要：」行里的数字必须能在正文（摘要行之后、评论区之前）找到——
防「摘要写了个正文没有的数」与 over six million→精确数 式合理化编造。
千分位归一化（72,000 == 72000）；匹配是子串级（正文含 4 万亿 即覆盖摘要的 4 万亿）。

输出：每篇一行 OK / MISSING（缺的数字列出）；有 MISSING 时退出码 1。
"""
import re, sys, glob

NUM_RE = re.compile(r"[\w$€£¥%.,+-]*\d[\w$€£¥%.,+-]*")


def norm_tokens(text):
    """数字 token 集合，千分位逗号归一；剥尾部标点；token 里的连续数字段也单独入集合
    （防 Sub-100ms / 100ms 这类带单位/前后缀的形态挡住摘要的裸数字）。"""
    out = set()
    for t in NUM_RE.findall(text):
        t = t.strip(".,+-")
        if not t:
            continue
        out.add(t.replace(",", ""))
        for d in re.findall(r"\d+", t):
            out.add(d)
    return out


def check(path):
    t = open(path, encoding="utf-8").read()
    lines = t.split("\n")
    # frontmatter 之后找摘要行
    start, fm_end = 0, 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm_end = i + 1
                break
    sm_i = next((i for i in range(fm_end, len(lines)) if lines[i].startswith("摘要：")), None)
    if sm_i is None:
        return None, "无摘要行"
    # 参照集 = 除摘要行外的全文（H1/正文/评论区标题与内容——「37 条评论」这类摘要数字
    # 合法来源在评论区标题，H1 也常带数字；图片 URL 的 hex 只造成假放行不造成假缺失）
    summary = lines[sm_i][len("摘要："):]
    body = "\n".join(lines[fm_end:sm_i] + lines[sm_i + 1 :])
    body_toks = norm_tokens(body)
    # 摘要数字必须命中正文；正文 token 允许带纯字母单位后缀（摘要「100 毫秒」↔ 正文「100ms」）
    missing = sorted(
        (x for x in norm_tokens(summary)
         if x not in body_toks
         and not any(g.startswith(x) and g[len(x):].isalpha() for g in body_toks)),
        key=lambda x: (len(x), x))
    return missing, summary[:50]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for pat in sys.argv[1:]:
        for p in (glob.glob(pat) if any(c in pat for c in "*?[") else [pat]):
            missing, info = check(p)
            if missing is None:
                print("SKIP  %s（%s）" % (p, info))
            elif not missing:
                print("OK    %s" % p)
            else:
                bad += 1
                print("MISS  %s" % p)
                print("      摘要数字不在正文：%s ｜ 摘要首 50 字：%s" % ("、".join(missing), info))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
