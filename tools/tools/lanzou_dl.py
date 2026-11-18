#!/usr/bin/env python3
# lanzou_dl.py —— 蓝奏云分享链接解析+下载（免登录）
# 2026 版页面配方，移植自 hanximeng/LanzouAPI index.php（2026-10 活跃版）
# 用法: python3 lanzou_dl.py "<链接>" [更多链接...] [-p 密码] [-o 输出目录]
#       python3 lanzou_dl.py "<链接>" --info        # 只解析直链不下载
# 输出默认 /sdcard/Download/DSH-Go/
# 依赖: requests（pip install requests）
import argparse
import json
import os
import re
import sys
import time

import requests

VERSION = "1.0.0"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/72.0.3626.121 Safari/537.36"
DEFAULT_OUT = "/sdcard/Download/DSH-Go"
HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}
FINAL_HEADERS = {
    **HEADERS_BASE,
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "X-Requested-With": "mark.via",
}


class ParseError(Exception):
    pass


def acw_sc_v2(arg1: str) -> str:
    """阿里云 ESA 挑战页 var arg1 -> acw_sc__v2 cookie 值（位置重排 + mask 异或）"""
    pos_list = [15, 35, 29, 24, 33, 16, 1, 38, 10, 9, 19, 31, 40, 27, 22, 23, 25, 13,
                6, 11, 39, 18, 20, 8, 14, 21, 32, 26, 2, 30, 7, 4, 17, 5, 3, 28, 34, 37, 12, 36]
    mask = "3000176000856006061501533003690027800375"
    out = [""] * 40
    for i, ch in enumerate(arg1):
        for j, pos in enumerate(pos_list):
            if pos == i + 1:
                out[j] = ch
    arg2 = "".join(out)
    result = ""
    for i in range(0, min(len(arg2), len(mask)), 2):
        xor = hex(int(arg2[i:i + 2], 16) ^ int(mask[i:i + 2], 16))[2:]
        result += xor.zfill(2)
    return result


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.headers["X-Forwarded-For"] = "66.66.66.66"
    s.headers["CLIENT-IP"] = "66.66.66.66"
    return s


def get_with_challenge(s: requests.Session, url: str, referer: str = "") -> str:
    """GET 页面；若命中 ESA 挑战页（var arg1=40位hex）则算 acw_sc__v2 cookie 后重试一次"""
    headers = {**HEADERS_BASE, **({"Referer": referer} if referer else {})}
    resp = s.get(url, headers=headers, timeout=15)
    html = resp.text
    m = re.search(r"var\s+arg1=['\"]([0-9a-f]{40})['\"]", html, re.I)
    if m:
        s.cookies.set("acw_sc__v2", acw_sc_v2(m.group(1)))
        resp = s.get(url, headers=headers, timeout=15)
        html = resp.text
    return html


def post_multipart(s: requests.Session, url: str, fields: dict, referer: str) -> dict:
    """PHP curl POST 数组 = multipart/form-data；蓝奏云 ajax 接口要求此编码"""
    files = {k: (None, str(v)) for k, v in fields.items()}
    resp = s.post(url, files=files, headers={"Referer": referer}, timeout=15)
    try:
        return resp.json()
    except ValueError:
        raise ParseError(f"接口返回非 JSON: {resp.text[:120]}")


def head_final(s: requests.Session, url: str) -> str:
    """请求直链但不跟随重定向；302 的 Location 即最终下载地址；ESA 挑战最多重试 3 次"""
    for _ in range(3):
        try:
            resp = s.get(url, headers=FINAL_HEADERS, allow_redirects=False, timeout=15, stream=True)
        except requests.RequestException:
            return ""
        loc = resp.headers.get("Location", "")
        if loc:
            return loc
        body = resp.text
        m = re.search(r"var\s+arg1=['\"]([0-9a-f]{40})['\"]", body, re.I)
        if m:
            s.cookies.set("acw_sc__v2", acw_sc_v2(m.group(1)))
            continue
        break
    return ""


