#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站视频+弹幕下载（2026-11-19）

用法:
    python3 /root/workspace/tools/bili_dl.py "<BV号或bilibili链接>" [更多...] [--res 1080]
    python3 bili_dl.py -f urls.txt [--res 720]

分辨率: --res N（360/480/720/1080/1440/2160，默认 1080；超过登录权限的档位自动回退）

能力:
    视频: yt-dlp 下载 DASH 流并 ffmpeg 合并（默认 1080p avc1 优先，免登录）
    弹幕: comment.bilibili.com/<cid>.xml（raw deflate 解压，每页最多 1200 条）

落点:
    /sdcard/Download/bilibili/<UP主>/<UP主>_<BV号>.mp4 + 同名 .xml
    多P视频按 <UP主>_<BV号>_P01.mp4 逐P下载与弹幕。

幂等: 文件已存在且非空即跳过。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

VERSION = "1.1.0"
BASE = "/sdcard/Download/bilibili"
UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "Chrome/124.0 Mobile Safari/537.36")
HDRS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}


def _get(url, timeout=15):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=HDRS), timeout=timeout).read()


def parse_bvid(text):
    """从文本里抠出全部 BV 号（支持裸 BV 号与完整链接）。"""
    return re.findall(r"(BV[0-9A-Za-z]{10})", text or "")


def fetch_view(bvid):
    d = json.loads(_get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"))
    if d.get("code") != 0:
        raise RuntimeError(f"view API code={d.get('code')} {d.get('message','')}")
    v = d["data"]
    return {
        "bvid": bvid,
        "title": v.get("title", ""),
        "owner": v.get("owner", {}).get("name", "unknown"),
        "pages": [{"page": p["page"], "part": p.get("part", ""),
                   "cid": p["cid"]} for p in v.get("pages", [{"page": 1, "part": "", "cid": 0}])],
    }


def fetch_danmaku(cid, dst):
    raw = _get(f"https://comment.bilibili.com/{cid}.xml")
    import zlib
    xml = zlib.decompress(raw, -15).decode("utf-8", "replace")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return len(re.findall(r"<d ", xml))


def download_video(bvid, page, dst, max_height=1080):
    url = f"https://www.bilibili.com/video/{bvid}?p={page}"
    cmd = ["python3", "-m", "yt_dlp",
           "-f", f"bv*[vcodec^=avc1][height<={max_height}]+ba/"
                 f"b[height<={max_height}]/bv*+ba/b",
           "--merge-output-format", "mp4",
           "--no-playlist", "-q", "-o", dst, url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:200])


def handle(bvid, force=False, res=1080):
    v = fetch_view(bvid)
    owner = re.sub(r'[\\/:*?"<>|]', "_", v["owner"])
    folder = os.path.join(BASE, owner)
    os.makedirs(folder, exist_ok=True)
    stem = f"{owner}_{bvid}"
    single = len(v["pages"]) == 1
    n_ok = 0
    for p in v["pages"]:
        tag = stem if single else f"{stem}_P{p['page']:02d}"
        mp4 = os.path.join(folder, tag + ".mp4")
        xml = os.path.join(folder, tag + ".xml")
        ok_v = ok_d = True
        if os.path.isfile(mp4) and os.path.getsize(mp4) > 8192 and not force:
            print(f"  skip {tag}.mp4（已存在）")
        else:
            try:
                if force and os.path.isfile(mp4):
                    os.remove(mp4)          # force=真重下，避免 yt-dlp 见旧文件直接跳过
                print(f"  视频下载 {tag}.mp4 …")
                download_video(bvid, p["page"], mp4, res)
                print(f"  ok   {tag}.mp4  {os.path.getsize(mp4)/1e6:.1f}MB")
            except Exception as e:
                ok_v = False
                print(f"  ERR  {tag}.mp4  {e}")
        if os.path.isfile(xml) and os.path.getsize(xml) > 16 and not force:
            print(f"  skip {tag}.xml（已存在）")
        else:
            try:
                n = fetch_danmaku(p["cid"], xml)
                print(f"  ok   {tag}.xml  {n} 条弹幕")
            except Exception as e:
                ok_d = False
                print(f"  ERR  {tag}.xml  {e}")
        if ok_v or ok_d:
            n_ok += 1
    print(f"  -- {v['title'][:40]}（{v['owner']}，{len(v['pages'])}P）")
    print(f"  => {folder}")
    return n_ok


def main():
    args = sys.argv[1:]
    res = 1080
    force = False
    if "--force" in args:
        args = [a for a in args if a != "--force"]
        force = True
    if "--res" in args:
        i = args.index("--res")
        try:
            res = int(args[i + 1])
        except (IndexError, ValueError):
            print("--res 需要数字，如 --res 720"); return 1
        del args[i:i + 2]
    if "-f" in args:
        i = args.index("-f")
        with open(args[i + 1], encoding="utf-8") as fh:
            args = args[:i] + args[i + 2:] + [
                ln.strip() for ln in fh if ln.strip()]
    bvids = []
    for a in args:
        for bv in parse_bvid(a):
            if bv not in bvids:
                bvids.append(bv)
    if not bvids:
        print("未识别到 BV 号/链接")
        return 1
    fails = 0
    for bv in bvids:
        print(f"— {bv}")
        try:
            if handle(bv, force=force, res=res) == 0:
                fails += 1
        except Exception as e:
            fails += 1
            print(f"  ERR {e}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
