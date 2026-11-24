#!/usr/bin/env python3
"""semantic_check.py — LLM 语义/文笔初筛（2026-11-20 用户批「做，先小样本实测」）。
成对送（英文原句 + 中文译文）给 GLM，只输出 flagged 行；清单落 digests/。
用法:
  python3 tools/semantic_check.py --self-test          # 20 对样本验真
  python3 tools/semantic_check.py --pairs pairs.json   # [{en,zh},...] 通用
  python3 tools/semantic_check.py --note <笔记.md>      # 从笔记抽中英成对（暂未接）
凭据: /root/.config/tagllm/{key,base}（与 tag_llm.py 同源，免费中转）。
教训（tag_llm 实测）：推理模型 max_tokens 必须 ≥2000，否则 reasoning 吃光返空。
"""
import json, os, re, sys, urllib.request

KEY = open("/root/.config/tagllm/key").read().strip()
BASE = open("/root/.config/tagllm/base").read().strip()
# 多模型回退（2026-11-20 用户定）：启动时 ping 探测延迟，快者先用；批内失败切下一个
MODELS = [
  "ywd/nvidia/nemotron-3-ultra-550b-a55b",   # 首选：probe 稳定（5-8s、hit2/fp1），low 档全量约 3-4 分钟
          "wb/deepseek-v4.1-flash",                    # 备用（2026-11-23 实测：质量达标但 low 档全量约 12 分钟，慢 5 倍，仅 nemotron 不可用时用）
          "ywd/minimaxai/minimax-m3.1-flash",       # 2026-11-20 渠道下线（500 get_channel_failed），保留观察
          "ywd/z-ai/glm-5.3-flash",                 # 判定质量标杆（10/10 验真 0 误报），慢/偶超时
          # "ywd/stepfun-ai/step-5-preview",          # 2026-11-23 移除：probe 每次超时 150s 白耗
          ]
