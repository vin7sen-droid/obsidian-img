#!/usr/bin/env python3
# 摘要数字机检（absnumcheck）：摘要 vs 正文（同文档内部）的数字一致性比对，抓
# 「约数被合理化成精确数」（over six million→「650 万」）这类人工回源最容易漏的错。
# 设计定案（2026-11-16 用户借鉴定）：
#   ① 只出清单，不当阻断闸——摘要是作者改写，允许引入正文没有的背景数，flag 由人 30 秒过；
#   ② SI 归一判定（复用 xcheck numVerdict 思路）——「摘要 130 亿 vs 正文 $13 billion」合法换算放行；
#   ③ 跨语言——中文摘要 vs 英文原文也支持（英文数词 six million → 6e6）。
# 用法：python3 absnumcheck.py <摘要文本文件> <正文文本文件>   （通用模式）
#       python3 absnumcheck.py --md <笔记.md>                  （剪藏线：自动抽「摘要：」行 vs 其余正文）
# 输出 JSON：{flags:[{num,si,hint}], checked}；flags 为空 = 摘要数字全部可溯源。
import re, sys, json, unicodedata

# —— SI 归一 ——
CN_SCALE = {"万亿": 1e12, "亿": 1e8, "万": 1e4}
EN_WORD = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,
           "ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,
           "sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,
           "thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}
EN_SCALE = {"hundred":1e2,"thousand":1e3,"million":1e6,"billion":1e9,"trillion":1e12}

def si_values(text):
    """从文本提取数字并归一到 SI 数值集合；返回 {si值: 原文片段}"""
    out = {}
    t = unicodedata.normalize("NFKC", text).replace(",", "").replace("，", "")
    covered = []   # 已被量级归一覆盖的原文片段（裸数字扫描时跳过，防「650 万」同时出裸 650）
    # 1) 中文：数字+万亿/亿/万
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(万亿|亿|万)", t):
        out[round(float(m.group(1)) * CN_SCALE[m.group(2)], 4)] = m.group(0)
        covered.append(m.group(1))
    # 2) 英文：数词/阿拉伯数字+量级词（six million / 6 million / $13 billion）
    for m in re.finditer(r"([A-Za-z]+|\d+(?:\.\d+)?)\s+(trillion|billion|million|thousand)\b", t, re.I):
        w = m.group(1).lower()
        n = EN_WORD.get(w) if w in EN_WORD else (float(w) if w.replace(".", "").isdigit() else None)
        if n is not None:
            out[round(n * EN_SCALE[m.group(2).lower()], 4)] = m.group(0)
            covered.append(m.group(1))
    # 3) 英文数词裸形态（four weeks / five researchers——正文用英文数词表达时摘要的阿拉伯数字才有对应）
    for m in re.finditer(r"\b(" + "|".join(EN_WORD) + r")\b", t, re.I):
        out[round(float(EN_WORD[m.group(1).lower()]), 4)] = m.group(0)
    # 4) 裸数字（含 %/版本/年份）——按原值；被量级归一覆盖的跳过
    for m in re.finditer(r"\d+(?:\.\d+)?", t):
        if m.group(0) in covered:
            continue
        v = round(float(m.group(0)), 4)
        if v not in out:
            out[v] = m.group(0)
    return out

def clean_body(text):
    """剥图片行/URL/表格/代码围栏/frontmatter——同文档内部对比的主要噪音源"""
    lines, keep = text.split("\n"), []
    in_code = in_fm = False
    for ln in lines:
        if ln.strip() == "---" and len(keep) == 0 and not in_code: in_fm = not in_fm; continue
        if in_fm:
            if ln.startswith(("created:", "source:", "published:", "author:", "title:")): continue
            else: in_fm = False
        if ln.strip().startswith("```"): in_code = not in_code; continue
        if in_code: continue
        if re.match(r"^\s*(!\[|\|)", ln): continue          # 图片行 / 表格行
        ln = re.sub(r"https?://\S+", " LINK ", ln)           # 行内 URL
        ln = re.sub(r"\[[^\]]*\]\([^)]*\)", " LINK ", ln)    # markdown 链接
        keep.append(ln)
    return "\n".join(keep)

def extract_summary(md):
    """剪藏笔记抽「摘要：」行（兼容列表式 - **[标题](url)** + 缩进摘要行的日报条目）"""
    for ln in md.split("\n"):
        s = ln.strip()
        if s.startswith("摘要：") or s.startswith("摘要:"):
            return s[3:].strip()
    return None

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    if sys.argv[1] == "--md":
        md = open(sys.argv[2], encoding="utf-8").read()
        summ = extract_summary(md)
        if not summ: print(json.dumps({"error": "未找到「摘要：」行"}, ensure_ascii=False)); sys.exit(1)
        body = "\n".join(l for l in md.split("\n") if not l.strip().startswith("摘要"))
    else:
        summ = open(sys.argv[1], encoding="utf-8").read()
        body = open(sys.argv[2], encoding="utf-8").read()
    sm, bm = si_values(summ), si_values(clean_body(body))
    flags = []
    for v, frag in sorted(sm.items()):
        if v in bm: continue
        flags.append({"num": frag, "si": v, "hint": "摘要数字在正文无 SI 归一对应（约数合理化/背景数，人工 30 秒判）"})
    print(json.dumps({"checked": len(sm), "flags": flags, "flags_total": len(flags)}, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
