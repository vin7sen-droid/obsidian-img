#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""markdown 感知的 zh.mjs 翻译包装（2026-11-17）

zh.mjs 是逐行翻译器，直接喂 markdown 会踩三个坑：
  1. frontmatter 的 created/url/tags 键被翻成中文 → Obsidian 认不出；
  2. ``` 围栏代码块与缩进代码块里的 $(...)、|、{...} 被全角化 → 代码坏掉；
  3. `# 标题` 变成 `#标题`（井号后空格被吃）→ markdown 不渲染成标题。
本脚本把这三类保护起来，只把真正的散文交给 zh.mjs。

用法：
  python3 mdzh.py <in.md> [-o out.md] [--no-frontmatter]
  默认输出到 stdout。
"""
import argparse, os, re, subprocess, sys, tempfile

ZH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zh.mjs")


def split_fm(text):
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return (m.group(0), text[m.end():]) if m else ("", text)


_SYM = set("(){}$|;<>=&\\")


def is_code_line(s):
    """缩进块 + 「符号密度高」的裸代码行（邮件/技术稿里的 shell 片段常顶格写）。"""
    if not s.strip():
        return False
    if re.match(r"^(\s{4,}|\t)", s):
        return True
    if re.match(r"^\S+=\S", s):          # KEY=value 赋值行
        return True
    n = sum(1 for c in s if c in _SYM)
    return n >= 3 and n / max(len(s), 1) >= 0.08


def protect(lines):
    """返回 (mask, to_translate_text)。mask[i]=True 表示该行原样保留。"""
    mask = [False] * len(lines)
    fence = False
    for i, ln in enumerate(lines):
        st = ln.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
            mask[i] = True
            continue
        if fence or is_code_line(ln):
            mask[i] = True
    return mask


def fix_headings(line):
    """`#标题` → `# 标题`（zh.mjs 会吃掉井号后的空格）。"""
    m = re.match(r"^(#{1,6})([^\s#].*)$", line)
    return "%s %s" % (m.group(1), m.group(2)) if m else line


def translate_lines(lines):
    if not lines:
        return []
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        src = f.name
    try:
        out = subprocess.run(["node", ZH, "-f", src], capture_output=True, text=True,
                             timeout=3600)
        if out.returncode != 0:
            sys.exit("zh.mjs 失败：%s" % (out.stderr or "")[-400:])
        zh = out.stdout.split("\n")
    finally:
        os.unlink(src)
    if len(zh) < len(lines):
        zh += [""] * (len(lines) - len(zh))
    return zh[:len(lines)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--no-frontmatter", action="store_true")
    a = ap.parse_args()

    text = open(a.src, encoding="utf-8").read()
    fm, rest = ("", text) if a.no_frontmatter else split_fm(text)
    lines = rest.split("\n")
    mask = protect(lines)

    idx = [i for i, m in enumerate(mask) if not m and lines[i].strip()]
    body = [lines[i] for i in idx]
    zh = translate_lines(body)
    for i, t in zip(idx, zh):
        lines[i] = fix_headings(t)

    print((fm + "\n".join(lines)).rstrip() + "\n", end="")


if __name__ == "__main__":
    main()
