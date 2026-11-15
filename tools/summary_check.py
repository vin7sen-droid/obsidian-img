#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘要数字校验（2026-11-16 定，精修收尾自查用；2026-11-16 借鉴热评会话定版：清单制 + SI 归一）

用法：
  python3 tools/summary_check.py "<笔记路径>" [更多路径...]

规则：笔记「摘要：」行里的数字必须能在正文（除摘要行外的全文，含 H1/评论区标题）找到——
防「摘要写了个正文没有的数」与 over six million→精确数 式合理化编造。

定位（借鉴定案）：**只出清单，不当阻断闸**——摘要是作者改写，允许引入正文没有的
背景数或把约数改写成精确数；flag 由人 30 秒过一遍：编造/换算错 → 修；
约数改写 / 补背景 / SI 换算 → 放行。阻断式只适合译文（xcheck 管那层）。

SI 归一（只对**带量级单位**的 token 做）：摘要「130 亿」↔ 正文「$13 billion」、
「40000 亿」↔「4 万亿」是合法换算，自动放行（万=1e4 亿=1e8 万亿=1e12
B/billion=1e9 M/million=1e6 k/thousand=1e3）。裸数字（无单位）不做 SI 跨比——
量级在单位里，裸值相等不代表语义相等（摘要「4 亿美元」≠ 正文「第 4 页」的 4）。
千分位归一（72,000==72000）；单位前缀放行（摘要「100 毫秒」↔ 正文「100ms」）。

输出：每篇一行 OK / CHECK（flag 数字列出，附摘要写法）；有 flag 时退出码 1（提醒核对，非阻断）。
"""
import re, sys, glob

NUM_RE = re.compile(r"[\w$€£¥%.,+-]*\d[\w$€£¥%.,+-]*")

# 「数字 空格 量级词」粘连——中文习惯「267400 亿元」「4 万亿」带空格会把量级词从 token 里断开，
# SI 归一就失效了；比对前先把量级词粘回数字（只粘白名单量级词，不动其他文本）。
GLUE_RE = re.compile(r"(\d)\s+((?:万亿|亿|万)(?![\dA-Za-z])|billion|million|thousand)", re.IGNORECASE)


def _glue(text):
    return GLUE_RE.sub(r"\1\2", text)

SI_UNITS = [("万亿", 1e12), ("billion", 1e9), ("B", 1e9), ("亿", 1e8),
            ("million", 1e6), ("M", 1e6), ("万", 1e4), ("thousand", 1e3),
            ("K", 1e3), ("k", 1e3)]


def si_scaled(tok):
    """(绝对值, 是否带量级单位)。13B→(1.3e10,True)、130亿→(1.3e10,True)、12345→(12345,False)、
    100ms→(None,False)（非量级单位不定值）。"""
    t = tok.replace(",", "").strip(".,+-")
    m = re.match(r"^[$€£¥₹]?\s*(\d+(?:\.\d+)?)\s*(.*)$", t)
    if not m:
        return None, False
    num, unit = float(m.group(1)), m.group(2)
    if unit in ("", "%"):
        return num, False
    for u, mult in SI_UNITS:
        if unit.startswith(u):
            return num * mult, True
    return None, False


def norm_tokens(text):
    """(token集合, 数字段→所属raw映射)。token 里的连续数字段也单独入集合
    （防 Sub-100ms / 100ms 这类形态挡住摘要的裸数字）；映射供 SI 二级判定——
    数字段「40000」借完整 token「40000亿美元」的量级语义。"""
    out, seg = set(), {}
    for t in NUM_RE.findall(text):
        t = t.replace(",", "").strip(".,+-")
        if not t:
            continue
        out.add(t)
        seg[t] = t
        for d in re.findall(r"\d+", t):
            out.add(d)
            seg.setdefault(d, t)
    return out, seg


def check(path):
    t = open(path, encoding="utf-8").read()
    lines = t.split("\n")
    # frontmatter 之后找摘要行
    fm_end = 0
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
    summary = _glue(lines[sm_i][len("摘要："):])
    body = _glue("\n".join(lines[fm_end:sm_i] + lines[sm_i + 1 :]))
    body_toks, _ = norm_tokens(body)
    body_si = {si_scaled(b)[0] for b in body_toks if si_scaled(b)[1] and si_scaled(b)[0] is not None}
    sum_toks, sum_seg = norm_tokens(summary)
    # 摘要数字命中正文其一即可：①token 直命中；②正文 token 带纯字母单位后缀（100↔100ms）；
    # ③所属完整 token 带量级单位且 SI 等值（130 亿 ↔ $13 billion、40000 亿 ↔ 4 万亿）
    missing = []
    for x in sorted(sum_toks, key=lambda x: (len(x), x)):
        if x in body_toks:
            continue
        if any(g.startswith(x) and g[len(x):].isalpha() for g in body_toks):
            continue
        raw = sum_seg.get(x, x)
        v, scaled = si_scaled(raw)
        if scaled and v is not None and v in body_si:
            continue
        missing.append((x, raw))
    return missing, summary[:50]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    flags = 0
    for pat in sys.argv[1:]:
        for p in (glob.glob(pat) if any(c in pat for c in "*?[") else [pat]):
            missing, info = check(p)
            if missing is None:
                print("SKIP   %s（%s）" % (p, info))
            elif not missing:
                print("OK     %s" % p)
            else:
                flags += 1
                print("CHECK  %s" % p)
                toks = "、".join(x if x == raw else "%s（摘要写法：%s）" % (x, raw) for x, raw in missing)
                print("       摘要数字待人工核对：%s ｜ 摘要首 50 字：%s" % (toks, info))
                print("       判读：编造/换算错→必须修；约数改写、补背景数、SI 换算→放行（清单制，非阻断）")
    sys.exit(1 if flags else 0)


if __name__ == "__main__":
    main()
