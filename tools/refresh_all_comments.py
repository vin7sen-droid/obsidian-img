#!/usr/bin/env python3
"""批量刷新收集箱里所有 IT之家 笔记的评论区。

用法: python3 tools/refresh_all_comments.py [--dry]
输出: 每篇的 旧条数 → 新条数
"""
import os, re, subprocess, sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

INBOX = "/sdcard/Obsidian/dsh/ReadItLater Inbox"
CLIP = "/root/workspace/tools/clip.py"
PY = "/usr/local/bin/python3"


MAX_AGE_HOURS = 24


def _published(text):
    """从 `原文：URL ｜ 2026/11/7 19:07:37 ｜ 作者` 里取发布日期。"""
    m = re.search(r"原文：\S+\s*｜\s*(\d{4})/(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))
    except ValueError:
        return None


def notes(max_hours=None):
    max_hours = MAX_AGE_HOURS if max_hours is None else max_hours
    out = []
    now = datetime.now()
    for f in sorted(os.listdir(INBOX)):
        if not f.endswith(".md"):
            continue
        s = open(os.path.join(INBOX, f), encoding="utf-8").read()
        # 2026-11-13 frontmatter 化后原文行已删：优先读 frontmatter url，回落正文原文行
        m = re.search(r"^url:\s*(https://www\.ithome\.com/\S+)", s, re.M) \
            or re.search(r"^原文：(https://www\.ithome\.com/\S+)", s, re.M)
        if not m:
            continue
        old = re.search(r"## 评论区（(\d+) 条）", s)
        # 发布时间：frontmatter created（可能带 T 时分秒）优先，回落原文行日期
        cm = re.search(r"^created:\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?", s, re.M)
        if cm:
            try:
                pub = datetime(int(cm.group(1)), int(cm.group(2)), int(cm.group(3)),
                               int(cm.group(4) or 0), int(cm.group(5) or 0), int(cm.group(6) or 0))
            except ValueError:
                pub = None
        else:
            pub = _published(s)
        if pub and (now - pub).total_seconds() >= max_hours * 3600:
            continue          # 超过 max_hours 小时（按精确到分钟的时间算）不再刷新
        out.append((f[:-3], m.group(1), old.group(1) if old else "0"))
    return out


def main():
    dry = "--dry" in sys.argv
    hours = MAX_AGE_HOURS
    if "--hours" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--hours") + 1])
    if "--days" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--days") + 1]) * 24
    rows = notes(hours)
    print("IT之家笔记 %d 篇" % len(rows))
    if dry:
        for name, url, old in rows:
            print("  %-30s %s 条" % (name[:30], old))
        return

    def work(item):
        name, url, old = item
        try:
            p = subprocess.run([PY, CLIP, url, "--name", name, "--refresh-comments"],
                               capture_output=True, text=True, timeout=300)
            out = p.stdout or ""
            if "评论变少" in out:                      # 被减量护栏拦住，文件未改动
                m = re.search(r"评论变少（(\d+) → (\d+) 条）", out)
                return name, old, (m.group(2) if m else "?"), "skip"
            line = (out.strip().splitlines() or [""])[-1]
            m = re.search(r"(\d+) 条", line)
            return name, old, (m.group(1) if m else "?"), "upd"
        except Exception:
            return name, old, "?", "err"

    upd = skip = err = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, old, new, st in ex.map(work, rows):
            if st == "upd":
                if new.isdigit() and new != old:
                    upd += 1; note = "  ← 已更新" if int(new) > int(old) else "  ← 有变化"
                else:
                    skip += 1; note = "  ← 条数未变或未解析到"
            elif st == "skip":
                skip += 1; note = "  ← 接口只返部分评论，已保留原文件"
            elif st == "err":
                err += 1; note = "  ← 失败"
            else:
                note = "  ← 无变化"
            print("  %-30s %s → %s%s" % (name[:30], old, new, note))
    print("完成：更新 %d 篇 / 护栏拦截 %d 篇 / 失败 %d 篇" % (upd, skip, err))


if __name__ == "__main__":
    main()
