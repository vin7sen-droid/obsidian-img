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
MODELS = ["ywd/z-ai/glm-5.3-flash", "ms/deepseek-ai/DeepSeek-V4.1-Flash-1017"]


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
         "删来源媒体和类别凑数词；短名优先；能复用已有标签就别造新词。")
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
    for model in MODELS:
        try:
            tags = _call(model, key, base, prompt)
            if verbose:
                print("  [tag_llm] %s → %s" % (model, "/".join(tags) or "(空)"))
            if tags:
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
