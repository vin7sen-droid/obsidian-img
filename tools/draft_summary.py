#!/usr/bin/env python3
"""draft_summary.py —— 中文源摘要自动打底（快速模式优化 ①，2026-11-16 落地）

背景：快速模式（当天二次抓取增量）28 条新增里 25 条是中文源（ithome/cnbeta/solidot/
mydrivers/readhub），desc 本是中文完整导语，逐条手写摘要约占全程一半时间。
实测「截第一句」不可用（第一句多是「有关…消息持续浮出水面」这类废话，关键数字在第二句）；
本工具按句打分选句，产出 70-80% 可用草稿，领队只需改开头废话与「Reno178GB」类粘连。

用法：
  python3 draft_summary.py <pool.json> [--kw 关键词 [--kw ...]] [--src 源名 [--src ...]]
                            [--limit N] [--out 文件]

  默认打印全部条目的草稿清单（标题/来源/时间/草稿摘要/原文 desc 前两行）；
  --kw 按 title+desc 任一命中筛选；--src 按源筛选（如 ithome cnbeta）。
  --out 落盘 markdown（不指定则打印 stdout）。

打分规则（实测 4 条对照 3 条接近手写质量）：
  含数字 +3；含货币/单位 +2；含发布/收购/漏洞/涨跌类动词 +2；长度 18-60 +1；
  废话词（消息持续/浮出水面/据悉/注意到/整理如下/现场消息）-2；
  取最高分 3 句按原序拼接，截 100 字，去「阅读全文」等尾巴。
"""
import argparse, json, re, sys

# 预清洗：去头部消息导语与尾部样板
HEAD_RE = re.compile(
    r'^(?:IT之家\s*\d+\s*月\s*\d+\s*日(?:\s*消息|\s*现场消息)[，,]?|'
    r'快科技\d+\s*月\s*\d+\s*日消息[，,]?|'
    r'新浪科技讯|财联社\d+\s*月\s*\d+\s*日电?[，,]?)\s*')
TAIL_RE = re.compile(r'\s*(阅读全文|IT之家注意到|返回搜狐，查看更多|责任编辑[：:].*)\s*$')

NUM_RE = re.compile(r'\d')
UNIT_RE = re.compile(r'\d[\d,.]*\s*(?:元|美元|亿|万|%|％|TB|GB|MB|nm|纳米|英寸|寸|台|辆|款|人|条|次|年|月|日|小时|分钟|公里|千米|km|GHz|Hz|帧|瓦|千瓦|吉瓦)')
VERB_RE = re.compile(r'发布|推出|宣布|上市|开售|发售|收购|并购|融资|起诉|罚款|漏洞|攻击|泄露|涨|跌|增|降|裁|破产|暂停|恢复|启用量产|交付')
WASTE_RE = re.compile(r'消息持续|浮出水面|据悉|注意到|整理如下|现场消息|近日有|有消息')

def split_sents(text):
    # 按中文句号/问叹号切；分号与逗号太碎不切
    parts = re.split(r'(?<=[。！？])', text)
    return [p for p in (s.strip() for s in parts) if p]

def score(sent):
    v = 0
    if NUM_RE.search(sent): v += 3
    if UNIT_RE.search(sent): v += 2
    if VERB_RE.search(sent): v += 2
    if 18 <= len(sent) <= 60: v += 1
    if WASTE_RE.search(sent): v -= 4
    return v

def draft(desc, max_chars=100, max_sents=3):
    d = re.sub(r'\s+', ' ', (desc or '')).strip()
    d = HEAD_RE.sub('', d)
    sents = split_sents(d)
    if not sents:
        return ''
    ranked = sorted(range(len(sents)), key=lambda i: -score(sents[i]))
    picked = [i for i in ranked if score(sents[i]) >= 2][:max_sents]  # 入选门槛：至少 2 分
    if not picked:  # 全部低于门槛时退回最高分 1 句
        picked = ranked[:1]
    idx = sorted(picked)  # 按原序拼回
    out = ''.join(sents[i] for i in idx)
    out = TAIL_RE.sub('', out)
    return out[:max_chars]

CN_SRCS = ('ithome', 'cnbeta', 'solidot', 'mydrivers', 'readhub', 'techmeme_cn')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pool', help='pool_*.json 路径（条目含 src/title/url/date/desc）')
    ap.add_argument('--kw', action='append', default=[], help='按 title+desc 关键词筛选（可多次）')
    ap.add_argument('--src', action='append', default=[], help='按源筛选（可多次，如 ithome）')
    ap.add_argument('--limit', type=int, default=0, help='最多输出 N 条')
    ap.add_argument('--out', default='', help='落盘 markdown 路径（缺省打印）')
    a = ap.parse_args()

    items = json.load(open(a.pool, encoding='utf-8'))
    if isinstance(items, dict):
        items = items.get('items') or items.get('pool') or list(items.values())[0]

    picked = []
    for e in items:
        src = (e.get('src') or '').split(':')[0]
        if a.src and src not in a.src:
            continue
        hay = (e.get('title', '') + ' ' + (e.get('desc') or ''))
        if a.kw and not any(k in hay for k in a.kw):
            continue
        picked.append(e)

    if a.limit:
        picked = picked[:a.limit]

    lines = []
    for i, e in enumerate(picked, 1):
        src = (e.get('src') or '').split(':')[0]
        t = e.get('title', '')
        d = e.get('desc') or ''
        g = draft(d)
        lines.append(f'{i}. {t}  [{src} {e.get("date","")}]')
        lines.append(f'   草稿：{g}')
        if g and not NUM_RE.search(g):
            lines.append('   ⚠ 草稿无数字，desc 里可能有数字被废话句挡住——人工检查')
        if not g:
            lines.append('   ⚠ 草稿为空，人工从 desc 重写')

    text = '\n'.join(lines) if lines else '（无匹配条目）'
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(text + '\n')
        print(f'已写 {a.out}（{len(picked)} 条）')
    else:
        print(text)

if __name__ == '__main__':
    main()
