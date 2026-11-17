#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按楼层挑选翻译 Obsidian 剪藏笔记的评论区 + 产出 xcheck 校验清单（2026-11-17）

用法：
  python3 note_zh.py <note.md> --pick depth0            # 只译主楼（0 缩进的楼层）
  python3 note_zh.py <note.md> --pick first:30          # 前 30 条（含楼中楼）
  python3 note_zh.py <note.md> --pick author:foo        # 指定作者
  python3 note_zh.py <note.md> --pick all --body        # 全部 + 正文
  -o <out.md>      输出路径（默认 <note>.zh.md）
  --xcheck         翻完跑交叉校验，清单落 digests/<日期>_评论翻译人工审.md

保护规则、标题修空格等复用 mdzh.py。
"""
import argparse, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdzh

TOOLS = os.path.dirname(os.path.abspath(__file__))
# 条目头：`**作者 · 2026-11-17**`（HN）或 `**作者 · 2026-11-17 · 61 赞**`（Reddit，赞数可负）
HEAD = re.compile(r"^(　*)\*\*(?:↳\d+\s+)?(.+?)\s·\s(\d{4}-\d{2}-\d{2})(?:\s·\s-?\d+\s赞)?\*\*$")


def split_note(text):
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    fm = m.group(0) if m else ""
    rest = text[len(fm):]
    i = rest.find("\n---\n\n## 评论区")
    if i < 0:
        return fm, rest, ""
    return fm, rest[:i], rest[i:]


def parse_entries(sec):
    """评论区 → [{head_idx, end_idx, depth, author, lines}]（end 不含）。"""
    lines = sec.split("\n")
    heads = []
    for i, ln in enumerate(lines):
        m = HEAD.match(ln)
        if m:
            depth = len(m.group(1)) // 2
            heads.append((i, depth, m.group(2).strip()))
    out = []
    for k, (i, d, a) in enumerate(heads):
        endl = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        out.append({"start": i, "end": endl, "depth": d, "author": a})
    return lines, out


def select(entries, pick):
    if pick in ("all", "", None):
        return list(range(len(entries)))
    if pick == "depth0":
        return [i for i, e in enumerate(entries) if e["depth"] == 0]
    m = re.match(r"first:(\d+)$", pick)
    if m:
        return list(range(min(int(m.group(1)), len(entries))))
    m = re.match(r"depth:(\d+)$", pick)
    if m:
        return [i for i, e in enumerate(entries) if e["depth"] == int(m.group(1))]
    m = re.match(r"author:(.+)$", pick)
    if m:
        return [i for i, e in enumerate(entries) if m.group(1).lower() in e["author"].lower()]
    if pick == "has-kids":
        return [i for i, e in enumerate(entries) if e["depth"] == 0]
    sys.exit("不认识的 --pick：%s" % pick)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("note")
    ap.add_argument("--pick", default="depth0")
    ap.add_argument("-o", "--out")
    ap.add_argument("--body", action="store_true", help="连正文一起翻")
    ap.add_argument("--xcheck", action="store_true")
    a = ap.parse_args()

    text = open(a.note, encoding="utf-8").read()
    fm, body, sec = split_note(text)
    lines, entries = parse_entries(sec)
    idxs = select(entries, a.pick) if entries else []
    if not entries and not a.body:
        sys.exit("这篇没有评论区（用 --body 只翻正文）")

    # 收集待翻行（正文 + 选中楼层），统一送 zh.mjs，保持行数守恒
    jobs = []          # (容器标识, 行号)
    if a.body:
        for i, ln in enumerate(body.split("\n")):
            if ln.strip():
                jobs.append(("body", i))
    for ei in idxs:
        e = entries[ei]
        for i in range(e["start"] + 1, e["end"]):     # 头行不动（作者/日期）
            if lines[i].strip():
                jobs.append(("c", i))

    raw = [body.split("\n")[i] if c == "body" else lines[i] for c, i in jobs]
    pads = [re.match(r"^[　 \t]*", x).group(0) for x in raw]      # 行首缩进（楼中楼全角空格）
    src = [x[len(pd):] for x, pd in zip(raw, pads)]
    mask = mdzh.protect(src)
    keep = [j for j, m in enumerate(mask) if not m]
    zh = mdzh.translate_lines([src[j] for j in keep])

    body_lines = body.split("\n")
    pairs = []
    for j, t in zip(keep, zh):
        c, i = jobs[j]
        t = pads[j] + mdzh.fix_headings(t)
        if c == "body":
            body_lines[i] = t
        else:
            lines[i] = t
        pairs.append({"en": src[j], "zh": t, "name": "%s@%d" % (c, i)})

    out = fm + "\n".join(body_lines)
    if sec:
        out = out.rstrip("\n") + "\n" + "\n".join(lines)
    out = out.replace("<!-- 待精修：英文原文，请全文译成中文 -->\n", "")
    dst = a.out or (os.path.splitext(a.note)[0] + ".zh.md")
    open(dst, "w", encoding="utf-8").write(out)
    print("译文：%s（翻 %d 行 / %d 条楼层）" % (dst, len(keep), len(idxs)))

    if a.xcheck and pairs:
        # pid 隔离：固定文件名会让并发会话互相覆盖（A 的消费者读到 B 的对）
        pj = "/tmp/xcheck_pairs_%d.json" % os.getpid()
        try:
            with open(pj, "w", encoding="utf-8") as f:
                json.dump(pairs, f, ensure_ascii=False)
            r = subprocess.run(["node", os.path.join(TOOLS, "inbox_xcheck.mjs"), pj],
                               capture_output=True, text=True, timeout=900)
            sys.stdout.write(r.stdout)
            if r.returncode != 0:
                print("xcheck 失败：%s" % (r.stderr or "")[-300:])
        finally:
            try:
                os.remove(pj)
            except OSError:
                pass


if __name__ == "__main__":
    main()
