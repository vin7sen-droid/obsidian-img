#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""numcheck.py —— 薄转发（单一真源 scripts/xcheck.mjs 的 numDiff）

数字回源校验：原文数字 vs 译文数字，按 SI 归一比对，输出 missing / extra / scale_risk。
用法: python3 numcheck.py <原文.txt> <译文.txt>

2026-11-17 用户定「移到 xcheck.mjs」：本文件不再有独立实现，只做命令行兼容转发
（技能 SOP 里既有的 `python3 tools/numcheck.py a b` 调用路径全部不变）。
改逻辑请改 xcheck.mjs 的 numDiff / parseNums / parseZhNums。
"""
import os
import sys
import subprocess

XCHECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xcheck.mjs")

if len(sys.argv) < 3:
    print(__doc__.strip(), file=sys.stderr)
    sys.exit(2)

if not os.path.exists(XCHECK):
    print("找不到真源 " + XCHECK, file=sys.stderr)
    sys.exit(3)

r = subprocess.run(["node", XCHECK, "--numcheck", sys.argv[1], sys.argv[2]],
                   capture_output=True, text=True)
sys.stdout.write(r.stdout)
sys.stderr.write(r.stderr)
sys.exit(r.returncode)
