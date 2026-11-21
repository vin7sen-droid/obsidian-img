#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""radar_fixzh.py —— 按语义初筛清单批量重写中文标题（LLM 出稿 + 人工抽查）

读 digests/<日期>_semantic_人工审.md（radar_semantic.py 产物），把 flagged 条目
（英文原文 + 错误译文 + 错因）送 LLM 重写中文标题，产出修正映射 JSON：

    python3 radar_fixzh.py <清单.md>            # → <清单>_fixed.json  [{idx,en,zh_old,zh_fixed}]
    python3 radar_fixzh.py <清单.md> --apply    # 直接替换主 md + 速览，并把旧译文写进
                                                # translate-cache 的待删清单（cache_purge.json）

纪律：不改 JSON（数据源）；md 就地改写保 inode；替换前由调用方 cp -p 备份。
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIG = "/root/workspace/digests"
OBS = "/sdcard/Obsidian/dsh/新闻/AI radar"

FIX_PROMPT = """你是中文科技新闻编辑。给定英文原标题、当前机翻中文标题、以及质检指出的问题，
请重写中文标题，要求：
1) 忠实原文：不漏主体、不漏关键金额/数字、不添加原文没有的信息；
2) 符合中文新闻标题习惯：把「XX宣布/推出/收购…」的主谓宾摆顺，不要英文语序直搬；
3) 专名：公司/产品/模型名保留英文（OpenAI、Neon、Muse、Claude Code、Signal、harness、agent、token），
   人名用大陆通行中文译名（Jensen Huang→黄仁勋、Xi→习近平、Gavin Newsom→加文·纽森、
   Sam Altman→萨姆·奥特曼、Dario Amodei→达里奥·阿莫迪）；无人名译名就保留英文；
4) 术语按 IT之家口径：agent 保留英文、token 保留英文、open-weight→开放权重、
   AI Washing→AI 包装、Kill Switch→急停开关、compute crunch→算力紧张、
   attys→律师、Chief Legal Officer→首席法务官；
5) 长度与原标题相当，不要解释、不要加引号、只输出标题本身。

输出 JSON 数组：[{"idx": 序号, "zh": "重写后的标题"}]，每条都要有。只输出 JSON。"""


def _load():
    spec = importlib.util.spec_from_file_location("sc", os.path.join(HERE, "semantic_check.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def parse_list(md_path):
    rows = []
    for l in open(md_path, encoding="utf-8"):
        if not l.startswith("| ") or l.startswith("| #") or l.startswith("|---"):
            continue
        c = [x.strip() for x in l.strip().strip("|").split("|")]
        if len(c) < 5:
            continue
        rows.append({"idx": c[0], "type": c[1], "why": c[2], "zh": c[3], "en": c[4]})
    return rows


def fix_batch(sc, model, rows):
    lines = []
    for r in rows:
        lines.append("序号 %s｜类型 %s｜问题：%s\n  英文：%s\n  现译：%s" %
                     (r["idx"], r["type"], r["why"], r["en"], r["zh"]))
    msgs = [{"role": "system", "content": FIX_PROMPT}, {"role": "user", "content": "\n\n".join(lines)}]
    txt = sc._chat_once(model, msgs, timeout=240)
    m = re.search(r"\[[\s\S]*\]", txt)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    return [o for o in arr if isinstance(o, dict) and o.get("zh")]


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-h"):
        print(__doc__)
        sys.exit(2)
    md_path = sys.argv[1]
    rows = parse_list(md_path)
    if not rows:
        print("清单为空")
        sys.exit(0)
    fx = md_path.replace(".md", "_fixed.json")
    # 已有修正且覆盖本轮清单的 idx 时直接复用，不重跑 LLM（LLM 会抖、失败会白等一轮）
    if os.path.exists(fx) and "--force" not in sys.argv:
        try:
            prev = json.load(open(fx, encoding="utf-8"))
            have = {str(r.get("idx")) for r in prev}
            need = {r["idx"] for r in rows}
            if prev and need <= have:
                print("[fix] 复用已有修正 %d 条 → %s（不重跑 LLM；要重跑加 --force）" % (len(prev), fx))
                out = prev
                rows = []  # 跳过下面的 LLM 循环
        except Exception as e:
            print("[fix] 既有 fixed.json 不可用（%s），重跑 LLM" % str(e)[:60])
    sc = _load() if rows else None
    model = sc.MODELS[0] if sc else ""
    B = 10
    out = out if not rows else []
    for i in range(0, len(rows), B):
        chunk = rows[i:i + B]
        got = fix_batch(sc, model, chunk)
        by = {str(g.get("idx")): g["zh"].strip() for g in got}
        miss = 0
        for r in chunk:
            z = by.get(r["idx"])
            if z:
                out.append({"idx": r["idx"], "en": r["en"], "zh_old": r["zh"], "zh_fixed": z})
            else:
                miss += 1
        print("[fix] 批 %d/%d：%d 条，重写 %d，漏 %d" %
              (i // B + 1, (len(rows) + B - 1) // B, len(chunk), len(chunk) - miss, miss))
    json.dump(out, open(fx, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[fix] 共 %d 条 → %s" % (len(out), fx))

    if "--apply" in sys.argv:
        day = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(md_path))
        day = day.group(1) if day else ""
        targets = [os.path.join(OBS, "AI圈新闻雷达_%s.md" % day),
                   os.path.join(OBS, "AI圈新闻雷达_%s_速览.md" % day)]
        applied = 0
        for t in targets:
            if not os.path.exists(t):
                continue
            s = open(t, encoding="utf-8").read()
            for rec in out:
                old, new = rec["zh_old"], rec["zh_fixed"]
                if old and old != new and old in s:
                    s = s.replace(old, new, 1)
                    applied += 1
            open(t, "w", encoding="utf-8").write(s)  # 同路径写入，保 inode
        print("[fix] 已替换 %d 处 → %s" % (applied, " / ".join(targets)))
        # 旧译文写待删清单，供清缓存用
        purge = os.path.join(DIG, "cache_purge.json")
        json.dump([r["zh_old"] for r in out], open(purge, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[fix] 旧译文 %d 条 → %s（清缓存用）" % (len(out), purge))


if __name__ == "__main__":
    main()