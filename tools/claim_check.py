#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claim_check.py —— 摘要「断言越界」机检（清单制，不阻断）

针对日报摘要三类反复出现的错误，只看「摘要说了原文没说的断言」：
  R1 疑问/存疑标题被写成陈述（原文标题带 ？/疑/传/恐/是否，摘要是断言）
  R2 强断言算子越界（摘要含 呼吁/建议/意味着/表明/被解读为… 而原文没有该词）
  R3 归因主体凭空出现（摘要「X 警告/称/表示」，X 不在原文）

用法：
  python3 claim_check.py <md> --pool <pool.json|raw.json> [--pool ...] [--out <清单.md>]

设计：清单制非阻断（摘要是作者改写，允许合理概括），flags 由人 30 秒判。
只看中文源的可判项——英文原文与中文摘要无词面对应，只跑 R1（有中文标题的除外）。
"""
import json
import re
import sys

# 强断言算子：出现即意味着「原文说过这件事」，原文查不到就要人看一眼
# 只收「评价性/断言性」算子——逻辑连接词（因此/从而/说明/表明/意味着/暗示/反映出）
# 噪音过高（原文常有此意只是词不同），已剔除，见 2026-11-17 5 期回归
OPERATORS = [
    "呼吁", "建议", "应当", "必须", "联手", "被解读为", "被视为", "被认为",
    "标志着", "凸显", "恐将", "或将", "将导致", "倒逼", "抢攻", "切入了",
]
# 归因型表述：摘要自带信息出处，此时标题存疑也算正确处理
RELIABLE_ATTR = ["文件显示", "记录显示", "据文件", "披露", "登记记录", "公开记录", "报道称"]
# 对冲/限定词：摘要里出现这些就不算把疑问写成断言
HEDGES = ["可能", "或", "疑似", "据传", "据称", "传闻", "尚未", "是否", "?", "？",
          "预计", "据报", "有望", "外界", "被解读", "被视", "传闻", "存疑", "待确认"]
HEDGE_TITLE = re.compile(r"[?？]|是否|涉嫌|疑为|恐将|或将|传闻|据传|据悉|酝酿|猜测")
ATTRIB = re.compile(r"([\u4e00-\u9fa5A-Z][\u4e00-\u9fa5A-Za-z·]{2,10})"
                    r"(警告|表示|指出|宣布|透露|认为|声称|称)")
# 泛指归因主体：本身就是新闻叙述的一部分，不算凭空主体
GENERIC = {"消息", "官方", "同时", "报道", "外界", "该公司", "公司", "团队", "研究",
           "声明", "公告", "文件", "报告", "最新", "随后", "此前", "另据", "分析",
           # 描述性短语，不是可核查的主体
           "虚拟人物", "人物", "视频", "图片", "画面", "网友", "用户", "客户",
           "买家", "卖家", "平台", "员工", "读者"}
ITEM = re.compile(r"^(\d+)\.\s+\*\*\[(.+?)\]\((.+?)\)\*\*\s*$")
CJK = re.compile(r"[\u4e00-\u9fa5]")


def norm_url(u):
    return re.sub(r"[#?].*$", "", (u or "")).replace("http://", "https://").strip().rstrip("/")


def load_pool(paths):
    idx = {}
    for p in paths:
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print("  跳过 %s：%s" % (p, e), file=sys.stderr)
            continue
        for it in data if isinstance(data, list) else data.get("items", []):
            text = " ".join(str(it.get(k) or "") for k in ("title", "desc", "summary"))
            if len(CJK.findall(text)) > 5:
                idx.setdefault(norm_url(it.get("url")), text)
    return idx


def parse_md(path):
    """返回 [(no, title, url, summary)]"""
    out, cur = [], None
    for line in open(path, encoding="utf-8"):
        m = ITEM.match(line.rstrip("\n"))
        if m:
            if cur:
                out.append(cur)
            cur = [int(m.group(1)), m.group(2), m.group(3), ""]
            continue
        if cur and line.startswith("   "):
            s = line.strip()
            if s and "http" not in s and not s.startswith("（"):
                cur[3] += s
        elif cur:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def check(items, pool):
    flags = []
    matched = 0
    for no, title, url, summary in items:
        plain = summary.replace("**", "")
        src = pool.get(norm_url(url))
        if not src:
            continue  # 找不到原文就不猜，留给人工
        matched += 1
        if (HEDGE_TITLE.search(title)
                and not any(h in plain for h in HEDGES)
                and not any(a in plain for a in RELIABLE_ATTR)):
            flags.append((no, "R1 疑问标题写成断言", title, "原文标题存疑，摘要为确定表述"))
        hit = [op for op in OPERATORS if op in plain and op not in src]
        if hit:
            flags.append((no, "R2 断言算子越界", title,
                          "摘要用了 %s，原文未出现" % "、".join(hit[:3])))
        for m in ATTRIB.finditer(plain):
            who = m.group(1)
            if any(c in who for c in "的，、"):
                who = re.split(r"[的，、]", who)[-1]  # 「伟达三十余年的黄仁勋」→ 取末段
            who = re.sub(r"(曾|还|也|已|本周|上月|去年|今年|此前|随后|同时)$", "", who)
            if len(who) < 2 or any(w in who for w in GENERIC):
                continue  # 泛指/描述性主体（「视频中虚拟人物」「官方」）不是可核查对象
            if who[0] in "月日年时周" or "月" in who[:2]:
                continue  # 「6 月发布时称」这类时间状语，不是主体
            if who in src:
                continue
            tail = who[-2:] if len(who) > 2 else who
            if tail in src:
                continue
            flags.append((no, "R3 归因主体缺失", title,
                          "「%s%s」——原文找不到该主体" % (who, m.group(2))))
            break
    return flags, matched


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 2
    md = sys.argv[1]
    pools = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--pool" and i + 1 < len(sys.argv)]
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    items = parse_md(md)
    pool = load_pool(pools)
    flags, matched = check(items, pool)
    print("条目 %d · 命中原文 %d 条（池 %d）· flags %d"
          % (len(items), matched, len(pool), len(flags)))
    lines = ["# 摘要断言机检 · %s" % md.split("/")[-1], ""]
    seen = set()
    for no, kind, title, why in flags:
        if no in seen:
            continue
        seen.add(no)
        row = "- **#%d** %s｜%s｜%s" % (no, title, why, kind)
        print(row)
        lines.append(row)
    if out:
        open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print("清单 ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())