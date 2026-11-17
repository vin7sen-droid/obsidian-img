#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md2html.py —— 薄转发（单一真源 /root/workspace/tools/mdhtml.py，theme=ai）

以 Markdown 为唯一底稿生成可视化 HTML（AI 日报浅色主题）。
用法: python3 md2html.py <input.md> [output.html]

改渲染逻辑请改真源 mdhtml.py；本文件只为兼容既有调用路径（assemble/publish/append/mustread 都调它）。
"""
import runpy
import sys

sys.argv = [sys.argv[0]] + sys.argv[1:] + ["--theme", "ai"]
runpy.run_path("/root/workspace/tools/mdhtml.py", run_name="__main__")
