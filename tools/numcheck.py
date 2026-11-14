#!/usr/bin/env python3
# 数字回源校验：比对原文与译文的数字 token 集合差异（漏译的数字 / 译文多出的数字）
# 用法：python3 numcheck.py <原文.txt> <译文.txt>
# 说明：千分位逗号归一化后比对；「billion/million 换算成亿/万」属预期人工换算，
#       报告里的差值需要人工判读（换算类 vs 真错误）。
import re, sys, json
from collections import Counter

NUM_TOKEN = re.compile(r"\$?\d[\d,.]*%?")

def tokens(t):
    return Counter(x.replace(",", "") for x in NUM_TOKEN.findall(t))

src, dst = open(sys.argv[1], encoding="utf-8").read(), open(sys.argv[2], encoding="utf-8").read()
a, b = tokens(src), tokens(dst)
missing = a - b   # 原文有、译文无 → 疑似漏译
extra = b - a     # 译文有、原文无 → 疑似编造/换算
rpt = {
    "src_num_tokens": sum(a.values()),
    "zh_num_tokens": sum(b.values()),
    "missing": dict(missing),
    "missing_total": sum(missing.values()),
    "extra": dict(extra),
    "extra_total": sum(extra.values()),
}
print(json.dumps(rpt, ensure_ascii=False, indent=1))
