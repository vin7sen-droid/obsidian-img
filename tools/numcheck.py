#!/usr/bin/env python3
# 数字回源校验 v2：① 数字 token 集合比对（漏译/编造）② billion/million/trillion 量级换算检测
# 用法：python3 numcheck.py <原文.txt> <译文.txt>
# 量级规则：N billion → 预期译文含 N*10（亿）或 N*1000（万）；N million → N（百万）或 N*100（万）；
#          N trillion → N（万亿）。译文数字 token 不在候选集 → scale_risk（量级错/换算错）。
import re, sys, json
from collections import Counter

NUM_TOKEN = re.compile(r"\$?\d[\d,.]*%?")
SCALE = re.compile(r"(\$?)(\d[\d,.]*)\s*(trillion|billion|million)", re.I)

def tokens(t):
    return Counter(x.replace(",", "") for x in NUM_TOKEN.findall(t))

def scale_pairs(t):
    out=[]
    for m in SCALE.finditer(t):
        n=float(m.group(2).replace(",","")); unit=m.group(3).lower()
        if unit=="billion": cand=[n*10, n*1000]      # 亿 / 万
        elif unit=="million": cand=[n, n*100]        # 百万 / 万
        else: cand=[n, n*10000]                      # 万亿 / 亿
        out.append((m.group(0), [round(c,4) for c in cand]))
    return out

src, dst = open(sys.argv[1], encoding="utf-8").read(), open(sys.argv[2], encoding="utf-8").read()
a, b = tokens(src), tokens(dst)
missing = a - b
extra = b - a
zh_nums={x.replace(",","") for x in tokens(dst)}
def fmt(c):
    return str(int(c)) if float(c)==int(c) else str(c)
scale_risk=[]
for frag,cand in scale_pairs(src):
    hit=any(fmt(c) in zh_nums for c in cand)
    if not hit: scale_risk.append({"src":frag,"expect_zh":cand})
rpt = {
    "src_num_tokens": sum(a.values()),
    "zh_num_tokens": sum(b.values()),
    "missing": dict(missing),
    "missing_total": sum(missing.values()),
    "extra": dict(extra),
    "extra_total": sum(extra.values()),
    "scale_risk": scale_risk,
    "scale_risk_total": len(scale_risk),
}
print(json.dumps(rpt, ensure_ascii=False, indent=1))
