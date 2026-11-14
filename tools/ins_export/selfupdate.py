#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""社媒下载引擎自升级脚本（根据 GitHub 上游更新改进，2026-11-14）

原理：instaloader / gallery-dl / yt-dlp 都是活跃 pip 包，Instagram 抽风后
上游会修 extractor——升级 pip 包就是"根据 GitHub 改进"的最直接方式。

用法:
    python3 selfupdate.py            # 升级三包 -> 回归自检 -> 失败自动回滚
    python3 selfupdate.py --check    # 只看当前版本，不升级

自检：用已知公开好短码 DfeVc7QI29Z 走一遍元数据解析（不重复下载，
已存在文件自动跳过），解析活着 = 引擎链可用。
"""
import importlib.metadata as im
import subprocess
import sys

PKGS = ["instaloader", "gallery-dl", "yt-dlp"]
MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
TEST_URL = "https://www.instagram.com/p/DfeVc7QI29Z/"  # 已知公开好短码（paodaoo 5图）
INS_DL = "/root/workspace/ins_export/ins_dl.py"


def versions():
    out = {}
    for p in PKGS:
        try:
            out[p] = im.version(p)
        except im.PackageNotFoundError:
            out[p] = None
    return out


def pip(*args):
    r = subprocess.run([sys.executable, "-m", "pip", *args, "-i", MIRROR],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-300:] + r.stderr[-300:])
    return r.returncode


def regression():
    """自检：已知好短码能解析出元数据（幂等，不重复下载）。"""
    r = subprocess.run([sys.executable, INS_DL, TEST_URL],
                       capture_output=True, text=True, timeout=300)
    ok = r.returncode == 0 and "（5/5 个文件）" in (r.stdout + r.stderr) or "已存在" in r.stdout
    return ok, (r.stdout + r.stderr).strip().splitlines()[-3:]


def main():
    check_only = "--check" in sys.argv
    before = versions()
    print("当前版本:", ", ".join(f"{p}={v}" for p, v in before.items()))
    if check_only:
        return

    print("\n[1/3] pip 升级中……")
    pip("install", "-U", *PKGS)
    after = versions()
    changed = [p for p in PKGS if before[p] != after[p]]
    print("升级后:", ", ".join(f"{p}={v}" for p, v in after.items()))
    if not changed:
        print("（无版本变化，上游暂无新发布）")

    print("\n[2/3] 回归自检……")
    ok, tail = regression()
    for line in tail:
        print(" ", line)

    if ok:
        print("\n[3/3] 自检通过 ✅ 升级完成")
        return

    if not changed:
        print("\n[3/3] 自检失败 ⚠️ 但版本未变，可能是目标短码失效或网络问题，未回滚")
        return

    print(f"\n[3/3] 自检失败 ⚠️ 回滚到升级前版本：{' '.join(changed)}")
    for p in changed:
        pip("install", f"{p}=={before[p]}")
    ok2, tail2 = regression()
    print("回滚后自检:", "通过 ✅" if ok2 else "仍失败（问题可能不在包版本）⚠️")
    for line in tail2:
        print(" ", line)


if __name__ == "__main__":
    main()
