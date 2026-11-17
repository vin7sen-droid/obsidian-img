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
    if re.match(r"^([ ]{4,}|\t)", s):     # 只认 ASCII 缩进：全角空格是楼中楼缩进，不是代码
        return True
    if re.match(r"^\S+=\S", s):          # KEY=value 赋值行
        return True
    if len(s) > 160:                       # 长段落不判代码（评论长句常带括号被误伤）
        return False
    n = sum(1 for c in s if c in _SYM)
    return n >= 4 and n / max(len(s), 1) >= 0.10


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


MAXLEN = 320            # 单行上限：引擎对 500+ 字符的单行常整行不翻（实测 530 字符直接放弃）


def _split_long(s):
    if len(s) <= MAXLEN:
        return [s]
    parts, cur = [], ""
    for piece in re.split(r"(?<=[.!?;:。！？；：])\s*", s):
        if not piece:
            continue
        if cur and len(cur) + len(piece) > MAXLEN:
            parts.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        parts.append(cur)
    return parts


def translate_lines(lines):
    """逐行翻译；超长行先按句切成多段再翻（引擎对长单行会整行放弃），翻完拼回。"""
    if not lines:
        return []
    owner, expanded = [], []
    for i, ln in enumerate(lines):
        for piece in _split_long(ln):
            owner.append(i)
            expanded.append(piece)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("\n".join(expanded))
        src = f.name
    try:
        out = subprocess.run(["node", ZH, "-f", src], capture_output=True, text=True,
                             timeout=3600)
        if out.returncode != 0:
            sys.exit("zh.mjs 失败：%s" % (out.stderr or "")[-400:])
        zh = out.stdout.split("\n")
    finally:
        os.unlink(src)
    if len(zh) < len(expanded):
        zh += [""] * (len(expanded) - len(zh))
    merged = [""] * len(lines)
    for k, i in enumerate(owner):
        merged[i] += zh[k]
    return [_postfix(x) for x in merged]


def _postfix(s):
    """引擎高频小错统一修（只改确定性的，不动语义）。"""
    s = re.sub(r"(\d)百分比", r"\1%", s)               # 37.92百分比 → 37.92%
    s = s.replace("蒙特卡罗", "蒙特卡洛")
    s = re.sub(r"(\d{2})：(\d{2})：(\d{2})", r"\1:\2:\3", s)   # 时间里的全角冒号
    return s


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
