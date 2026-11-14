#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音图集/视频/live 图下载器（免签名配方，2026-11-09 实测）

用法：
  python3 douyin_dl.py "<整段分享文本或链接>" [更多链接...]

支持：v.douyin.com 短链（从分享文本里自动抠出）、图集图片、live 图视频、纯视频。
保存：/sdcard/Download/douyin/<用户名>/<用户名>_<短链码>_<3位序号>.jpg / .mp4
幂等：目标文件已存在（>8KB）自动跳过，同作者新链接并入原文件夹不重复下载。
其他：--check 配方体检｜--version 版本｜--no-update-check 跳过更新检查。
"""
import urllib.request, urllib.parse, json, re, os, sys, time, base64

VERSION = '2.0.1'
REPO = 'vin7sen-droid/obsidian-img'
RAW = 'https://gh-proxy.com/https://raw.githubusercontent.com/' + REPO + '/main/'
UPDATE_CHECK_URL = RAW + 'tools/douyin_dl.json'   # {"version","note","url"}
CACHE_DIR = os.path.expanduser('~/.cache/douyin_dl')
CHECK_INTERVAL = 86400  # 24h 内最多查一次

UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
OKUA = 'okhttp/3.10.0.1'
OUTROOT = '/sdcard/Download/douyin'
INDEX = os.path.join(OUTROOT, '_aweme_index.txt')  # aweme_id -> 已落盘文件，跨分享码去重

def http_get(url, headers, timeout=40, tries=2):
    """下载失败（网络/超时/5xx）自动重试 1 次——抖音接口偶发超时是常态"""
    last = None
    for k in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout).read()
        except Exception as e:
            last = e
    raise last

def index_get(aid):
    try:
        with open(INDEX, encoding='utf-8') as f:
            for line in f:
                p = line.rstrip('\n').split('\t')
                if p and p[0] == str(aid):
                    return [x for x in p[1:] if x]
    except Exception:
        pass
    return None

def index_put(aid, paths):
    try:
        with open(INDEX, encoding='utf-8') as f:
            lines = [l for l in f.read().splitlines()
                     if l.split('\t')[0] != str(aid)]
    except Exception:
        lines = []
    lines.append('\t'.join([str(aid)] + list(paths)))
    with open(INDEX, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

def resolve(short):
    """短链 -> (aweme_id, iid, did, share_version)"""
    r = urllib.request.urlopen(urllib.request.Request(
        'https://v.douyin.com/%s/' % short, headers={'User-Agent': UA}), timeout=15)
    final = r.url
    m = re.search(r'/(?:slides|video|note)/(\d+)', final)
    if not m:
        raise RuntimeError('重定向页没拿到 aweme_id: %s' % final)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
    return (m.group(1), (q.get('iid') or [''])[0], (q.get('did') or [''])[0],
            (q.get('share_version') or ['370600'])[0])

def resolve_long(u):
    """完整分享长链 -> (aweme_id, iid, did, share_version)

    App 分享到剪贴板/其他应用时，文本里除了 v.douyin.com 短链，往往还带
    www.iesdouyin.com/share/{note,video,slides}/<id>/?...&iid=..&did=..&share_version=..
    这条长链里的 iid/did 是真实设备参数，直接喂 feed 命中率最高。
    """
    m = re.search(r'/(?:slides|video|note)/(\d+)', u)
    if not m:
        return None
    q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
    return (m.group(1), (q.get('iid') or [''])[0], (q.get('did') or [''])[0],
            (q.get('share_version') or ['370600'])[0])

def fetch_aweme(aid, iid, did, ver):
    """feed 接口拿详情（免签名核心：okhttp UA + version_code>=370600）"""
    p = {'aweme_id': aid, 'iid': iid, 'device_id': did, 'aid': '1128',
         'version_code': ver, 'channel': 'googleplay', 'os_version': '13',
         'device_platform': 'android'}
    u = 'https://aweme.snssdk.com/aweme/v1/feed/?' + urllib.parse.urlencode(p)
    d = json.loads(http_get(u, {'User-Agent': OKUA}, timeout=15).decode('utf-8', 'replace'))
    lst = d.get('aweme_list') or []
    # aweme_list[0] 偶尔是推荐内容而非目标，必须按 aweme_id 精确匹配
    hit = next((x for x in lst if str(x.get('aweme_id')) == str(aid)), None)
    if hit is None:
        raise RuntimeError('feed 接口没返回目标 aweme_id=%s（作品可能已删除/设为私密，非脚本问题）' % aid)
    return hit

def dl(urls, fn, jpeg=False, ref='https://www.iesdouyin.com/'):
    """按序试 URL 列表，校验文件头；jpeg=True 时只要 FFD8，全失败退 webp 兜底"""
    if os.path.exists(fn) and os.path.getsize(fn) > 8192:
        return -1  # 已存在，跳过
    fallback = None
    for u2 in urls:
        try:
            data = http_get(u2, {'User-Agent': UA, 'Accept': 'image/jpeg,image/png,*/*;q=0.8',
                                 'Referer': ref})
        except Exception:
            continue
        if len(data) < 8000:
            continue
        if jpeg and data[:2] == b'\xff\xd8':
            open(fn, 'wb').write(data); return len(data)
        if not jpeg and data[:3] == b'\x00\x00\x00' or data[4:8] == b'ftyp':
            open(fn, 'wb').write(data); return len(data)
        if fallback is None:
            fallback = (fn, data)
    if fallback:
        open(fallback[0], 'wb').write(fallback[1]); return len(fallback[1])
    return 0

def best_bit_rate(bit_rate):
    """取清晰度最高的档位（借鉴 TikTokDownloader/Evil0ctal 的清晰度优先思路）。
    实测 bit_rate 档位按清晰度降序排列且 gear_name 自带档位语义，
    band_width 缺失（adapt 系档位无该字段），故按 size 降序 + gear_name 排除
    lower/lowest 兜底：bit_rate[0] 一般已是最高，但显式排序防档位顺序变化。"""
    br = [b for b in (bit_rate or []) if (b.get('play_addr') or {}).get('url_list')]
    if not br:
        return {}
    def rank(b):
        gear = (b.get('gear_name') or '')
        size = (b.get('play_addr') or {}).get('data_size') or 0
        demoted = ('lowest' in gear) * 2 + ('lower' in gear)  # lowest < lower < 普通
        return (size, -demoted)
    return sorted(br, key=rank, reverse=True)[0]

def play_urls(br):
    """一档 bit_rate 的全部候选 URL：play_addr + play_addr_bak"""
    return ((br.get('play_addr') or {}).get('url_list') or []
            ) + ((br.get('play_addr_bak') or {}).get('url_list') or [])

def process(short, long_url=None):
    if long_url:
        parsed = resolve_long(long_url)
        if not parsed:
            raise RuntimeError('长链里没拿到 aweme_id: %s' % long_url[:120])
        aid, iid, did, ver = parsed
        code = short or aid
    else:
        aid, iid, did, ver = resolve(short)
        code = short
    # 同一作品每次分享会生成不同短链码，按 aweme_id 去重才能挡住重复下载
    prev = index_get(aid)
    if prev and all(os.path.exists(p) and os.path.getsize(p) > 8192 for p in prev):
        print('  已下载过：%s（同一作品换了分享码，跳过）'
              % '、'.join(os.path.basename(p) for p in prev))
        return
    a = fetch_aweme(aid, iid, did, ver)
    user = ((a.get('author') or {}).get('nickname') or 'douyin').replace('/', '_')
    imgs = a.get('images') or []
    outdir = os.path.join(OUTROOT, user)
    os.makedirs(outdir, exist_ok=True)
    print('「%s」@%s  图集=%d  aweme_id=%s' % ((a.get('desc') or '')[:36], user, len(imgs), aid))
    if imgs:
        targets = []
        for i, im in enumerate(imgs, 1):
            base = os.path.join(outdir, '%s_%s_%03d' % (user, code, i))
            urls = (im.get('url_list') or [])[:]
            if len(urls) >= 3:
                urls = [urls[2]] + urls  # index 2 才是 JPEG 原图，前两个是 webp
            urls += (im.get('download_url_list') or []) + (im.get('url_list') or [])
            n = dl(urls, base + '.jpg', jpeg=True)
            line = '  %03d.jpg %s' % (i, ('%dB' % n) if n >= 0 else '已存在')
            targets.append(base + '.jpg')
            if im.get('live_photo_type') == 1:
                vurls = play_urls(best_bit_rate((im.get('video') or {}).get('bit_rate')))
                nv = dl(vurls, base + '.mp4', jpeg=False, ref='https://www.douyin.com/')
                line += ' | live %03d.mp4 %s' % (i, ('%dB' % nv) if nv >= 0 else '已存在')
                targets.append(base + '.mp4')
            print(line)
        if targets and all(os.path.exists(t) and os.path.getsize(t) > 8192 for t in targets):
            index_put(aid, targets)
    else:
        target = os.path.join(outdir, '%s_%s_001.mp4' % (user, code))
        pa = best_bit_rate((a.get('video') or {}).get('bit_rate')).get('play_addr') or {}
        n = dl(pa.get('url_list') or [], target,
               jpeg=False, ref='https://www.douyin.com/')
        print('  纯视频 001.mp4 %s' % ('%dB' % n if n >= 0 else '已存在'))
        if os.path.exists(target) and os.path.getsize(target) > 8192:
            index_put(aid, [target])

CANARY_AID = '7706160692601144179'  # 秦时「下次再见」：结构最全的验证作品（图集+live）

def quick_check():
    """配方体检（快速）：feed 接口连通 + 字段结构。
    返回 (ok, msg)。下载失败时自动调用，区分『作品没了』vs『配方失效』。"""
    try:
        a = fetch_aweme(CANARY_AID, '', '', '370600')
    except Exception as e:
        return False, 'feed 接口异常：%s' % e
    n_img = len(a.get('images') or [])
    has_video = bool((a.get('video') or {}).get('bit_rate'))
    if n_img or has_video:
        return True, '配方正常（canary：图集=%d 视频=%s）' % (n_img, bool(has_video))
    return False, 'feed 返回异常结构（无 images/video 字段），配方可能已失效'

def check():
    """完整体检：三环节连通性（feed 结构 → 图片可下 → live 可下）"""
    ok, msg = quick_check()
    print('[1/3] feed 接口：', msg)
    if not ok:
        sys.exit(1)
    a = fetch_aweme(CANARY_AID, '', '', '370600')
    im = (a.get('images') or [{}])[0]
    urls = (im.get('url_list') or [])
    if len(urls) >= 3:
        urls = [urls[2]] + urls
    try:
        head = http_get(urls[0], {'User-Agent': UA, 'Referer': 'https://www.iesdouyin.com/'})[:2]
        print('[2/3] 图片下载：%s（FFD8=%s）' % ('正常' if head == b'\xff\xd8' else '非 JPEG 头 %r' % head, head == b'\xff\xd8'))
    except Exception as e:
        print('[2/3] 图片下载：异常 %s' % e)
    if im.get('live_photo_type') == 1:
        vu = play_urls(best_bit_rate((im.get('video') or {}).get('bit_rate')))
        try:
            head = http_get(vu[0], {'User-Agent': UA, 'Referer': 'https://www.douyin.com/'})[4:8]
            print('[3/3] live 视频：%s（ftyp=%s）' % ('正常' if head == b'ftyp' else '异常头 %r' % head, head == b'ftyp'))
        except Exception as e:
            print('[3/3] live 视频：异常 %s' % e)
    else:
        print('[3/3] live 视频：canary 无 live 块（跳过，若大量作品缺 live 请人工复查）')

def _ver_tuple(s):
    """版本号转元组：(2,10,0)>(2,9,0)。字符串比较在 2.10<2.9 上翻车"""
    return tuple(int(p) if p.isdigit() else 0
                 for p in re.split(r'[.\-+]', str(s)))

def check_update():
    """启动时查远端版本，有新版只打印提示（不下载不替换）。
    断网/404/JSON 坏一律静默；checked_at 只在请求成功后写，防网络抖动锁死 24h。"""
    cache_path = os.path.join(CACHE_DIR, 'update.json')
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if time.time() - cache.get('checked_at', 0) < CHECK_INTERVAL:
        return
    try:
        raw = http_get(UPDATE_CHECK_URL, {'User-Agent': UA}, timeout=3, tries=1)
        info = json.loads(raw.decode('utf-8', 'replace'))
    except Exception:
        return
    cache['checked_at'] = time.time()
    latest = str(info.get('version') or '')
    if latest and _ver_tuple(latest) > _ver_tuple(VERSION):
        if cache.get('prompted') != latest:      # 同一版本只提醒一次，避免天天打扰
            print('! 有新版本 v%s（当前 v%s）— %s'
                  % (latest, VERSION, info.get('note') or ''))
            if info.get('url'):
                print('  取新版: %s' % info['url'])
            cache['prompted'] = latest
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except Exception:
        pass

def main():
    argv = [a for a in sys.argv[1:] if a != '--no-update-check']
    if '--version' in sys.argv[1:]:
        print('douyin_dl ' + VERSION); sys.exit(0)
    if len(argv) == 1 and argv[0] == '--check':
        check(); sys.exit(0)
    if len(argv) == len(sys.argv[1:]):  # 未显式跳过时才查更新
        check_update()
    if not argv:
        print(__doc__); sys.exit(1)
    text = ' '.join(argv)
    longs = [u for u in re.findall(r'https?://[^\s]+', text)
             if resolve_long(u)]
    shorts = re.findall(r'v\.douyin\.com/([A-Za-z0-9_-]+)', text)
    if not longs and not shorts and re.fullmatch(r'[A-Za-z0-9_-]{8,}', text.strip()):
        shorts = [text.strip()]
    if not longs and not shorts:
        print('没找到抖音链接（需要 v.douyin.com 短链或 iesdouyin/douyin.com 长链）')
        sys.exit(1)
    done = set()
    for u in longs:
        aid = resolve_long(u)[0]
        if aid in done:
            continue
        done.add(aid)
        try:
            process(None, long_url=u)
        except Exception as e:
            print('[%s] 失败: %s' % (u[:60], e))
            ok, msg = quick_check()
            print('  配方体检：%s' % msg)
    for s in shorts:
        if s in done:
            continue
        done.add(s)
        try:
            process(s)
        except Exception as e:
            print('[%s] 失败: %s' % (s, e))
            ok, msg = quick_check()
            print('  配方体检：%s' % msg)

if __name__ == '__main__':
    main()