def resolve(url: str, pwd: str = "") -> dict:
    """解析分享链接 -> {name, size, durl}；失败抛 ParseError"""
    s = new_session()
    origin = re.match(r"(https?://[^/]+)", url).group(1)
    html = get_with_challenge(s, url, referer=origin)

    if "文件取消分享了" in html or "文件不存在" in html:
        raise ParseError("分享已取消或文件不存在")

    name_m = (re.search(r'font-size: 30px;text-align: center;padding: 56px 0px 20px 0px;">(.*?)</div>', html)
              or re.search(r'<div class="n_box_3fn".*?>(.*?)</div>', html)
              or re.search(r"var filename = '(.*?)';", html)
              or re.search(r'div class="b"><span>(.*?)</span></div>', html))
    size_m = (re.search(r'<div class="n_filesize".*?>大小：(.*?)</div>', html)
              or re.search(r'<span class="p7">文件大小：</span>(.*?)<br>', html))
    name = name_m.group(1) if name_m else ""
    size = size_m.group(1) if size_m else ""

    if "function down_p(){" in html:  # 带密码
        if not pwd:
            raise ParseError("需要分享密码（-p 或 --pwd）")
        sign = ""
        m = re.search(r"^[ \t]*data\s*:\s*\{[^\r\n]*['\"]sign['\"]\s*:\s*(?:(['\"])(.*?)\1|([A-Za-z_$][\w$]*))", html, re.M)
        if m and m.group(2) is not None:
            sign = m.group(2)
        elif m and m.group(3):
            sv = re.findall(r"var\s+" + re.escape(m.group(3)) + r"\s*=\s*(['\"])(.*?)\1\s*;", html)
            sv = [v[1] for v in sv if v[1]]
            sign = sv[-1] if sv else ""
        if not sign:
            seg = re.findall(r"'sign':'(.*?)',", html)
            sign = seg[1] if len(seg) > 1 else (seg[0] if seg else "")
        aj = re.search(r"(?:https?://[^/\s]+/)?(ajax(?:m|file)\.php\?file=\d+)", html)
        if not sign or not aj:
            raise ParseError("未找到密码页 sign 或下载接口参数")
        ajax_path = aj.group(0)
        if not ajax_path.startswith("http"):
            ajax_path = origin + "/" + ajax_path.lstrip("/")
        data = post_multipart(s, ajax_path, {"action": "downprocess", "sign": sign, "p": pwd, "kd": 1}, url)
        if data.get("inf"):
            name = data["inf"]
    else:  # 不带密码
        link = (re.search(r'\n<iframe.*?name="[\s\S]*?"\ssrc="/(.*?)"', html)
                or re.search(r'<iframe.*?name="[\s\S]*?"\ssrc="/(.*?)"', html))
        if not link:
            raise ParseError("未找到下载入口，请检查链接是否有效")
        iframe_url = origin + "/" + link.group(1)
        page2 = get_with_challenge(s, iframe_url, referer=url)
        wp_sign = re.search(r"wp_sign = '(.*?)'", page2)
        websign = re.search(r"ajaxdata = '(.*?)'", page2)
        aj = re.search(r"(?:https?://[^/\s]+/)?(ajax(?:m|file)\.php\?file=\d+)", page2)
        if not wp_sign or not aj:
            raise ParseError("未找到下载接口参数，请检查链接是否有效")
        ajax_path = aj.group(0)
        if not ajax_path.startswith("http"):
            ajax_path = origin + "/" + ajax_path.lstrip("/")
        data = post_multipart(s, ajax_path, {
            "action": "downprocess",
            "websignkey": websign.group(1) if websign else "",
            "signs": websign.group(1) if websign else "",
            "sign": wp_sign.group(1),
            "websign": "", "kd": 1, "ves": 1,
        }, iframe_url)

    if data.get("zt") != 1:
        raise ParseError(f"解析失败: {data.get('inf', '蓝奏云返回异常')}")

    durl1 = data["dom"] + "/file/" + data["url"]
    durl = head_final(s, durl1) or durl1
    durl = re.sub(r"pid=.*?&", "", durl)
    return {"name": name or data.get("inf", ""), "size": size, "durl": durl}


def safe_name(name: str) -> str:
    name = os.path.basename(name.strip()) or ("lanzou_" + str(int(time.time())))
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def download(durl: str, name: str, out_dir: str, referer: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, safe_name(name))
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  跳过（已存在）: {path}")
        return path
    s = new_session()
    headers = {**HEADERS_BASE, "Referer": referer}
    with s.get(durl, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        done = 0
        t0 = time.time()
        with open(path + ".part", "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                done += len(chunk)
                if total and done % (1 << 20) < (1 << 16):
                    pct = done * 100 // total
                    speed = done / max(time.time() - t0, 0.1) / 1024
                    print(f"\r  {pct:3d}% {done/1048576:.1f}/{total/1048576:.1f}MB {speed:.0f}KB/s", end="", flush=True)
    os.rename(path + ".part", path)
    print(f"\r  100% 共 {done/1048576:.2f}MB{'':20}")
    return path


def main():
    ap = argparse.ArgumentParser(description="蓝奏云分享链接解析+下载（免登录）")
    ap.add_argument("urls", nargs="+", help="分享链接（可多条）")
    ap.add_argument("-p", "--pwd", default="", help="分享密码")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--info", action="store_true", help="只解析直链不下载")
    ap.add_argument("--version", action="version", version=f"lanzou_dl.py {VERSION}")
    args = ap.parse_args()

    ok, fails = [], []
    for u in args.urls:
        print(f"[解析] {u}")
        try:
            info = resolve(u, args.pwd)
            print(f"  名称: {info['name']}  大小: {info['size'] or '?'}")
            print(f"  直链: {info['durl'][:90]}")
            if args.info:
                ok.append(u)
                continue
            p = download(info["durl"], info["name"], args.out, referer=u)
            print(f"  完成: {p}")
            ok.append(u)
        except (ParseError, requests.RequestException) as e:
            print(f"  失败: {e}")
            fails.append((u, str(e)))
    if fails:
        print(f"\n{len(ok)} 成功, {len(fails)} 失败:")
        for u, e in fails:
            print(f"  {u} -> {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
