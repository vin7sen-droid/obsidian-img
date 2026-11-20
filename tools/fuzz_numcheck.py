#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""summary_check 六条机检 fuzz 测试（2026-11-20 重写版）

六类缺陷：
- missing_number: 摘要漏掉文章关键数字（从文章删掉数字+单位）
- wrong_number:   摘要数字写错（同单位，数值改 ±30%~80%）
- wrong_unit:     单位写错（数值不变，单位换成同族另一单位）
- scale_error:    量级词写错（数值不变，亿/万/万亿 互换）
- missing_unit:   单位丢失（数字保留，单位删掉）
- extra_number:   摘要出现文章没有的数字（额外注入一个数字）

验收目标：坏样本拦截率 ≥80%，干净样本零误报。
"""

import random
import re

random.seed(20261120)

# ============================================================
# 数字解析器（复合单位整体消费版）
# ============================================================

# SI 前缀因子（用于 兆瓦/吉瓦/千瓦/毫瓦/毫安 这类 SI 前缀单位）
SI_PREFIX_FACTOR = {'兆': 1e6, '吉': 1e9, '千': 1e3, '毫': 1e-3}
# 中文量级词
SCALE_WORDS = {'万亿': 1e12, '亿': 1e8, '万': 1e4, '千': 1e3}

# 复合 SI 单位（长词优先）
SI_UNITS = ['千瓦时', '兆瓦时', '吉瓦时', '瓦时', '兆瓦', '吉瓦', '千瓦', '毫瓦', '毫安时', '毫安', '瓦']
# 量级词+货币/普通单位
ZH_UNIT_UNITS = ['亿美元', '亿美元', '亿元', '万元', '美元', '元', '公里', '千米', '厘米', '毫米', '米',
                 '英里', '英尺', '英寸', '吨', '颗', '辆', '台', '家', '人', '次', '个', '条', '度',
                 '个月', '月', '日', '年', '小时', '分钟', '毫秒', '秒',
                 'GB', 'MB', 'TB', 'KB', 'Mbps', 'Gbps', 'nm', 'W', 'kW', 'MW', 'GW']
# 量级词（长词优先）
ZH_SCALES = ['万亿', '亿', '万', '千', '兆', '京']

_NUM = r'(\d[\d,]*(?:\.\d+)?)'


def _mk_unit_alt(units):
    """把单位列表编译成 (长词|短词) 交替，长词在前"""
    return '|'.join(re.escape(u) for u in units)


def parse_number(text):
    """解析文本中的所有数字，返回 list[dict]

    dict 字段：
      raw       原始匹配串
      raw_value 不含量级/单位前缀的数值
      scale     中文量级因子（1 表示无）
      unit      单位串（'' 表示无）
      si_value  归一后的值 = raw_value * scale * (SI 前缀因子 if SI 单位)
      type      version/si_unit/zh_money/zh_unit/zh_scale/percent/plain
      pos       起始位置
    """
    results = []
    claimed = []  # 已消费区间

    def free(s, e):
        return all(e <= cs or s >= ce for cs, ce in claimed)

    def add(m, val, scale, unit, typ, si=None):
        raw = m.group(0)
        try:
            raw_value = float(m.group(1).replace(',', ''))
        except ValueError:
            return
        si_value = si if si is not None else val * scale
        results.append({
            'raw': raw, 'raw_value': raw_value, 'scale': scale,
            'unit': unit, 'si_value': si_value, 'type': typ, 'pos': m.start(),
        })

    # 1. 版本号 x.y.z（先消费，防拆散）
    pat = re.compile(r'\d+\.\d+\.\d+')
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        raw = m.group(0)
        try:
            parts = [float(p) for p in raw.split('.')]
        except ValueError:
            continue
        results.append({'raw': raw, 'raw_value': parts[0], 'scale': 1, 'unit': '',
                        'si_value': parts[0], 'type': 'version', 'pos': s})

    # 2. 百分比
    pat = re.compile(_NUM + r'\s*%')
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        add(m, float(m.group(1).replace(',', '')), 1, '%', 'percent')

    # 3. 复合 SI 单位：数字 + （可选中文量级）+ SI 单位
    #    例：12 亿千瓦时 / 5000 毫安时 / 150 兆瓦 / 120W(按 W 单独处理在 4)
    pat = re.compile(_NUM + r'\s*(?:(万亿|亿|万|千)\s*)?(' + _mk_unit_alt(SI_UNITS) + r')')
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        num = float(m.group(1).replace(',', ''))
        scale = SCALE_WORDS.get(m.group(2) or '', 1)
        unit = m.group(3)
        # SI 前缀因子：兆/吉/千/毫 开头的复合单位
        prefix = SI_PREFIX_FACTOR.get(unit[0], 1) if unit[0] in SI_PREFIX_FACTOR else 1
        add(m, num, scale, unit, 'si_unit', si=num * scale * prefix)

    # 4. 英文 SI 前缀单位：120W / 3nm / 2.5GB / 100Mbps / 550MW
    pat = re.compile(_NUM + r'\s*([kKmMgGtT]?)(W|nm|GB|MB|TB|KB|Mbps|Gbps)\b')
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        num = float(m.group(1).replace(',', ''))
        pfx = {'k': 1e3, 'K': 1e3, 'm': 1e-3, 'M': 1e6, 'g': 1e9, 'G': 1e9, 't': 1e12, 'T': 1e12}.get(m.group(2) or '', 1)
        unit = (m.group(2) or '') + m.group(3)
        add(m, num, 1, unit, 'si_unit', si=num * pfx)

    # 5. 量级词 + 货币/普通单位：12.5 亿元 / 120 亿美元 / 550 公里 / 50 毫秒
    pat = re.compile(_NUM + r'\s*(?:(万亿|亿|万|千)\s*)?(' + _mk_unit_alt(ZH_UNIT_UNITS) + r')(?![瓦安时月度个条人台家次辆颗])')
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        num = float(m.group(1).replace(',', ''))
        scale = SCALE_WORDS.get(m.group(2) or '', 1)
        unit = m.group(3)
        add(m, num, scale, unit, 'zh_money' if unit[-1] in '元' else 'zh_unit')

    # 6. 纯量级词：1.8 万亿 / 200 万（后面不接单位字符，防吞 兆瓦/千瓦时）
    pat = re.compile(_NUM + r'\s*(' + _mk_unit_alt(ZH_SCALES) + r')(?![瓦安时月日元美元度个条人台家次辆颗 GBMBTKbpsnmgwkMGW毫千吉兆])')
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        num = float(m.group(1).replace(',', ''))
        scale = SCALE_WORDS.get(m.group(2), 1e12) if m.group(2) == '万亿' else SCALE_WORDS.get(m.group(2), 1)
        if m.group(2) == '兆':
            scale = 1e12
        elif m.group(2) == '京':
            scale = 1e16
        add(m, num, scale, '', 'zh_scale')

    # 7. 裸数字
    pat = re.compile(_NUM)
    for m in pat.finditer(text):
        s, e = m.span()
        if not free(s, e):
            continue
        claimed.append((s, e))
        add(m, float(m.group(1).replace(',', '')), 1, '', 'plain')

    results.sort(key=lambda r: r['pos'])
    return results


def extract_numbers_with_context(text, window=40):
    nums = parse_number(text)
    for n in nums:
        s = max(0, n['pos'] - window)
        e = min(len(text), n['pos'] + len(n['raw']) + window)
        n['context'] = text[s:e]
    return nums


# ============================================================
# 检查器（六条机检）
# ============================================================

APPROX_RE = re.compile(r'(超过|约|近|逾|将近|以上|以下|左右|多|余)\s*$')


def is_approx(text, pos):
    """pos 前是否有约数词"""
    if pos <= 0:
        return False
    return bool(APPROX_RE.search(text[max(0, pos - 4):pos]))


def normalize_unit(u):
    if not u:
        return ''
    u = u.strip()
    aliases = {'公里': '千米', '分钟': 'min', '小时': 'h', '秒': 's', '毫秒': 'ms',
               'MB': 'MB', '兆瓦': 'MW', '吉瓦': 'GW', '千瓦': 'kW', '毫瓦': 'mW',
               'W': 'W', 'kW': 'kW', 'MW': 'MW', 'GW': 'GW'}
    return aliases.get(u, u)


def check_summary(summary, article, debug=False):
    """返回 (flags, details)"""
    flags = []
    details = {'summary_nums': [], 'article_nums': [], 'matched': [], 'issues': []}

    sum_nums = extract_numbers_with_context(summary)
    art_nums = extract_numbers_with_context(article)
    details['summary_nums'] = [{k: n[k] for k in ('raw', 'unit', 'type', 'pos', 'si_value')} for n in sum_nums]
    details['article_nums'] = [{k: n[k] for k in ('raw', 'unit', 'type', 'pos', 'si_value')} for n in art_nums]

    art_matched = [False] * len(art_nums)

    for sn in sum_nums:
        best_idx, best_score = -1, 0

        # 阶段1：按位置匹配（摘要与文章是同主题文本，同位置≈同数字）
        # 内容吻合才给分（值/单位/SI 至少一个相同）——否则错位一格的数字会
        # 连锁错配（删一个数后全部前移，60 分档把每个数字都配到邻居上）
        for ai, an in enumerate(art_nums):
            if art_matched[ai]:
                continue
            pos_diff = abs(sn['pos'] - an['pos'])
            if pos_diff > 15:
                continue
            agree = (abs(sn['raw_value'] - an['raw_value']) < 1e-9
                     or normalize_unit(sn['unit']) == normalize_unit(an['unit'])
                     or abs(sn['si_value'] - an['si_value']) < 1e-9)
            score = 100 if pos_diff <= 5 else 60
            if not agree:
                score = 40  # 位置接近但内容不吻合：不足阈值，宁缺勿错
            if score > best_score:
                best_score, best_idx = score, ai

        # 阶段2：按 SI 值匹配
        if best_idx < 0:
            for ai, an in enumerate(art_nums):
                if art_matched[ai]:
                    continue
                if sn['si_value'] == an['si_value'] and sn['si_value'] != 0:
                    best_score, best_idx = 80, ai
                    break
                if sn['si_value'] and an['si_value']:
                    rel = abs(sn['si_value'] - an['si_value']) / max(abs(sn['si_value']), abs(an['si_value']))
                    if rel < 0.05:
                        best_score, best_idx = 70, ai
                        break

        if best_idx >= 0 and best_score >= 50:
            art_matched[best_idx] = True
            an = art_nums[best_idx]
            details['matched'].append({'summary': sn['raw'], 'article': an['raw']})

            sn_u = normalize_unit(sn['unit'])
            an_u = normalize_unit(an['unit'])
            rel = 0
            if sn['si_value'] or an['si_value']:
                rel = abs(sn['si_value'] - an['si_value']) / max(abs(sn['si_value']), abs(an['si_value']), 1e-12)

            # wrong_number：单位一致（含都为空）但数值差 ≥5%
            if sn_u == an_u and rel >= 0.05 and sn['type'] not in ('version',):
                # 约数豁免：双方都是约数表述
                if is_approx(summary, sn['pos']) and is_approx(article, an['pos']):
                    pass
                else:
                    flags.append('wrong_number')
                    details['issues'].append({'type': 'wrong_number', 'summary': sn['raw'], 'article': an['raw']})

            # wrong_unit：原始数值相同但单位不同
            if sn_u != an_u and abs(sn['raw_value'] - an['raw_value']) < 1e-9 and sn_u and an_u:
                flags.append('wrong_unit')
                details['issues'].append({'type': 'wrong_unit', 'summary': sn['raw'], 'article': an['raw']})

            # scale_error：unit 相同（含都为空）但中文量级因子不同
            if (sn_u == an_u and sn['scale'] != an['scale']
                    and sn['type'] not in ('version', 'plain')
                    and an['type'] not in ('version', 'plain')):
                flags.append('scale_error')
                details['issues'].append({'type': 'scale_error', 'summary': sn['raw'], 'article': an['raw']})

            # missing_unit：摘要无单位、文章有单位
            if not sn_u and an_u and sn['type'] != 'version':
                flags.append('missing_unit')
                details['issues'].append({'type': 'missing_unit', 'summary': sn['raw'], 'article': an['raw']})
        else:
            # 摘要数字在文章里找不到（约数、纯数词豁免）
            if sn['type'] in ('percent', 'version'):
                continue
            if is_approx(summary, sn['pos']):
                continue
            flags.append('extra_number')
            details['issues'].append({'type': 'extra_number', 'summary': sn['raw']})

    # 反向：文章关键数字在摘要里缺失（只查第一句——摘要天然只覆盖导语）
    first_end = article.find('。')
    if first_end == -1:
        first_end = len(article)
    for ai, an in enumerate(art_nums):
        if art_matched[ai]:
            continue
        if an['type'] in ('version', 'chinese'):
            continue
        if an['pos'] >= first_end:
            continue  # 非导语句，摘要可不提
        if 1900 <= an['raw_value'] <= 2100 and an['unit'] in ('', '年', '个月', '月'):
            continue
        if is_approx(article, an['pos']):
            continue
        flags.append('missing_number')
        details['issues'].append({'type': 'missing_number', 'article': an['raw'], 'context': an['context']})

    uniq = []
    for f in flags:
        if f not in uniq:
            uniq.append(f)
    return uniq, details


# ============================================================
# Fuzz 生成器（保证每类缺陷真的注入）
# ============================================================

BASE_ARTICLES = [
    "公司今日宣布融资 12.5 亿元，估值达 120 亿美元。董事会已批准该轮融资。",
    "新款手机搭载 3nm 工艺芯片，性能提升 40%。首发骁龙 8 Gen 4，跑分破 200 万。",
    "最新财报显示营收 850 亿元，同比增长 25%。净利润 120 亿元，超预期。",
    "数据中心功耗达 150 兆瓦，年耗电 12 亿千瓦时。采用液冷技术降低 PUE 至 1.15。",
    "漏洞 CVSS 评分 9.8，影响版本 2.4.1 及以下。已发布补丁 2.4.2，建议立即升级。",
    "卫星发射成功，轨道高度 550 公里，倾角 53 度。携带 60 颗通信卫星，总重 18 吨。",
    "电池容量 5000 毫安时，支持 120W 快充，19 分钟充满。续航测试 14 小时。",
    "AI 模型参数量 1.8 万亿，训练耗时 3 个月，算力 2.5 亿 GPU 小时。推理延迟 50 毫秒。",
]

# 同族单位对（wrong_unit 用：数值不变、单位互换）
UNIT_PAIRS = [
    ('公里', '米'), ('兆瓦', '千瓦'), ('小时', '分钟'), ('GB', 'MB'),
    ('吨', '千克'), ('毫秒', '秒'), ('英里', '公里'),
]
# 量级词对（scale_error 用：数值不变、量级互换；长词在前防「万亿」被拆成「万万」）
SCALE_PAIRS = [('万亿', '亿'), ('亿', '万'), ('万', '亿'), ('亿', '万亿')]


def _find_first(nums, pred):
    for n in nums:
        if pred(n):
            return n
    return None


def inject_defect(article, defect, rng):
    """向文章注入缺陷，返回 (summary, ok)。ok=False 表示该文章无法注入此缺陷。"""
    nums = parse_number(article)

    if defect == 'missing_number':
        # 从文章第一句删掉一个数字+单位（摘要天然只覆盖导语）
        first_end = article.find('。')
        if first_end == -1:
            first_end = len(article)
        cand = [n for n in nums if n['type'] in ('si_unit', 'zh_money', 'zh_unit', 'zh_scale')
                and n['pos'] < first_end
                and not (1900 <= n['raw_value'] <= 2100)]
        if not cand:
            return article, False
        n = rng.choice(cand)
        # 摘要 = 文章删掉该数字+单位（连带前面一个空格）
        s = n['pos']
        e = s + len(n['raw'])
        # 向后吃掉紧跟的单位字符已经含在 raw 里；再吃一个空格
        while e < len(article) and article[e] == ' ':
            e += 1
        summary = article[:s] + article[e:]
        return summary, True

    if defect == 'wrong_number':
        # 同单位，数值改 ±30%~80%
        cand = [n for n in nums if n['type'] in ('si_unit', 'zh_money', 'zh_unit', 'zh_scale', 'percent')]
        if not cand:
            return article, False
        n = rng.choice(cand)
        factor = rng.choice([0.3, 0.5, 1.5, 1.8, 2.0])
        new_val = round(n['raw_value'] * factor, 2)
        if new_val == n['raw_value']:
            return article, False
        # 替换 raw 中的数字部分
        m = re.match(r'(\d[\d,]*(?:\.\d+)?)(.*)', n['raw'], re.S)
        if not m:
            return article, False
        new_raw = str(new_val) + m.group(2)
        s = n['pos']
        e = s + len(n['raw'])
        summary = article[:s] + new_raw + article[e:]
        return summary, True

    if defect == 'wrong_unit':
        # 数值不变，单位换成同族另一单位
        for n in nums:
            for u1, u2 in UNIT_PAIRS:
                if n['unit'] == u1:
                    new_raw = n['raw'].replace(u1, u2, 1)
                    s = n['pos']
                    return article[:s] + new_raw + article[s + len(n['raw']):], True
                if n['unit'] == u2:
                    new_raw = n['raw'].replace(u2, u1, 1)
                    s = n['pos']
                    return article[:s] + new_raw + article[s + len(n['raw']):], True
        return article, False

    if defect == 'scale_error':
        # 数值不变，量级词互换
        for n in nums:
            raw = n['raw']
            for s1, s2 in SCALE_PAIRS:
                if s1 in raw:
                    new_raw = raw.replace(s1, s2, 1)
                    s = n['pos']
                    return article[:s] + new_raw + article[s + len(raw):], True
        return article, False

    if defect == 'missing_unit':
        # 数字保留，单位删掉
        cand = [n for n in nums if n['type'] in ('si_unit', 'zh_money', 'zh_unit', 'percent')]
        if not cand:
            return article, False
        n = rng.choice(cand)
        if n['unit'] == '%':
            new_raw = n['raw'].replace('%', '', 1)
        else:
            new_raw = n['raw'].replace(n['unit'], '', 1)
        s = n['pos']
        summary = article[:s] + new_raw + article[s + len(n['raw']):]
        return summary, True

    if defect == 'extra_number':
        # 摘要里额外加一个文章没有的数字
        rng_val = rng.choice([88, 250, 7.7, 666, 1.23])
        return article + f"（另有 {rng_val} 亿元投资。）", True

    return article, False


DEFECTS = ['missing_number', 'wrong_number', 'wrong_unit', 'scale_error', 'missing_unit', 'extra_number']


def generate_fuzz_samples(n_bad=120, n_clean=60):
    rng = random.Random(20261120)
    clean_samples = [(a, a) for a in BASE_ARTICLES]
    # 干净样本再多一些变体：摘要=文章前 60%（截断句）
    for a in BASE_ARTICLES:
        cut = int(len(a) * 0.6)
        # 找句号截断
        m = None
        for m2 in re.finditer(r'[。；]', a[:cut]):
            m = m2
        if m and m.end() >= 15:
            clean_samples.append((a[:m.end()], a))

    bad_samples = []
    i = 0
    attempts = 0
    while len(bad_samples) < n_bad and attempts < n_bad * 10:
        attempts += 1
        defect = DEFECTS[i % len(DEFECTS)]
        i += 1
        article = rng.choice(BASE_ARTICLES)
        summary, ok = inject_defect(article, defect, rng)
        if not ok or summary == article:
            continue
        bad_samples.append((summary, article, defect))
    return clean_samples, bad_samples


# ============================================================
# 跑批
# ============================================================

def main():
    clean_samples, bad_samples = generate_fuzz_samples(120, 60)
    print(f'生成样本：干净 {len(clean_samples)}，有缺陷 {len(bad_samples)}')

    clean_pass = 0
    clean_fp = []
    for summary, article in clean_samples:
        flags, _ = check_summary(summary, article)
        if not flags:
            clean_pass += 1
        else:
            clean_fp.append((flags, summary, article))

    print(f'干净样本通过: {clean_pass}/{len(clean_samples)}')
    for flags, s, a in clean_fp[:5]:
        print(f'  误报 {flags}: {s!r}')

    defect_stats = {d: {'total': 0, 'caught': 0} for d in DEFECTS}
    misses = []
    for summary, article, defect in bad_samples:
        flags, _ = check_summary(summary, article)
        defect_stats[defect]['total'] += 1
        if defect in flags:
            defect_stats[defect]['caught'] += 1
        else:
            misses.append((defect, flags, summary, article))

    print()
    print('=== 缺陷拦截统计（坏样本应 ≥80%）===')
    total_caught = total = 0
    for d, st in defect_stats.items():
        rate = st['caught'] / st['total'] * 100 if st['total'] else 0
        total_caught += st['caught']
        total += st['total']
        print(f'{d}: {st["caught"]}/{st["total"]} = {rate:.1f}%')
    print(f'总体拦截率: {total_caught}/{total} = {total_caught/total*100:.1f}%')
    print(f'干净样本零误报: {clean_pass == len(clean_samples)}')

    if misses:
        print()
        print(f'=== 漏报明细（前 10 条）===')
        for defect, flags, s, a in misses[:10]:
            print(f'[{defect}] flags={flags}')
            print(f'  摘要: {s}')
            print(f'  文章: {a}')

    return total_caught / total >= 0.8 and clean_pass == len(clean_samples)


if __name__ == '__main__':
    ok = main()
    print()
    print('FUZZ ' + ('PASS ✅' if ok else 'FAIL ❌'))
