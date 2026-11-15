#!/usr/bin/env python3
# 摘要数字机检（摘要 vs raw 原文 title+desc 同事件对比）——只出清单不当阻断闸
# 用法: python3 summary_check.py <workdir> [date]
# 输入: <workdir>/merged.json 优先（{"merged":[...]} 或 [...]），无则合并 json/T*.json；raw_<date>.json 按 url 建索引
# 基准: 每条按 url 回 raw 取原始 title+desc（merged title 是队员改写版会丢/改数字，不作依据）
# 输出: <workdir>/summary_人工审.md + stdout 统计；exit 恒 0（清单制）
# SI 归一复用 xcheck.mjs 的 parseNums/parseZhNums/close（node 子进程批量跑）
import json, sys, os, glob, re, subprocess

ARGS = sys.argv[1:]
MODE = "md" if ARGS and ARGS[0] == "--md" else "workdir"   # md=剪藏笔记「摘要：」行 vs 同文件正文；workdir=日报 items 摘要 vs raw.json 原文
if MODE == "md":
    ARGS = ARGS[1:]
WORK = ARGS[0] if ARGS else "."
DATE = sys.argv[2] if len(sys.argv) > 2 else None
XC   = "/root/workspace/tools/xcheck.mjs"

def load_items():
    p = os.path.join(WORK, "merged.json")
    if os.path.exists(p):
        d = json.load(open(p))
        if isinstance(d, list): return d
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list): return v
        return []
    items = []
    for f in sorted(glob.glob(os.path.join(WORK, "json", "T*.json"))):
        items += json.load(open(f)).get("items", [])
    return items

def raw_index():
    # 优先级：raw_<date>.json（显式指定）> raw.json（daily.py 全量主文件，含雷达盲区源）> raw_*.json（raw_fetch 等旧版备份）
    cands = ([os.path.join(WORK, "raw_%s.json" % DATE)] if DATE else []) + \
            [os.path.join(WORK, "raw.json")] + \
            sorted(glob.glob(os.path.join(WORK, "raw_*.json")), reverse=True)
    idx = {}
    for f in cands:
        if not os.path.exists(f): continue
        for e in json.load(open(f)):
            u = e.get("url") or ""
            if u and u not in idx:
                idx[u] = {"title": e.get("title",""), "desc": (e.get("desc") or "").strip()}
    return idx

