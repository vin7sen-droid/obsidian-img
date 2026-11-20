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
MODELS = ["ywd/nvidia/nemotron-3-ultra-550b-a55b",   # probe 实测最快（1.0-1.2s），判定可用
          "ywd/minimaxai/minimax-m3.1-flash",       # 2026-11-20 渠道下线（500 get_channel_failed），保留观察
          "ywd/z-ai/glm-5.3-flash",                 # 判定质量标杆（10/10 验真 0 误报），慢/偶超时
          "ywd/stepfun-ai/step-5-preview"]          # 备（偶发截断 JSON）
MODEL = MODELS[0]
OUT_DIR = "/root/workspace/digests"
PROMPT_HEAD = """你是中英翻译质检员。对每对「英文原句/中文译文」判断译文是否有以下问题：
1) 语义偏差（译反、译错、漏译关键信息）
2) 数字/量级错误（billion→亿、单位、货币）
3) 术语错（AI 领域：agent 保留英文、token 保留英文、yield=良率、foundry=晶圆代工厂、grounding=落地/锚定、Elo=等级分；人名用通行中文译名；personal intelligence=个人智能（产品名，非「个人情报」））
4) 严重不通顺（读不懂/病句/直译腔到影响理解）
5) 方向性习语：英文 "be X (months/weeks/days/years) away" = 距离发生只剩 X（不是「还需要/要等 X」）
6) EN 端可能只有标题（正文/描述缺失）。此时中文摘要含标题之外的细节是正常的（从多源补充），不算「添加信息」「过度翻译」；只报：主体/公司错误（如 Alphabet 译成谷歌）、夸大（Not Reachable→大规模）、技术栈或机制臆断（原文未提却断言某语言/框架）、报告数与风险混淆；不报「补充了细节」本身。
只报「确有问题的行」，没问题的跳过。输出 JSON 数组：[{"idx": 行号, "type": "语义|数字|术语|文笔", "why": "一句话"}]。没有问题输出 []。只输出 JSON，不要解释。"""


def _chat_once(model, messages, timeout=240, max_tokens=None):
    # 推理模型思考档（用户 2026-11-20 实测定：默认 none —— 18 对样本三轮 none/low 判定完全相同（9/9 命中、1/9 误报），耗时 none 中位 17.0s vs low 22.5s；长文本 79 对 none 137.6s vs low 268.4s）
    # 想复核漏检时 SEMANTIC_REASONING=low；网关不认 none 会 400 → 自动去参数重试（回落模型默认档）
    if max_tokens is None:
        max_tokens = int(os.environ.get("SEMANTIC_MAX_TOKENS", "16000"))
    body_d = {"model": model, "messages": messages,
              "max_tokens": max_tokens, "temperature": 0.1,
              "reasoning_effort": os.environ.get("SEMANTIC_REASONING", "none")}
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


def probe(timeout=90):
    """启动时 ping 各模型测延迟（用户 2026-11-20 定：探测哪个快用哪个），快者排前。
    结果缓存 24h（2026-11-20 实测 probe 全跑 6 分钟，GLM 超时占大头）。"""
    global MODEL
    import time
    try:
        c = json.load(open(PROBE_CACHE, encoding="utf-8"))
        if time.time() - c.get("ts", 0) < 86400 and c.get("model") in MODELS:
            MODEL = c["model"]
            print("[semantic] 用缓存探测结果 %s（%.1fs/对，%s 前有效）" % (
                MODEL.split("/")[-1], c.get("secs", -1),
                time.strftime("%m-%d %H:%M", time.localtime(c["ts"] + 86400))))
            return
    except Exception:
        pass
    scored = []
    for m in MODELS:
        t0 = time.time()
        try:
            out = _chat_once(m, [{"role": "user", "content": "回复OK"}],
                             timeout=timeout, max_tokens=2000)
            if out.strip():
                scored.append((time.time() - t0, m))
        except Exception as e:
            print("[semantic] 探测 %s 失败：%s" % (m, type(e).__name__))
    if scored:
        scored.sort()
        MODEL = scored[0][1]
        print("[semantic] 模型探测：%s" % ", ".join("%s(%.1fs)" % (m.split("/")[-1], t) for t, m in scored))
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            json.dump({"model": MODEL, "secs": scored[0][0], "ts": time.time()},
                      open(PROBE_CACHE, "w", encoding="utf-8"))
        except Exception:
            pass
    else:
        print("[semantic] 探测全失败，保持默认 %s" % MODEL)


def chat(pairs, timeout=240):
    lines = []
    for i, p in enumerate(pairs, 1):
        lines.append("%d. EN: %s\n   ZH: %s" % (i, p["en"], p["zh"]))
    messages = [{"role": "system", "content": PROMPT_HEAD},
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


def run(pairs, label, batch=15):
    """分批送检：每批 ≤batch 对（整篇 176 主楼层时防 token 爆），合并 flags（idx 已还原全局序号）。"""
    import math
    if not os.environ.get("SEMANTIC_NO_PROBE"):
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
                    print("[semantic] 批 %d/%d 返回非 JSON：%s" % (b + 1, nb, raw[:120]))
                    continue
        if not isinstance(flags, list):
            flags = [flags]
        for f in flags:
            if isinstance(f.get("idx"), int):
                f["idx"] = f["idx"] + b * batch  # 还原全局序号
            all_flags.append(f)
        print("[semantic] 批 %d/%d：%d 对，flagged %d" % (b + 1, nb, len(chunk), len(flags)))
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
