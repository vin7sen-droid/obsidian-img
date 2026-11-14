#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微博图片 / Live Photo 下载器（v2）

用法:
    python3 weibo_dl.py "https://weibo.com/8473635228/5364082854592800"
    python3 weibo_dl.py 5364082854592800 <url2> <url3>     # 一次多条
    python3 weibo_dl.py "<url>" --jobs 6 --retries 3
    python3 weibo_dl.py "<url>" --out /sdcard/Download/weibo

落点（默认）: /sdcard/Download/weibo/<博主昵称>/
命名: <昵称>_<bid>_<两位序号>.jpg + 同序号 .mov（仅 livephoto）
幂等: 目标存在且**校验通过**则跳过（坏文件自动重下）。
清单: 同目录 _manifest.json，按 mid 累积（多条微博互不覆盖），原子写。

v2 相对 v1 的改动（参考 GitHub 同类项目 nondanee/weiboPicDownloader、
lonsty/weibo-image-spider 的做法）:
1. 重试 + 指数退避：偶发 403/超时不再直接崩，默认重试 3 次。
2. 并发下载：--jobs，默认 4（线程池），一条微博的多张图并行。
3. 完整性校验：JPEG 必查 FFD9 结尾、视频必查 ftyp+moov，截断文件判失败重下。
4. 一次多目标：命令行可传多条 URL / mid。
5. 失败汇总：末尾列出失败项并以退出码 1 结束，便于重跑。
6. manifest 按 mid 累积，兼容 v1 的单条结构（自动迁移）。

关键点（2026-11-11 实测）:
1. 免登录接口 https://m.weibo.cn/statuses/show?id=<mid>，带手机 UA + X-Requested-With。
2. 图片必须带 Referer: https://weibo.com/，否则 sinaimg 全 403（与 UA 无关）。
3. d['pics'][i]['large']['url'] 是 2000px 宽大图（mw2000 / large 同一文件）。
4. livephoto: pics[i]['type']=='livephoto'，pics[i]['videoSrc'] =
   https://video.weibo.com/media/play?livephoto=<urlencode(mov)>；
   解包 query 里 livephoto 参数得到 livephoto.us.sinaimg.cn 上的 .mov（QuickTime），
   直接 GET 该 mov 会 403，必须走 video.weibo.com/media/play 中转。
5. 长文: d['isLongText'] 为真时正文在 https://m.weibo.cn/statuses/extend?id=<mid>。
6. 匿名态**不支持**按博主批量（container/getIndex 一律 432，需登录 cookie），
   要整号下载请带 cookie 用其它工具。
"""
import argparse
import json
import os
import re
import struct
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')
REF = 'https://weibo.com/'
DEF_OUT = '/sdcard/Download/weibo'

VERSION = '2.0.0'
# 版本检查源（只提示、不自动替换）：24 小时内最多查一次，失败静默。
UPDATE_CHECK_URL = ('https://gh-proxy.com/https://raw.githubusercontent.com/'
                    'vin7sen-droid/obsidian-img/main/tools/weibo_dl.json')
CACHE_DIR = os.path.expanduser('~/.cache/weibo_dl')
CHECK_INTERVAL = 86400


# ---------- 网络 ----------

def _open(url, referer=REF, timeout=60, extra=None):
    headers = {'User-Agent': UA, 'Referer': referer}
    if extra:
        headers.update(extra)
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                  timeout=timeout)


def with_retry(fn, what, retries=3, backoff=1.0):
    """失败重试，退避 1s/2s/4s。最后一次仍失败则抛 RuntimeError。"""
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # 403 / 超时 / 连接重置都在这里
            last = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    raise RuntimeError('%s 失败（已重试 %d 次）: %s' % (what, retries, last))


def fetch_json(url, retries=3):
    extra = {'Accept': 'application/json, text/plain, */*',
             'X-Requested-With': 'XMLHttpRequest'}

    def _do():
        with _open(url, referer='https://m.weibo.cn/', extra=extra) as r:
            return r.read().decode('utf-8', 'replace')

    # 缺 Accept/X-Requested-With 时接口会回 HTML 而不是 JSON（实测）
    raw = with_retry(_do, '接口请求', retries)
    if not raw.lstrip().startswith('{'):
        raise SystemExit('接口返回非 JSON（可能被风控）：' + raw[:200])
    return json.loads(raw)


# ---------- 校验 ----------

def verify_file(path):
    """返回 (ok, reason)。JPEG 查 FFD8/FFD9，视频查 ftyp + moov。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False, '读不到文件属性'
    if size < 1024:
        return False, '文件过小(%dB)' % size
    with open(path, 'rb') as f:
        head = f.read(64)
        f.seek(-2, os.SEEK_END)
        tail2 = f.read(2)
        f.seek(0)
        headblk = f.read(min(size, 1048576))
        f.seek(max(0, size - 1048576))
        tailblk = f.read()
    if head[:2] == b'\xff\xd8':
        if tail2 == b'\xff\xd9':
            return True, ''
        return False, 'JPEG 结尾不是 FFD9（疑似截断）'
    if b'ftyp' in head:
        return _atoms_ok(path, size)
    return True, '未知类型，跳过校验'