NODE = r'''
const { parseNums, parseZhNums } = await import("file:///root/workspace/tools/xcheck.mjs");
let input = ""; process.stdin.on("data", d => input += d);
process.stdin.on("end", () => {
  const pairs = JSON.parse(input);
  const close = (a, b) => {
    if (!isFinite(a) || !isFinite(b)) return false;
    const d = Math.abs(a - b), s = Math.max(Math.abs(a), Math.abs(b), 1);
    return d / s < 0.02 || d < 1e-6;
  };
  // 长度单位预归一成毫米裸数（英寸 25.4 / 英尺 304.8 / 英里 1609344 / 厘米 10 / 毫米 1）
  const LENMAP = {};
  const MM = { "寸":25.4, '"':25.4, '″':25.4, "英寸":25.4, "吋":25.4, "inch":25.4, "inches":25.4, "英尺":304.8, "feet":304.8,
               "foot":304.8, "英里":1609344, "miles":1609344, "mile":1609344,
               "厘米":10, "cm":10, "毫米":1, "mm":1 };
  const W2N = {one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10,eleven:11,twelve:12,thirteen:13,fourteen:14,fifteen:15,sixteen:16,seventeen:17,eighteen:18,nineteen:19,twenty:20,thirty:30,forty:40,fifty:50,sixty:60,seventy:70,eighty:80,ninety:90};
  const clean = t => String(t||"")
    .replace(/\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b/gi, w => " " + W2N[w.toLowerCase()] + " ")
    .replace(/https?:\/\/[^\s)\]]+/g, " ").replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/(\d[\d,]*(?:\.\d+)?)\s*(英寸|吋|寸|inch(?:es)?|"|″|英尺|feet|foot|英里|miles?|厘米|cm|毫米|mm)(?![a-zA-Z\u4e00-\u9fff])/gi,
             (m, n, u) => { const v = parseFloat(n.replace(/,/g,"")) * (MM[u.toLowerCase()] ?? MM[u]);
                            if (!isFinite(v)) return m;
                            const key = String(Math.round(v*100)/100);
                            LENMAP[key] = m.trim();   // 反查表：170.18 → 「6.7"」
                            return " " + key + " "; })
    .replace(/(\d(?:\.\d+)?)[\u2010-\u2015-]?(trillion|billion|million|thousand|crore|lakh)\b/gi, "$1 $2") // 连字符隔断量级词：1.6-trillion→1.6 trillion（防 SI 没乘上去）
    .replace(/\b[xX](86|64)\b/g, " ")              // 架构名 x86/x64
    .replace(/\b[A-Z]{1,3}\d{2,}\b/g, " ")          // 型号 QN10 / KB5132057 / A100
    .replace(/二次开发|二进制|二合一/g, " ")            // 中文习语数字
    ;
  const out = pairs.map(p => {
    // 摘要里每个数字都要能在 raw title+desc 里找到对应（摘要数字 ⊆ 素材数字）
    // 双方都纯数才比数值；任一带单位则比 SI（防「18亿」靠裸 18 冒充 18 crore）
    const S = parseZhNums(clean(p.summary));
    const bases = p.bases.map(d => p.desc_is_zh ? parseZhNums(clean(d))
                                                : [...parseNums(clean(d)), ...parseZhNums(clean(d))]);
    const flags = [];
    for (const s of S) {
      const hit = bases.some(B => B.some(t =>
        (s.si === s.n) ? (!t.unit && close(s.n, t.n)) : (t.unit && close(s.si, t.si))));
      if (!hit) {
        if (s.si === s.n && s.n < 10) continue;          // 小整数豁免：第三方/3 类（带单位的仍查）
        if (s.si % 10000 === 0 &&
            /数[十百]?[万亿]|上百|上千|上万|成千/.test(p.summary))
          continue;                                       // 模糊量词豁免（仅限真模糊基数）：数十万→100000 定点值，raw 常为 hundreds of thousands 文本。
                                                          // 「约30亿/近650万」不豁免——si 是真值，原文可正常命中；对不上正是该 flag 给人工的跨币种/约数合理化判定
        flags.push(String(s.n));
      }
    }
    return { title: p.title, total: S.length, flags: flags.map(f => LENMAP[f] || f),
             bases_n: p.bases.length, missing: p.missing };
  });
  console.log(JSON.stringify(out));
});
'''

