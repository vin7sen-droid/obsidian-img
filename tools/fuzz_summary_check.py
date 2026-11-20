#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘要六条机检 fuzz 测试器（单一真源，跨会话/跨设备可用）。

用法：
    python3 /root/workspace/tools/fuzz_summary_check.py               # 默认 4 seed × (300 坏 + 200 净)
    python3 /root/workspace/tools/fuzz_summary_check.py --bad 100 --clean 50 --seed 7
    python3 --check --summary "某条摘要文本"                            # 只查一条摘要（命中六条机检哪条）

规则真源：news-ai/json2md.py 的 check()（本脚本 importlib 动态加载，不复制规则代码）。
规则口径（2026-11-20 定版）：
  ① 省略号结尾 (?:\\.{2,}|…|。{2,})$
  ② 站点前缀直贴（PREFIX_WORDS）
  ③ 聚合格式 [非中文]{0,32}[／/][中文A-Za-z ]{1,20}[:：]
  ④ 繁体字（TRAD 表）
  ⑤ 英文短语残留 [A-Za-z]{3,}\\s+(via|by|with|from|in|on|at|to|the)\\s+…（豁免《》“”「」引号与（纯英文）括号内专名）
  ⑥ 片段重复 = 句段级（≥10 字完整句段×2）+ 同段内 5 字子串×2
"""
import argparse
import importlib.util
import random
import re
import sys

J2M = "/root/workspace/news-ai/json2md.py"
KEYS = ("省略号", "前缀直贴", "聚合格式", "繁体", "英文短语", "片段重复")


def load_check(path=J2M):
    spec = importlib.util.spec_from_file_location("j2m", path)
    j2m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(j2m)
    except SystemExit:
        pass
    return j2m.check


def run_check(check, title, summary):
    _, errs = check({"items": [{"cat": "模型与产品", "title": title, "url": f"https://e.com/x",
                               "summary": summary,
                               "sources": [{"name": "T", "url": "https://t.com/a", "time": "2026-11-19 08:00"}]}]},
                    "2026-11-19")
    return [e for e in errs if any(k in e for k in KEYS)]


DEFECTS = [
    ("省略号", lambda b, r: b + r.choice(["……", "...", "…"])),
    ("前缀", lambda b, r: r.choice(["IT之家 11 月 19 日消息，", "智东西11月19日报道，",
                                    "要闻提示 1.", "资料来源："]) + b),
    ("聚合", lambda b, r: r.choice(["Saritha Rai /彭博社：", "凯蒂·保罗/路透社：",
                                    "Wayne Ma /The Information：", "Jasmine Sun /纽约时报："]) + b),
    ("繁体", lambda b, r: (b[:12] + "廣告投放" + b[12:]) if len(b) > 14 else b + "廣告"),
    ("英文", lambda b, r: b[:-2] + r.choice([" inhibition via the ROS-GPX4 axis",
                                             " delivered by the team of experts",
                                             " uses 100 AI agents to conduct audits"])),
    ("复读", lambda b, r: "该公司在声明中表示将加大投入力度，" * 2 + b),
]

CLEAN = [
    lambda i, r: f"公司{i}发布 H200——采用 Blackwell 架构的旗舰产品，性能提升 {r.randint(10,90)}%，MAI Code 2 达 GPT-6.1 Sol 98% 的性能，成本仅 40%，已向首批客户交付。",
    lambda i, r: f"Anthropic 发布最新技术报告《Advancing chip design with OpenAI tools》，详述 {r.randint(3,30)} 个芯片设计案例。",
    lambda i, r: f"谷歌云发布 TPU v7 训练平台，性能提升 {r.randint(20,80)} 倍，已开放给 Cloud 客户预览使用，正式版明年上线。",
    lambda i, r: f"调查显示 {r.randint(60,90)}% 的企业已把 AI 用于核心业务流程，其中制造业与金融业渗透率最高，中小企业相对滞后，该趋势预计明年加速。",
    lambda i, r: f"{chr(65+i%26)} 公司一季度营收 {r.randint(10,90)} 亿美元、同比增长 {r.randint(5,60)}%，AI 半导体收入 {r.randint(5,30)} 亿美元、同比增长 {r.randint(80,300)}%；对下财季指引为总营收约 {r.randint(20,50)} 亿美元。",
    lambda i, r: f"盖洛普调查显示 {r.randint(20,40)}% 青少年使用 \"Into the Unknown\" 类 AI 陪伴应用，GDC Festival of Gaming 行业调查亦显示游戏开发者采用率过半。",
]


def body_of(i):
    return (f"公司{i}发布了全新一代 AI 芯片与配套软件平台，性能提升 {random.randint(10,90)}%，"
            f"成本下降 {random.randint(5,40)}%，即日起向首批企业客户交付使用")


def main():
    ap = argparse.ArgumentParser(description="摘要六条机检 fuzz 测试器")
    ap.add_argument("--bad", type=int, default=300, help="每个 seed 的坏样本数")
    ap.add_argument("--clean", type=int, default=200, help="每个 seed 的干净样本数")
    ap.add_argument("--seed", type=int, help="随机种子（默认跑 4 个 seed）")
    ap.add_argument("--check", metavar="TEXT", help="只检查一条摘要文本")
    a = ap.parse_args()
    check = load_check()

    if a.check:
        hits = run_check(check, "t", a.check)
        print("命中:" if hits else "✓ 干净（六条机检 0 命中）")
        for e in hits:
            print(" -", e)
        return

    seeds = [a.seed] if a.seed is not None else [7, 13, 99, 2024]
    tot_hit = tot_bad = tot_fp = tot_clean = 0
    miss_demo = []
    for sd in seeds:
        r = random.Random(sd)
        for i in range(a.bad):
            name, fn = r.choice(DEFECTS)
            s = fn(body_of(i), r)
            tot_bad += 1
            if run_check(check, f"t{i}", s):
                tot_hit += 1
            elif len(miss_demo) < 3:
                miss_demo.append((name, s[:36]))
        for i in range(a.clean):
            s = r.choice(CLEAN)(i, r)
            tot_clean += 1
            if run_check(check, f"n{i}", s):
                tot_fp += 1
    print(f"fuzz：坏样本 {tot_bad} 拦截 {tot_hit}（漏 {tot_bad-tot_hit}）| 干净 {tot_clean} 误报 {tot_fp}")
    for name, demo in miss_demo:
        print(f"  漏检示例 [{name}]: {demo}…")
    sys.exit(0 if tot_bad == tot_hit and tot_fp == 0 else 1)


if __name__ == "__main__":
    main()
