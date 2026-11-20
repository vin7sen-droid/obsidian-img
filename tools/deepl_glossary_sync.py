#!/usr/bin/env python3
"""把 translate.mjs 的 IDIOM_ZH 定译表同步进 DeepL glossary（2026-11-20）。
用法: python3 tools/deepl_glossary_sync.py [--dry]
流程: node 抽 IDIOM_ZH（未导出，从源码 eval 数组段）→ 正则字面化（\\s+/字符类→空格、尾 s? 变体、
      剥 \\b/(?:) 壳；残留元字符跳过并报告）→ 合并 deepl.py 静态 ENTRIES → DELETE+POST 重建 glossary。
以后 translate.mjs 习语表更新后重跑本脚本即可；glossary 大小写不敏感，i 标志无需处理。
"""
import json, os, re, subprocess, sys, urllib.request

KEY = os.environ.get("DEEPL_API_KEY") or open("/root/.config/deepl/api_key").read().strip()
BASE = "https://api-free.deepl.com/v2"
NAME = "clip-terms"
HERE = os.path.dirname(os.path.abspath(__file__))

# 静态术语（与 deepl.py ENTRIES 保持一致）
STATIC = [
    ("agent", "agent"), ("SDR", "SDR"), ("ARR", "ARR"), ("GTM", "GTM"),
    ("token", "token"), ("issue tracker", "问题跟踪器"), ("Linear", "Linear"),
    ("presales", "presales"), ("outbound", "外呼拓客"),
    ("paid pilots", "付费试点"), ("paid pilot", "付费试点"),
]


def req(path, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": "DeepL-Auth-Key " + KEY, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else None


def extract_idioms():
    js = r'''
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const m = src.match(/const IDIOM_ZH = \[([\s\S]*?)\n\];/);
if (!m) { console.error("IDIOM_ZH 段未找到"); process.exit(1); }
const arr = eval("[" + m[1] + "]");
const out = arr.map(([re, zh]) => [re.source, zh]);
console.log(JSON.stringify(out));
'''
    p = subprocess.run(["node", "-e", js, os.path.join(HERE, "translate.mjs")],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        sys.stderr.write(p.stderr[-500:] + "\n")
        sys.exit(1)
    return json.loads(p.stdout)


def literalize(src):
    """正则 source → 字面短语；不可字面化返回 None。"""
    s = src
    s = re.sub(r"\\b", "", s)                     # 词边界锚删除
    s = re.sub(r"\(\?:", "(", s)                  # 非捕获组壳
    if s.startswith("(") and s.endswith(")") and "(" not in s[1:-1]:
        s = s[1:-1]
    s = re.sub(r"\[\s*-\\s\s*\]|\[\\s?-\s?\]|\[-\\s\]", " ", s)   # [-\s] 字符类 → 空格
    s = s.replace("\\s+", " ").replace("\\s", " ")
    s = s.replace("\\-", "-").replace("\\ ", " ")
    variants = []
    if s.endswith("s?"):                          # 尾部可选复数 → 两个变体
        base = s[:-2]
        variants = [base, base + "s"]
    elif s.endswith("es?"):
        base = s[:-3]
        variants = [base, base + "es"]
    else:
        variants = [s]
    out = []
    for v in variants:
        v = v.strip()
        if not v or re.search(r"[\\^$.*+?\[\]{}()|]", v):   # 残留元字符 → 不可字面化
            continue
        if len(v) < 2:
            continue
        out.append(v)
    return out


def main():
    dry = "--dry" in sys.argv
    idioms = extract_idioms()
    pairs, seen, skipped = list(STATIC), set(), []
    for src, zh in idioms:
        zh = zh.strip()
        if not zh:
            continue
        for lit in literalize(src):
            k = lit.lower()
            if k in seen:
                continue
            seen.add(k)
            pairs.append((lit, zh))
    lits_all = [literalize(s) for s, _ in idioms]
    n_skip = sum(1 for x in lits_all if not x)
    print("定译条目: 静态 %d + 习语字面化 %d（跳过 %d 条含正则元字符）"
          % (len(STATIC), len(pairs) - len(STATIC), n_skip))
    if dry:
        for lit, zh in pairs[:12]:
            print("  %s → %s" % (lit, zh))
        print("…（--dry 不落盘）")
        return
    tsv = "\n".join("%s\t%s" % (a, b) for a, b in pairs)
    # 删旧建新（glossary 无 update 接口；DELETE 204 空体正常）
    for g in (req("/glossaries") or {}).get("glossaries", []):
        if g.get("name") == NAME:
            try:
                req("/glossaries/" + g["glossary_id"], "DELETE")
            except Exception:
                pass
    g = req("/glossaries", "POST", {"name": NAME, "source_lang": "en",
                                    "target_lang": "zh", "entries": tsv,
                                    "entries_format": "tsv"})
    print("glossary 已重建:", g["glossary_id"], "条目", g["entry_count"])
    # 自检两条
    for probe in ["This move is doubling down on AI agents.",
                  "Their API is a blessing in disguise for token costs."]:
        d = req("/translate", "POST", {"text": [probe], "target_lang": "ZH",
                                       "source_lang": "en", "glossary_id": g["glossary_id"]})
        print("自检:", d["translations"][0]["text"])


if __name__ == "__main__":
    main()