MODEL = MODELS[0]
OUT_DIR = "/root/workspace/digests"
import datetime as _dt
TODAY = _dt.datetime.now().strftime("%Y-%m-%d")
PROMPT_HEAD = """你是中英翻译质检员。对每对「英文原句/中文译文」判断译文是否有以下问题：
1) 语义偏差（译反、译错、漏译关键信息）
2) 数字/量级错误（billion→亿、单位、货币）
3) 术语错（AI 领域：agent 保留英文、token 保留英文、yield=良率、foundry=晶圆代工厂、grounding=落地/锚定、Elo=等级分；人名用通行中文译名；personal intelligence=个人智能（产品名，非「个人情报」）；blowup（PDE 语境）=解的爆破/奇性爆发，不得译作「奇点」（数学所指不同）；slop（AI 低质产出语境）=低质/注水内容，不译作「垃圾」以外的贬义泛称；open-weight=开放权重；distillation=蒸馏）
4) 严重不通顺（读不懂/病句/直译腔到影响理解）
5) 方向性习语：英文 "be X (months/weeks/days/years) away" = 距离发生只剩 X（不是「还需要/要等 X」）
6) EN 端可能只有标题（正文/描述缺失）。此时中文摘要含标题之外的细节是正常的（从多源补充），不算「添加信息」「过度翻译」；只报：主体/公司错误（如 Alphabet 译成谷歌）、夸大（Not Reachable→大规模）、技术栈或机制臆断（原文未提却断言某语言/框架）、报告数与风险混淆；不报「补充了细节」本身。
7) EN 端可能是多源拼接：[源名] 分隔的若干段是同一事件的不同媒体报道，中文摘要只要与任一段吻合即属正确，「另一段没提到」不算臆造；出现「（原文摘录在此截断，未出现的细节不算臆造）」时该源描述被截断，摘要写到截断处之外的细节不判臆造——但主体/公司/机制/量级错仍要报。
8) 中文摘要里的「HN 讨论 N 分／M 评论」「Hacker News·N分」是条目自带的社区热度元数据（我们标注的，不来自原文），不是臆造；源名括注同理。
9) **时间锚点（2026-11-23 加，最重要）**：当前真实时间是 {TODAY}，你的训练知识很可能停在更早年份。
   · 你**不掌握**今天的现实，不得以「据我所知 X 不存在／未发布／未发生」「现任是某人」「实际上不是他」「该成果尚未成立」「现在是某年」这类**记忆里的过时事实**否定译文。
   · 判断依据只有两样：给出的英文原文 与 中文译文，两者对不对得上。原文有的译文写了就对；原文没有的译文加了才报。
   · 不得因「这模型版本听着不像真的」「这人不是现任」「该研究还没被证明」而报错。
10) **多源标记**：EN 端出现 [源名] 分隔的多段或标注「正文节选」，说明摘要综合多来源；只有**所有段落都没有**该细节时才判臆造。

【只报硬伤，不要挑刺】
- 译文准确、可读、只是「不是我会这么写」→ 不报。宁可漏，不要凑数。
- 不报：标点/空格/顿号用法、标题里加不加年份、加不加逗号、语气轻重、同义近义词偏好（如「治愈声明」vs「治愈主张」、「险情」vs「未遂事故」二选一都能读通）、专名是否「建议保留英文」但译文已可辨认。
- 不臆测原文背景：只依据给出的英文判断，不要引入「这可能是虚构」「该股可能不存在」这类外部猜测。
- 判定为正确时不要输出任何条目（不写「译文正确」这类说明）。
- 【术语：只报明确译错，不报可选译法】2026-11-21 实测教训：模型曾因「blowup 译作解的奇点」判错，
  而改动方向反而是错的（blowup 在 PDE 语境确指解的爆破/奇性爆发）。规则：
  · 一个术语有多个学界通行译法时（agent=智能体/AI代理、blowup=爆破/奇性爆发、token=词元/标记），
    只要译文用的是通行译法之一，一律不报；只在改变了所指对象（如把「爆破」写成「奇点」这种
    数学含义不同的词）时才报，并给出你判断的正确译法。
  · 不要仅因「我更倾向另一种说法」而报术语问题。
- 【数字：正文是唯一判据】标题与正文数字不一致时，以原文为准判哪个错，并明确指出该改哪一处；
  但若两个表述在数量级上等价（近1000万 = 近千万），不要报「不一致」，那不是错误。
- 【主体归属：原文写了就不算错】原文自己把某产品归到某公司名下（如「SpaceX's AI coding platform,
  Cursor」）时，中文摘要沿用或省略该归属都属正常，不得判为「主体/公司错误」；只有当中文引入了
  原文完全没有的公司归属时才算错。
只报「确有问题的行」，没问题的跳过。输出 JSON 数组：[{"idx": 行号, "type": "语义|数字|术语|文笔", "why": "一句话"}]。没有问题输出 []。只输出 JSON 数组（首字符必须是 [），不要解释，不要编号列表或任何前言后语。"""


