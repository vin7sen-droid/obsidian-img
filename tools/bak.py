#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动文件前先存副本：python3 tools/bak.py "<文件>" ["<文件2>" ...]

副本落到 /root/workspace/bak/inbox/ 与 /sdcard/Download/DSH-Go/inbox-bak/，
文件名 <YYYYmmdd-HHMMSS>__<原名>；超过 24 小时的旧副本自动清理。
备份失败直接以非零码退出——调用方必须停下，不得继续改文件。
"""
import os
import sys
import hashlib
import glob
import re

sys.path.insert(0, "/root/workspace/tools")
from clip import _backup, _backup_or_die  # noqa: E402
from clip import BAK_DIR, BAK_MIRROR  # noqa: E402


def _same_day_copy_exists(path):
    """当日已有内容相同的副本则返回其路径（幂等：内容没变不重复保存）。"""
    import datetime
    today = datetime.datetime.now().strftime("%Y%m%d")
    base = os.path.basename(path)
    try:
        cur = open(path, "rb").read()
    except OSError:
        return None
    for d in (BAK_DIR, BAK_MIRROR):
        for old in glob.glob(os.path.join(d, today + "-*__" + base)):
            try:
                if hashlib.md5(open(old, "rb").read()).hexdigest() == hashlib.md5(cur).hexdigest():
                    return old
            except OSError:
                continue
    return None


def safe_write(path, data, encoding="utf-8", binary=False):
    """覆盖前先备份；备份失败直接 sys.exit，绝不静默覆盖。"""
    _backup_or_die(path)
    if binary:
        with open(path, "wb") as f:
            f.write(data)
    else:
        with open(path, "w", encoding=encoding) as f:
            f.write(data)


def safe_open(path, mode="w", encoding="utf-8"):
    """open() 的包装：写模式且文件已存在时先备份。"""
    if "w" in mode or "a" in mode:
        _backup_or_die(path)
    return open(path, mode, encoding=encoding)

def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python3 tools/bak.py <文件> [更多文件...]")
    for p in sys.argv[1:]:
        if not os.path.exists(p):
            print("跳过（文件不存在）：%s" % p)
            continue
        same = _same_day_copy_exists(p)
        if same:
            print("已有备份（内容未变），跳过 → %s" % os.path.basename(same))
            continue
        dst = _backup_or_die(p)   # 失败会直接 sys.exit
        print("已备份 → %s" % dst)

if __name__ == "__main__":
    main()