def node_check(pairs):
    """统一 node 通道；失败抛 RuntimeError 由调用方按清单制降级（exit 恒 0，不阻断主产物）"""
    try:
        r = subprocess.run(["node", "--input-type=module", "-e", NODE],
                           input=json.dumps(pairs, ensure_ascii=False),
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("node 子进程超时 60s")
    if r.returncode != 0:
        raise RuntimeError("node 失败: " + (r.stderr or "")[-500:])
    return json.loads(r.stdout)

def check_md(paths):
    """--md 模式：每个路径抽「摘要：」行 vs 其余全文（正文剥图片行/表格/URL/代码围栏/frontmatter），
    SI 归一比对（复用 node_check 单对通道，bases=正文）。返回 [{note,total,flags}]。"""
    import re as _re
    rows, pending = [], []
    for path in paths:
        text = open(path, encoding="utf-8").read()
        lines = text.split("\n")
        summ = None
        keep = []
        for ln in lines:
            s2 = ln.strip()
            if summ is None and s2.startswith(("摘要：", "摘要:")):
                summ = s2[3:].strip(); continue
            keep.append(ln)
        if not summ:
            rows.append({"note": os.path.basename(path), "total": 0, "flags": [], "skip": "no 摘要行"})
            continue
        body_lines, in_code, in_fm = [], False, False
        for ln in keep:
            st = ln.strip()
            if st == "---" and not body_lines and not in_code:
                in_fm = not in_fm; continue
            if in_fm:
                if ln.startswith(("created:", "source:", "published:", "author:", "title:", "tags:", "description:")): continue
                else: in_fm = False
            if st.startswith("```"): in_code = not in_code; continue
            if in_code or _re.match(r"^\s*!\[", ln): continue   # 表格数据行保留（是正文基准）；|---| 分隔行无数值无害
            ln = _re.sub(r"https?://\S+", " LINK ", ln)
            ln = _re.sub(r"\[[^\]]*\]\([^)]*\)", " LINK ", ln)
            body_lines.append(ln)
        body = "\n".join(body_lines)
        if not _re.search(r"\d", summ):
            rows.append({"note": os.path.basename(path), "total": 0, "flags": [], "skip": "摘要无数字"})
            continue
        pending.append((len(rows), os.path.basename(path), summ, body))
        rows.append(None)   # 占位，批量 node 后回填
    if pending:
        pairs = [{"title": nt, "summary": sm, "bases": [bd], "desc_is_zh": False, "missing": []} for _, nt, sm, bd in pending]
        try:
            res = node_check(pairs)
        except RuntimeError as e:
            res = [{"title": nt, "flags": ["ERR"], "total": 0, "bases_n": 1, "missing": [],
                    "_err": "机检失败 %s（人工核对数字）" % e} for _, nt, _, _ in pending]
        for (ri, nt, _, _), r in zip(pending, res):
            r["note"] = nt
            rows[ri] = r
    return [r for r in rows if r is not None]


def main():
    if MODE == "md":
        rows = check_md(ARGS)
        flagged = [r for r in rows if r["flags"]]
        out = ["# 摘要数字机检（笔记）· 清单（只提示不阻断）", "",
               "> %d 篇参检 / **flagged %d**" % (len(rows), len(flagged)), ""]
        for r in rows:
            if r["flags"]:
                out.append("- **%s** — 摘要数字 %s 在正文无对应；先判「约数合理化/背景数」，确实无据再修。30 秒人工判。" % (r["note"], r["flags"]))
        if not flagged: out.append("（无 flag，摘要数字全部在正文可对应）")
        out_dir = os.path.dirname(os.path.abspath(ARGS[-1]))
        path = os.path.join(out_dir, "summary_人工审.md")
        open(path, "w").write("\n".join(out) + "\n")
        print("摘要数字机检(笔记): %d 篇 / flagged %d → %s" % (len(rows), len(flagged), path))
        return
    items = load_items()
    if not items:
        print("摘要数字机检: workdir 无 merged.json / json/T*.json，无输入可检 → 跳过")
        return
    idx = raw_index()
    pairs, skipped = [], 0
    for it in items:
        zh_src = bool(re.search(r"[\u4e00-\u9fff]", it.get("title","")))
        if not re.search(r"\d", it.get("summary","")): skipped += 1; continue
        # 基准=raw 原始 title+desc；source url 不在 raw 的记 miss（如抓取后追加的新源）
        bases, missing = [], []
        for u in [it.get("url","")] + [s.get("url","") for s in it.get("sources", [])]:
            if not u: continue
            r = idx.get(u)
            if r: bases += [r["title"], r["desc"]]
            elif u not in missing: missing.append(u)
        if not bases: skipped += 1; continue
        pairs.append({"title": it.get("title",""), "summary": it.get("summary",""),
                      "bases": bases, "desc_is_zh": zh_src, "missing": missing})
    res = node_check(pairs)
    flagged = [r for r in res if r["flags"]]
    out = ["# 摘要数字机检 · 人工审清单（只提示不阻断）", "",
           "> %d 条参检 / %d 条跳过（无 raw 原文或摘要无数字）/ **flagged %d**" % (len(res), skipped, len(flagged)), ""]
    for r in flagged:
        note = "；⚠️ 源 desc 不在 raw（抓取后追加的新源），无法对照" if r.get("missing") else ""
        out.append("- **%s** — 摘要数字 %s（共 %d 个数字）在 %d 个来源（raw title+desc）中无对应%s；先判「补背景数/约数合理化/跨币种换算」，确实无据再修。" % (
            r["title"], r["flags"], r["total"], r["bases_n"], note))
    if not flagged: out.append("（无 flag，全部数字在来源原文中可对应）")
    path = os.path.join(WORK, "summary_人工审.md")
    open(path, "w").write("\n".join(out) + "\n")
    print("摘要数字机检: 参检 %d / 跳过 %d / flagged %d → %s" % (len(res), skipped, len(flagged), path))

if __name__ == "__main__":
    main()