def _chat_once(model, messages, timeout=240, max_tokens=None):
    # 推理模型思考档（用户 2026-11-20 实测定：默认 none —— 18 对样本三轮 none/low 判定完全相同（9/9 命中、1/9 误报），耗时 none 中位 17.0s vs low 22.5s；长文本 79 对 none 137.6s vs low 268.4s）
    # 想复核漏检时 SEMANTIC_REASONING=low；网关不认 none 会 400 → 自动去参数重试（回落模型默认档）
    if max_tokens is None:
        max_tokens = int(os.environ.get("SEMANTIC_MAX_TOKENS", "16000"))
    body_d = {"model": model, "messages": messages,
              "max_tokens": max_tokens, "temperature": 0.1,
              "reasoning_effort": os.environ.get("SEMANTIC_REASONING", "low")}   # 默认 low（用户 2026-11-23 定）；只想抓硬伤求快：SEMANTIC_REASONING=none（150s vs 3-4 分钟）
    body = json.dumps(body_d).encode()
    r = urllib.request.Request(BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            d = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 400:  # 网关不认 reasoning_effort 参数 → 去掉重试
            body_d.pop("reasoning_effort")
            r = urllib.request.Request(BASE + "/chat/completions",
                data=json.dumps(body_d).encode(),
                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                d = json.loads(resp.read().decode())
        else:
            raise
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    return msg.get("content") or ""


PROBE_CACHE = os.path.join(OUT_DIR, "semantic_probe.json")

# 轻量质量基准（2026-11-20 加，防「快 ≠ 准」）：3 条真错译 + 3 条带陷阱的正确译文。
# 陷阱项专测误报（SI 换算 / 时间习语 / 术语正译）——实测 minimax 曾在此类上良译误报 4/8。
QUALITY_PROBE = [
    {"en": "Starling Bank raised $13 billion in funding.",
     "zh": "星灵银行筹集了10亿美元资金。", "bad": True},
    {"en": "The company is doubling down on its paid pilots program.",
     "zh": "该公司正在向飞行员支付报酬的项目上加倍投入。", "bad": True},
    {"en": "Token costs dropped after the API price cut.",
     "zh": "代币成本在API降价后下降了。", "bad": True},
    {"en": "The company raised $1.4 trillion in committed capital.",
     "zh": "该公司筹集了1.4万亿美元承诺资本。", "bad": False},
    {"en": "The startup is three months away from shipping.",
     "zh": "这家初创公司距离出货还有三个月。", "bad": False},
    {"en": "Chiplet packaging improves yield at TSMC.",
     "zh": "芯粒封装提升了台积电的良率。", "bad": False},
]


def _parse_flags(raw):
    """从模型输出里抽 JSON 数组（容忍 ```json 包裹与杂质）。"""
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`").lstrip("json").strip()
    for cand in (txt,):
        try:
            v = json.loads(cand)
            return v if isinstance(v, list) else [v]
        except Exception:
            pass
    m = re.search(r'\[[\s\S]*\]', txt)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, list) else [v]
        except Exception:
            pass
    out = []
    for mo in re.finditer(r'\{[^{}]*"idx"[^{}]*\}', txt):
        try:
            out.append(json.loads(mo.group(0)))
        except Exception:
            pass
    if not out:
        # 2026-11-23 补：deepseek 系模型不遵守 JSON 指令，实际输出是「编号 + 复述原文 + 括号说明」块，
        # 如「1. EN: ... ZH: ... （原文误译为"10亿"，应为130亿美元。）」「3. ZH 正确。」
        # 按编号块切分：块内出现错误类关键词才标 flagged；明确写「正确」的块跳过。
        parts = re.split(r'(?m)^\s*(\d+)[.、)]\s*', txt)
        for i in range(1, len(parts) - 1, 2):
            try:
                idx = int(parts[i])
            except ValueError:
                continue
            body = parts[i + 1] if i + 1 < len(parts) else ""
            flat = re.sub(r'\s+', ' ', body).strip()
            if re.search(r'incorrect|wrong|误|应为|应改|应该有|错误|不符|偏差|不准确|不一致|矛盾|相反|不匹配|漏译|问题[：:]|建议[：:]|不宜|不是', flat, re.I):  # 2026-11-23: 「原译误」「**问题:**」都要命中
                if not re.match(r'ZH\s*正确', flat, re.I) and not re.fullmatch(r'ZH\s*正确[。.]?', flat, re.I):
                    if not re.search(r'无硬伤|无错误|无问题|仅作核对|基本正确|无需修改|不需修改|未发现', flat, re.I):
                        out.append({"idx": idx, "type": "语义", "why": flat[:180]})
        # 2026-11-23 二补：reasoning=low 档下 deepseek 常改输出 markdown 表格
        # 「| 5 | 语义 | 问题描述 | 建议译文 |」；问题列含错误/建议类关键词才 flag。
        for mo in re.finditer(r'(?m)^\|\s*(\d+)\s*\|(.+)$', txt):
            try:
                idx = int(mo.group(1))
            except ValueError:
                continue
            cells = [c.strip() for c in mo.group(2).split('|') if c.strip()]
            body = " ".join(cells)
            if re.search(r'incorrect|wrong|误|应为|错误|不符|偏差|不准确|生硬|改为|建议|不一致|矛盾|相反|不匹配|漏译', body, re.I):
                out.append({"idx": idx, "type": "语义", "why": body[:180]})
    if not out and re.search(r'incorrect|wrong|误|应为|错误|不符|偏差|不准确', txt, re.I) \
            and not re.search(r'无硬伤|无错误|无问题|仅作核对|基本正确|无需修改|未发现', txt, re.I):
        # 2026-11-23 三补：无编号整段/单行（常见于单条批，如「ZH translation is wrong: …」）。
        # 多条的批若完全没给编号，会统一落到第 1 条——属最后兜底，人工审仍可定位。
        out.append({"idx": 1, "type": "语义", "why": re.sub(r'\s+', ' ', txt)[:180]})
    return out


def _grade(model, timeout=150):
    """跑质量基准：一次请求同时给「延迟 + 验真成绩」。失败返回 None。
    hit=错译命中数（满 3），fp=良译误报数（越少越好），ok=fp≤1 且 hit≥2。"""
    import time
    lines = ["%d. EN: %s\n   ZH: %s" % (i, p["en"], p["zh"])
             for i, p in enumerate(QUALITY_PROBE, 1)]
    t0 = time.time()
    try:
        out = _chat_once(model, [{"role": "system", "content": PROMPT_HEAD},
                                 {"role": "user", "content": "\n".join(lines)}],
                         timeout=timeout, max_tokens=4000)
    except Exception as e:
        print("[semantic] 质量基准 %s 失败：%s" % (model.split("/")[-1], type(e).__name__))
        return None
    secs = round(time.time() - t0, 1)
    if not (out or "").strip():
        print("[semantic] 质量基准 %s 返回空" % model.split("/")[-1])
        return None
    flagged = {f.get("idx") for f in _parse_flags(out) if isinstance(f.get("idx"), int)}
    hit = sum(1 for i, p in enumerate(QUALITY_PROBE, 1) if p["bad"] and i in flagged)
    fp = sum(1 for i, p in enumerate(QUALITY_PROBE, 1) if not p["bad"] and i in flagged)
    return {"secs": secs, "hit": hit, "fp": fp, "ok": hit >= 2 and fp <= 1}


def probe(timeout=150):
    """启动时选模型（2026-11-20 用户定「加质量门槛」：延迟 + 质量双闸）。
    每个模型跑一次 6 对质量基准——同一请求既给延迟也给验真成绩，
    合格（fp≤1 且 hit≥2）的按延迟升序排前，不合格的排后（防「快 ≠ 准」把高误报模型顶到首位）。
    结果缓存 24h。SEMANTIC_PROBE_FAST=1 只测延迟（省时间，不做质量闸）。"""
    global MODEL
    import time
    try:
        c = json.load(open(PROBE_CACHE, encoding="utf-8"))
        if time.time() - c.get("ts", 0) < 86400 and c.get("model") in MODELS:
            MODEL = c["model"]
            g = (c.get("grades") or {}).get(MODEL) or {}
            print("[semantic] 用缓存探测结果 %s（%.1fs，命中 %s 误报 %s，%s 前有效）" % (
                MODEL.split("/")[-1], g.get("secs", c.get("secs", -1)),
                g.get("hit", "?"), g.get("fp", "?"),
                time.strftime("%m-%d %H:%M", time.localtime(c["ts"] + 86400))))
            return
    except Exception:
        pass
    fast = bool(os.environ.get("SEMANTIC_PROBE_FAST"))
    grades = {}
    for m in MODELS:
        if fast:
            t0 = time.time()
            try:
                if _chat_once(m, [{"role": "user", "content": "回复OK"}],
                              timeout=timeout, max_tokens=2000).strip():
                    grades[m] = {"secs": round(time.time() - t0, 1),
                                 "hit": None, "fp": None, "ok": True}
            except Exception as e:
                print("[semantic] 探测 %s 失败：%s" % (m.split("/")[-1], type(e).__name__))
            continue
        g = _grade(m, timeout=min(timeout, 60))   # 2026-11-23：慢模型 60s 未出结果即淘汰
        if g:
            grades[m] = g
    if not grades:
        print("[semantic] 探测全失败，保持默认 %s" % MODEL)
        return
    good = sorted([m for m, g in grades.items() if g.get("ok")],
                  key=lambda m: grades[m]["secs"])
    bad = sorted([m for m, g in grades.items() if not g.get("ok")],
                 key=lambda m: (-(grades[m].get("hit") or 0), grades[m]["secs"]))
    order = good + bad
    MODEL = order[0]
    print("[semantic] 模型探测：%s" % " > ".join(
        "%s(%.1fs%s)" % (
            m.split("/")[-1], grades[m]["secs"],
            "" if fast else " hit%s/fp%s%s" % (
                grades[m].get("hit"), grades[m].get("fp"),
                "" if grades[m].get("ok") else "✗"))
        for m in order))
    if not good:
        print("[semantic] 警告：无模型通过质量闸（hit≥2 且 fp≤1），按最快可用 %s 兜底"
              % MODEL.split("/")[-1])
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        json.dump({"model": MODEL, "secs": grades[MODEL]["secs"], "ts": time.time(),
                   "grades": grades, "order": order},
                  open(PROBE_CACHE, "w", encoding="utf-8"))
    except Exception:
        pass


def chat(pairs, timeout=240):
    lines = []
    for i, p in enumerate(pairs, 1):
        lines.append("%d. EN: %s\n   ZH: %s" % (i, p["en"], p["zh"]))
    messages = [{"role": "system", "content": PROMPT_HEAD.replace("{TODAY}", TODAY)},
                {"role": "user", "content": "\n".join(lines)}]
    order = [MODEL] + [m for m in MODELS if m != MODEL]
    last = None
    for m in order:                       # 首选失败自动切下一个模型
        for attempt in (1, 2):
            try:
                out = _chat_once(m, messages, timeout=timeout)
                if out.strip():
                    return out
                last = "空响应"
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, str(e)[:100])
            import time as _t
            _t.sleep(3)
        print("[semantic] %s 失败（%s），切换下一模型" % (m.split("/")[-1], last))
    raise RuntimeError("全部模型失败：%s" % last)


def run(pairs, label, batch=15):   # 2026-11-23：统一 15（daily 旧默认 8 会让雷达批数翻倍）
    """分批送检：每批 ≤batch 对（整篇 176 主楼层时防 token 爆），合并 flags（idx 已还原全局序号）。"""
    import math
    forced = os.environ.get("SEMANTIC_MODEL")
    if forced:
        global MODEL
        if forced in MODELS:
            MODEL = forced
            print("[semantic] 强制指定模型 %s（SEMANTIC_MODEL）" % forced.split("/")[-1])
        else:
            print("[semantic] SEMANTIC_MODEL=%s 不在 MODELS 里，忽略" % forced)
    elif not os.environ.get("SEMANTIC_NO_PROBE"):
        probe()
    all_flags = []
    nb = math.ceil(len(pairs) / batch) if pairs else 0
    for b in range(nb):
        chunk = pairs[b * batch:(b + 1) * batch]
        try:
            raw = chat(chunk)
        except Exception as e:
            print("[semantic] 批 %d/%d 全模型失败（%s），跳过" % (b + 1, nb, e))
            continue
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.strip("`").lstrip("json").strip()
        flags = None
        try:
            flags = json.loads(txt)
        except Exception:
            # salvage：LLM 输出 JSON 带杂质时，先抓整段数组，再逐个 {"idx":…} 对象提取
            m = re.search(r'\[[\s\S]*\]', txt)
            if m:
                try:
                    flags = json.loads(m.group(0))
                except Exception:
                    flags = None
            if flags is None:
                flags = []
                for mo in re.finditer(r'\{[^{}]*"idx"[^{}]*\}', txt):
                    try:
                        o = json.loads(mo.group(0))
                        if isinstance(o, dict):
                            flags.append(o)
                    except Exception:
                        pass
                if not flags:
                    flags = _parse_flags(raw)   # 2026-11-23：编号块/表格兜底
                if not flags:
                    # deepseek 系常逐条复述并判「ok」——有编号/表格结构即视为「该批无问题」，
                    # 不再误报「返回非 JSON」（2026-11-23 语义修正）。
                    if re.search(r'(?m)^\s*\d+[.、)]', raw) or re.search(r'(?m)^\|\s*\d+\s*\|', raw):
                        flags = []
                    else:
                        print("[semantic] 批 %d/%d 返回非 JSON：%s" % (b + 1, nb, raw[:120]))
                        continue
        if not isinstance(flags, list):
            flags = [flags]
        for f in flags:
            if isinstance(f.get("idx"), int):
                f["idx"] = f["idx"] + b * batch  # 还原全局序号
            all_flags.append(f)
        print("[semantic] 批 %d/%d：%d 对，flagged %d" % (b + 1, nb, len(chunk), len(flags)))
    # ── 去重与矛盾消解（2026-11-21 改进 2）──────────────────────────────
    # 实测问题：同一个 idx 被报两次（模型先说「无误」又说「漏译」），清单噪音大且自相矛盾。
    # 规则：同一 idx 只保留一条——优先保留判定明确的（含「应改为/应为/漏译」这类可执行结论），
    # 丢弃只描述现象或自称「无误」的条目；同分时取 why 更长的（信息更足）。
    CLEAR = re.compile(r"应改|应为|应译|漏译|错译|译错|错误|篡改|矛盾")
    SELF_OK = re.compile(r"无误|正确|不算错|不构成|可接受|尚可")
    by_idx = {}
    for f in all_flags:
        i = f.get("idx")
        if not isinstance(i, int):
            continue
        why = str(f.get("why") or "")
        score = (2 if CLEAR.search(why) else 0) - (3 if SELF_OK.search(why) and not CLEAR.search(why) else 0)
        cur = by_idx.get(i)
        if cur is None or score > cur[0] or (score == cur[0] and len(why) > len(cur[1].get("why") or "")):
            by_idx[i] = (score, f)
    # 优化2（2026-11-23）：知识过时型降级——理由是「我的记忆与现实不符」而非「原文与译文不符」
    STALE_RX = re.compile(r"不存在|未发布|未发生|未证明|尚未证明|并未确认|并非现任|现任为|实际上不是|现实中|据我所知|截至.{0,6}未|属于虚构|疑似虚构|可能虚构|误写|幻觉|预测性表述|实际年份|听起来不像真")
    _kept, _stale = [], []
    for v in sorted(by_idx.values(), key=lambda x: str(x[1].get("idx"))):
        f = v[1]
        if STALE_RX.search(str(f.get("why") or "")):
            f["stale"] = True; _stale.append(f)
        else:
            _kept.append(f)
    if _stale:
        print("[semantic] 知识过时型降级 %d 条（不进主清单，需快速扫一眼）" % len(_stale))
        for f in _stale:
            print("   [过时?] #%s %s" % (f.get("idx"), str(f.get("why"))[:90]))
    deduped = _kept
    dropped = len(all_flags) - len(deduped)
    all_flags = sorted(deduped, key=lambda f: f.get("idx"))
    if dropped:
        print("[semantic] 去重合并 %d 条重复/自相矛盾判定" % dropped)
    print("== %s：%d 对（%d 批），flagged %d ==" % (label, len(pairs), nb, len(all_flags)))
    for f in all_flags:
        idx = f.get("idx") - 1 if isinstance(f.get("idx"), int) else None
        src = pairs[idx] if idx is not None and 0 <= idx < len(pairs) else {}
        print("  #%s [%s] %s" % (f.get("idx"), f.get("type"), f.get("why")))
        if src:
            print("      EN:", src["en"][:80])
            print("      ZH:", src["zh"][:80])
    return all_flags


def self_test():
    import datetime
    pairs = json.load(open(OUT_DIR + "/semantic_samples.json"))
    flags = run(pairs, "自测样本")
    if flags is None:
        return 2
    # 验真：10 个已知错译（note 以「错：」开头）必须被抓到；8 个良译不该 flag
    caught = {f.get("idx") for f in flags if isinstance(f.get("idx"), int)}
    miss, false_pos = [], []
    for i, p in enumerate(pairs, 1):
        bad = p["note"].startswith("错：")
        if bad and i not in caught:
            miss.append(i)
        if (not bad) and i in caught:
            false_pos.append(i)
    print("验真：错译 10 对，漏抓 %d %s；良译 8 对，误报 %d %s"
          % (len(miss), miss or "", len(false_pos), false_pos or ""))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    json.dump({"flags": flags, "miss": miss, "false_pos": false_pos},
              open("%s/semantic_%s.json" % (OUT_DIR, stamp), "w"), ensure_ascii=False, indent=1)
    return 0 if not miss and not false_pos else 1


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1].startswith("-h"):
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "--self-test":
        sys.exit(self_test())
    if sys.argv[1] == "--pairs":
        pairs = json.load(open(sys.argv[2]))
        sys.exit(0 if run(pairs, os.path.basename(sys.argv[2])) is not None else 1)
    print(__doc__)
    sys.exit(2)