def _atoms_ok(path, size):
    """遍历顶层 atom 校验容器完整性：atom 声明长度超出文件尾即判截断。"""
    off, seen = 0, []
    with open(path, 'rb') as f:
        while off < size:
            f.seek(off)
            head = f.read(16)
            if len(head) < 8:
                return False, 'atom 头不完整（截断）'
            sz = struct.unpack('>I', head[:4])[0]
            typ = head[4:8]
            if sz == 1:                      # 64 位扩展长度
                if len(head) < 16:
                    return False, 'atom 扩展长度不完整'
                sz = struct.unpack('>Q', head[8:16])[0]
            elif sz == 0:                    # 延伸到文件尾
                sz = size - off
            if sz < 8:
                return False, 'atom 长度非法(%s)' % typ.decode('latin1', 'replace')
            if off + sz > size:
                return False, '%s 声明长度超出文件尾（截断）' % typ.decode('latin1', 'replace')
            seen.append(typ)
            off += sz
    for need in (b'ftyp', b'moov', b'mdat'):
        if need not in seen:
            return False, '缺少 %s' % need.decode()
    return True, ''


def already_ok(path, verify=True):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    return verify_file(path)[0] if verify else True


# ---------- 下载 ----------

def download(url, out, referer=REF, retries=3, verify=True):
    """下到 .part 再校验、再原子改名。返回字节数。"""
    tmp = out + '.part'

    def _do():
        n = 0
        with _open(url, referer=referer) as r, open(tmp, 'wb') as f:
            while True:
                b = r.read(65536)
                if not b:
                    break
                f.write(b)
                n += len(b)
        return n

    n = with_retry(_do, os.path.basename(out), retries)
    if n == 0:
        os.remove(tmp)
        raise RuntimeError('空响应: ' + url)
    if verify:
        ok, why = verify_file(tmp)
        if not ok:
            os.remove(tmp)
            raise RuntimeError('校验失败(%s): %s' % (why, url))
    os.replace(tmp, out)
    return n


def run_tasks(tasks, jobs, retries, verify, quiet):
    """tasks: [{kind,url,path,label}]。返回 (done, failed)。"""
    results = {}
    todo = []
    for t in tasks:
        if already_ok(t['path'], verify):
            results[t['path']] = ('skip', os.path.getsize(t['path']), '')
            if not quiet:
                print('  skip %-34s %5.1fMB' % (t['label'], os.path.getsize(t['path']) / 1048576))
        else:
            todo.append(t)

    failed = []
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            futs = {ex.submit(download, t['url'], t['path'], t.get('referer', REF),
                              retries, verify): t for t in todo}
            for f in as_completed(futs):
                t = futs[f]
                try:
                    n = f.result()
                    results[t['path']] = ('ok', n, '')
                    if not quiet:
                        print('  ok   %-34s %5.1fMB' % (t['label'], n / 1048576))
                except Exception as e:
                    results[t['path']] = ('fail', 0, str(e))
                    failed.append((t, e))
                    if not quiet:
                        print('  FAIL %-34s %s' % (t['label'], e))

    done = sum(1 for r in results.values() if r[0] in ('ok', 'skip'))
    return done, failed


# ---------- 解析 ----------

def parse_mid(text):
    text = text.strip()
    if re.fullmatch(r'\d{10,}', text):
        return text
    m = re.search(r'weibo\.com/\d+/([0-9A-Za-z]{9,})', text)
    if m:
        # 接口 https://m.weibo.cn/statuses/show 同时接受 mid 与 bid（实测），
        # 原样返回即可；真实 mid 从响应里取，不要自己换算。
        return m.group(1)
    m = re.search(r'(?:m\.weibo\.cn/(?:detail|status)/|[?&]id=)(\d{10,})', text)
    if m:
        return m.group(1)
    raise SystemExit('无法从输入解析微博 mid: ' + text)


# ---------- 版本检查（只提示，不自动替换） ----------

def _ver_tuple(s):
    out = []
    for part in re.split(r'[.\-+]', str(s)):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def check_update():
    """有新版只打印一行提示；任何异常都静默忽略，绝不阻塞下载。"""
    cache_path = os.path.join(CACHE_DIR, 'update.json')
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if time.time() - cache.get('checked_at', 0) < CHECK_INTERVAL:
        return
    try:
        with _open(UPDATE_CHECK_URL, referer='https://github.com/', timeout=3) as r:
            info = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        return
    cache['checked_at'] = time.time()
    latest = str(info.get('version') or '')
    if latest and _ver_tuple(latest) > _ver_tuple(VERSION):
        print('! 有新版本 v%s（当前 v%s）— %s' % (latest, VERSION, info.get('note') or ''))
        if info.get('url'):
            print('  取新版: %s' % info['url'])
        else:
            print('  当前脚本已是最新，无需处理。')
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except Exception:
        pass


# ---------- 清单 ----------

