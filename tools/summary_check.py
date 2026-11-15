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

SI 归一：摘要「130 亿」↔ 正文「$13 billion」是合法换算，自动放行（数值×量级比对：
万=1e4 亿=1e8 万亿=1e12 B/billion=1e9 M/million=1e6 k/thousand=1e3）。
千分位归一（72,000==72000）；单位前缀放行（摘要「100 毫秒」↔ 正文「100ms」）。

输出：每篇一行 OK / CHECK（flag 数字列出，人工判）；有 flag 时退出码 1（提醒核对，非阻断）。
"""
import re, sys, glob

NUM_RE = re.compile(r"[\w$€£¥%.,+-]*\d[\w$€£¥%.,+-]*")

SI_UNITS = [("万亿", 1e12), ("billion", 1e9), ("B", 1e9), ("亿", 1e8),
            ("million", 1e6), ("M", 1e6), ("万", 1e4), ("thousand", 1e3),
            ("K", 1e3), ("k", 1e3)]


def si_val(tok):
    """token → 绝对数值（数值×量级）；无法解析返回 None。13B / 130亿 / 1.3 万亿 归一比对。"""
    t = tok.replace(",", "").strip(".,+-")
    m = re.match(r"^[$€£¥₹]?\s*(\d+(?:\.\d+)?)\s*(.*)$", t)
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2)
    if unit in ("", "%"):
        return num
    for u, mult in SI_UNITS:
        if unit.startswith(u):
            return num * mult
    return None


def norm_tokens(text):
    """(token集合, 原始token列表)：千分位逗号归一；token 里的连续数字段也单独入集合
    （防 Sub-100ms / 100ms 这类带单位/前后缀的形态挡住摘要的裸数字）。
    原始 token 列表供 SI 二级判定——数字段「40000」须借完整 token「40000亿美元」的量级语义。"""
    out, raw = set(), []
    for t in NUM_RE.findall(text):
        t = t.strip(".,+-")
        if not t:
            continue
        raw.append(t.replace(",", ""))
        out.add(t.replace(",", ""))
        for d in re.findall(r"\d+", t):
            out.add(d)
    return out, raw


def check(path, report=False):
    """report=False：Inbox 笔记「摘要：」行模式；report=True：分析报告「## 摘要」章节模式
    （摘要章节 vs 其余全文，剥离 URL/图片行防 CDN hex 假放行——借 numcheck 跨稿坑的教训）。"""
    t = open(path, encoding="utf-8").read()
    if report:
        m = re.search(r"^##\s*摘\s*要\s*$\n(.*?)(?=^##\s)", t, re.M | re.S)
        if not m:
            return None, "无「## 摘要」章节"
        summary = m.group(1)
        body = t[:m.start()] + t[m.end():]
        # 剥图片行与 URL（CDN hash 只造成假放行，但同剥干净）
        body = "\n".join(l for l in body.split("\n") if not l.strip().startswith("!["))
        body = re.sub(r"https?://\S+", " LINK ", body)
    else:
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
        summary = lines[sm_i][len("摘要："):]
        body = "\n".join(lines[fm_end:sm_i] + lines[sm_i + 1 :])
    body_toks, _ = norm_tokens(body)
    body_si = {v for v in (si_val(b) for b in body_toks) if v is not None}
    sum_toks, sum_raw = norm_tokens(summary)
    # 摘要数字命中正文其一即可：①token 直命中；②正文 token 带纯字母单位后缀（100↔100ms）；
    # ③SI 归一（130 亿 ↔ $13 billion）——裸数字段借用所属完整 token 的量级（40000↔4万亿）
    missing = sorted(
        (x for x in sum_toks
         if x not in body_toks
         and not any(g.startswith(x) and g[len(x):].isalpha() for g in body_toks)
         and (si_val(x) is None or si_val(x) not in body_si)
         and not any(si_val(r) is not None and si_val(r) in body_si and (x in r or r in x)
                     for r in sum_raw)),
        key=lambda x: (len(x), x))
    return missing, summary[:50]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    report = "--report" in sys.argv
    paths = [a for a in sys.argv[1:] if a != "--report"]
    flags = 0
    for pat in paths:
        for p in (glob.glob(pat) if any(c in pat for c in "*?[") else [pat]):
            missing, info = check(p, report=report)
            if missing is None:
                print("SKIP   %s（%s）" % (p, info))
            elif not missing:
                print("OK     %s" % p)
            else:
                flags += 1
                print("CHECK  %s" % p)
                print("       摘要数字待人工核对：%s ｜ 摘要首 50 字：%s" % ("、".join(missing), info))
                print("       判读：编造/换算错→必须修；约数改写、补背景数、SI 换算→放行（清单制，非阻断）")
    sys.exit(1 if flags else 0)


if __name__ == "__main__":
    main()
