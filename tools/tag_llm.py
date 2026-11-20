#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 LLM 给新主题出标签（词典 tag_suggest 为空时的回落，2026-11-17）。

主模型 GLM-5.3-flash，后备 DeepSeek-V4.1-Flash（用户 2026-11-17 定）。
凭据：/root/.config/tagllm/{key,base}（600，不同步，缺任一则静默退出）。

用法:
  python3 tools/tag_llm.py "标题" [--summary "摘要"] [--brief] [--no-cache]
  库里调用：from tag_llm import suggest; suggest(title, summary) -> ["标签1","标签2"]
"""
import json, os, re, sys, hashlib, urllib.request, urllib.error

KEY_F = "/root/.config/tagllm/key"
BASE_F = "/root/.config/tagllm/base"
CACHE = "/root/workspace/bak/tagllm_cache.json"
DICT = "/root/workspace/tools/tagdict.json"
MODELS = ["ywd/z-ai/glm-5.3-flash",        # 主：12 条样本 58%/60%，0 空响应
          "ywd/poolside/laguna-xs-2.1",       # 备：50%/51% 但 1.5s/篇、0 空响应（比 DeepSeek 稳）
          "ywd/nvidia/nemotron-3-ultra-550b-a55b"]  # 新增备（probe 实测 1.1s 稳定，语义判定偏松）

PROBE_CACHE = "/root/workspace/bak/tagllm_probe.json"
STATS = "/root/workspace/bak/tagllm_stats.json"

# 泛类词黑名单：谁都套得上的标签 = 无信息（2026-11-17 定「不要给泛类词」）
JUNK_TAGS = {"科技", "人工智能", "AI", "芯片", "智能手机", "显卡驱动", "手机",
             "互联网", "数码", "新闻", "行业", "科技新闻", "硬件", "软件", "产品"}


def _record(model, title, tags):
    """自评一次真实出标签的结果并累计到模型质量统计（零额外请求）。
    通过 = 非空 + 至少一个标签是标题子串（贴题）+ 无泛类词。"""
    try:
        ok = bool(tags) and any(t.lower() in title.lower() for t in tags) \
             and not (set(tags) & JUNK_TAGS)
        import time
        d = {}
        if os.path.exists(STATS):
            try: d = json.load(open(STATS, encoding="utf-8"))
            except Exception: d = {}
        s = d.setdefault(model, {"n": 0, "ok": 0})
        s["n"] += 1
        if ok:
            s["ok"] += 1
        d["_ts"] = time.time()
        tmp = STATS + ".%d" % os.getpid()
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, STATS)
    except Exception:
        pass


def _stats():
    try:
        d = json.load(open(STATS, encoding="utf-8"))
        return {k: v for k, v in d.items() if not k.startswith("_")}
    except Exception:
        return {}


def _rank(order):
    """在延迟顺序之上叠加质量闸：跑够 3 次且通过率 <50% 的模型降到最后。
    未积累统计的模型保持延迟序（先验视为可用），不影响冷启动。"""
    st = _stats()
    good, bad = [], []
    for m in order:
        s = st.get(m) or {}
        n, ok = s.get("n", 0), s.get("ok", 0)
        (bad if (n >= 3 and ok / n < 0.5) else good).append(m)
    if bad:
        print("  [tag_llm] 质量门降级：%s" % ", ".join(
            "%s(%d/%d)" % (m.split("/")[-1], st[m]["ok"], st[m]["n"]) for m in bad))
    return good + bad


def _probe(timeout=20):
    """启动探测各模型延迟（用户 2026-11-20 定：快者排前，对齐 semantic_check）。
    结果缓存 6h（tag 场景调用频繁，全探一轮约 5-8s，不能每篇都探）。"""
    import time
    try:
        c = json.load(open(PROBE_CACHE, encoding="utf-8"))
        if time.time() - c.get("ts", 0) < 21600:
            order = [m for m in c.get("order", []) if m in MODELS]
            if order:
                return order + [m for m in MODELS if m not in order]
    except Exception:
        pass
    key, base = _cred()
    if not key:
        return list(MODELS)
    scored = []
    for m in MODELS:
        t0 = time.time()
        try:
            _call(m, key, base, "回复OK", timeout=timeout)
            scored.append((time.time() - t0, m))
        except Exception:
            pass
    if scored:
        scored.sort()
        order = [m for _, m in scored] + [x for x in MODELS if x not in [y for _, y in scored]]
        print("  [tag_llm] 模型探测：%s" % ", ".join("%s(%.1fs)" % (mm.split("/")[-1], tt) for tt, mm in scored))
        try:
            os.makedirs(os.path.dirname(PROBE_CACHE), exist_ok=True)
            json.dump({"order": order, "ts": time.time()}, open(PROBE_CACHE, "w"))
        except Exception:
            pass
        return order
    return list(MODELS)


def _cred():
    try:
        k = open(KEY_F).read().strip()
        b = open(BASE_F).read().strip().rstrip("/")
        return (k, b) if k and b else (None, None)
    except Exception:
        return None, None


def _top_tags(n=40):
    try:
        d = json.load(open(DICT, encoding="utf-8"))
        items = sorted(d["counts"].items(), key=lambda x: -x[1])
        return [k for k, _ in items[:n]]
    except Exception:
        return []


def _cache_get(h):
    try:
        return json.load(open(CACHE, encoding="utf-8")).get(h)
    except Exception:
        return None


def _cache_put(h, tags):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        d = {}
        if os.path.exists(CACHE):
            try: d = json.load(open(CACHE, encoding="utf-8"))
            except Exception: d = {}
        d[h] = tags
        tmp = CACHE + ".%d" % os.getpid()
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, CACHE)
    except Exception:
        pass


def _prompt(title, summary):
    top = _top_tags()
    p = ("给这条中文科技新闻标题定 1-2 个标签。规则：看主骨定数量；公司+产品可双标签；"
         "删来源媒体和类别凑数词；短名优先；能复用已有标签就别造新词；"
         "公司名用通行全称（字节跳动而非字节、英伟达而非 NVIDIA）；"
         "不要给泛类词（显卡驱动、智能手机、芯片）这类谁都套得上的标签。")
    if top:
        p += "已有标签参考：" + "、".join(top)
    p += '\n只输出 JSON：{"tags":["标签1","标签2"]}\n\n标题：' + title
    if summary:
        p += "\n摘要：" + summary[:280]
    return p


def _call(model, key, base, prompt, timeout=60):
    body = json.dumps({"model": model, "temperature": 0.1, "max_tokens": 2000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    r = urllib.request.Request(base + "/chat/completions", data=body,
                               headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(r, timeout=timeout))
    c = (d["choices"][0]["message"].get("content") or "")
    m = re.search(r"\{.*\}", c, re.S)
    if not m:
        return []
    try:
        tags = [x.lstrip("#").strip() for x in json.loads(m.group(0)).get("tags", [])]
    except Exception:
        return []
    return [t for t in tags if t and not re.search(r"[.+\[\]{}]", t)][:3]


def suggest(title, summary="", use_cache=True, verbose=False):
    """返回 1-3 个标签；无凭据/全失败返回 []（绝不抛异常）。"""
    key, base = _cred()
    if not key:
        return []
    prompt = _prompt(title, summary)
    h = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16]
    if use_cache:
        c = _cache_get(h)
        if c is not None:
            return c
    for model in _rank(_probe()):
        for attempt in range(2):          # 空响应/异常都重试一次（中转限流会吐空 content）
            try:
                tags = _call(model, key, base, prompt)
                if verbose:
                    print("  [tag_llm] %s → %s" % (model, "/".join(tags) or "(空)"))
                if tags:
                    _record(model, title, tags)
                    _cache_put(h, tags)
                    return tags
            except Exception as e:
                if verbose:
                    print("  [tag_llm] %s ERR %s" % (model, type(e).__name__))
    return []


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("title")
    ap.add_argument("--summary", default="")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    t = suggest(a.title, a.summary, use_cache=not a.no_cache, verbose=a.verbose)
    if a.brief:
        print(" / ".join(t) if t else "(LLM 未给出)")
    else:
        print("标签：" + ("、".join(t) if t else "(未给出)"))