def load_manifest(path):
    """读旧清单；v1 的单条结构 {mid,items,...} 自动迁移成 {posts:{mid:...}}。"""
    try:
        with open(path, encoding='utf-8') as f:
            m = json.load(f)
    except Exception:
        return {'posts': {}}
    if 'posts' in m:
        m.setdefault('posts', {})
        return m
    old_mid = m.get('mid')
    base = {k: v for k, v in m.items() if k not in ('mid', 'bid', 'items')}
    out = dict(base)
    out['posts'] = {old_mid: m} if old_mid else {}
    return out


def save_manifest(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- 单条微博 ----------

def handle_post(target, args):
    mid = target if args.mid else parse_mid(target)
    j = fetch_json('https://m.weibo.cn/statuses/show?id=' + mid, args.retries)
    if j.get('ok') != 1:
        raise RuntimeError('接口失败 mid=%s: %r' % (mid, j.get('ok')))
    d = j['data']
    name = d['user']['screen_name']
    uid = str(d['user']['id'])
    bid = d.get('bid') or mid
    mid = str(d.get('mid') or mid)   # 真实 mid（输入可能是 bid）
    base = os.path.join(args.out, name)
    os.makedirs(base, exist_ok=True)

    text = re.sub(r'<[^>]+>', '', d.get('text', '')).replace('&nbsp;', ' ')
    pics = d.get('pics') or []

    print('# %s  %s  mid=%s  %d 图' % (name, bid, mid, len(pics)))
    if not args.quiet and text.strip():
        print('# %s' % text.strip()[:100])

    tasks, manifest = [], []
    for i, p in enumerate(pics, 1):
        stem = '%s_%s_%02d' % (name, bid, i)
        jpg = os.path.join(base, stem + '.jpg')
        tasks.append({'kind': 'jpg', 'url': p['large']['url'], 'path': jpg,
                      'label': stem + '.jpg'})
        row = {'idx': i, 'jpg': os.path.basename(jpg), 'type': p.get('type', 'photo')}
        if p.get('type') == 'livephoto' and p.get('videoSrc'):
            m = re.search(r'livephoto=([^&]+)', p['videoSrc'])
            if m:
                live = urllib.parse.unquote(m.group(1))
                play = ('https://video.weibo.com/media/play?livephoto='
                        + urllib.parse.quote(live, safe=''))
                mov = os.path.join(base, stem + '.mov')
                tasks.append({'kind': 'mov', 'url': play, 'path': mov,
                              'label': stem + '.mov'})
                row['mov'] = os.path.basename(mov)
                row['live_url'] = live
        manifest.append(row)

    page_info = d.get('page_info') or {}
    video_url = page_info.get('page_url') if page_info.get('type') == 'video' else None
    if not pics and video_url:
        print('  该微博无图，视频地址: %s' % video_url)

    done, failed = run_tasks(tasks, args.jobs, args.retries, not args.no_verify, args.quiet)
    if not args.quiet:
        print('  -- %d 项完成（含跳过）' % done)

    # 清单按 mid 累积（每个博主目录一份，多条微博互不覆盖）
    mp_path = os.path.join(base, '_manifest.json')
    data = load_manifest(mp_path)
    data['user'] = name
    data['uid'] = uid
    data.setdefault('posts', {})[mid] = {
        'mid': mid, 'bid': bid,
        'page_url': 'https://weibo.com/%s/%s' % (uid, bid),
        'created_at': d.get('created_at'), 'text': text.strip(),
        'video': video_url, 'items': manifest,
    }
    save_manifest(mp_path, data)
    print('  => ' + base)
    return failed


def main():
    ap = argparse.ArgumentParser(description='微博图片 / Live Photo 下载器')
    ap.add_argument('target', nargs='+', help='微博 URL 或 mid，可多个')
    ap.add_argument('--out', default=DEF_OUT, help='父目录，默认 ' + DEF_OUT)
    ap.add_argument('--mid', action='store_true', help='强制把 target 当 mid')
    ap.add_argument('--jobs', type=int, default=4, help='并发下载数，默认 4')
    ap.add_argument('--retries', type=int, default=3, help='单个文件重试次数，默认 3')
    ap.add_argument('--no-verify', action='store_true', help='跳过文件完整性校验')
    ap.add_argument('-q', '--quiet', action='store_true')
    ap.add_argument('--no-update-check', action='store_true', help='跳过版本检查')
    ap.add_argument('--version', action='version', version='weibo_dl ' + VERSION)
    args = ap.parse_args()

    if not args.no_update_check:
        check_update()

    all_failed = []
    for n, target in enumerate(args.target, 1):
        if len(args.target) > 1 and not args.quiet:
            print('\n[%d/%d] %s' % (n, len(args.target), target))
        try:
            all_failed += handle_post(target, args)
        except SystemExit:
            raise
        except Exception as e:
            all_failed.append(({'label': target, 'url': target}, e))
            print('  !! %s: %s' % (target, e))

    if all_failed:
        print('\n!! %d 项失败（重跑本命令会只补下这些）:' % len(all_failed))
        for t, e in all_failed:
            print('   %-36s %s' % (t.get('label', '?'), e))
        sys.exit(1)


if __name__ == '__main__':
    main()