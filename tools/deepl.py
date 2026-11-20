#!/usr/bin/env python3
"""DeepL Free API 客户端（2026-11-20 接入，四个落点：兜底行补译/难句/glossary 术语锁定/人名音译参考）
用法:
  python3 tools/deepl.py "文本"               # 翻译（en→zh，自动读 key，preserve_formatting）
  python3 tools/deepl.py "文本" --no-glossary # 不带术语表
  python3 tools/deepl.py -f <file>            # 整文件翻译（按行，输出到 stdout）
  python3 tools/deepl.py --usage              # 查额度 character_count/limit
  python3 tools/deepl.py --glossary-init      # 建术语表（幂等：存在即复用）
  python3 tools/deepl.py --glossary-list      # 列出 glossary
key: /root/.config/deepl/api_key (600) 或 env DEEPL_API_KEY。
注意: Free 档域名是 api-free.deepl.com（不是 api.deepl.com）。
"""
import json, os, sys, urllib.request, urllib.error

KEY = os.environ.get("DEEPL_API_KEY") or open("/root/.config/deepl/api_key").read().strip()
BASE = "https://api-free.deepl.com/v2"
GLOSSARY_NAME = "clip-terms"

# 术语锁定表（TSV: source\ttarget）。目标=原词即「锁定不译」（用户 2026-11-13 裁定 token/agent 保留英文）。
ENTRIES = (
    "agent\tagent\n"
    "SDR\tSDR\n"
    "ARR\tARR\n"
    "GTM\tGTM\n"
    "token\ttoken\n"
    "issue tracker\t问题跟踪器\n"
    "Linear\tLinear\n"
    "presales\tpresales\n"
    "outbound\t外呼拓客\n"
    "paid pilots\t付费试点\n"
    "paid pilot\t付费试点\n"
)


def req(path, method="GET", body=None):
    r = urllib.request.Request(
        BASE + path, method=method,
        headers={"Authorization": "DeepL-Auth-Key " + KEY, "Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(r, data=data, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.stderr.write("DeepL %s %s: %s\n" % (e.code, e.reason, e.read().decode()[:200]))
        sys.exit(3 if e.code == 429 else 2)


def find_glossary():
    d = req("/glossaries")
    for g in (d.get("glossaries") if isinstance(d, dict) else d) or []:
        if g.get("name") == GLOSSARY_NAME:
            return g["glossary_id"]
    return None


def glossary_init():
    gid = find_glossary()
    if gid:
        print("glossary 已存在:", gid)
        return gid
    g = req("/glossaries", "POST", {"name": GLOSSARY_NAME, "source_lang": "en",
                                    "target_lang": "zh", "entries": ENTRIES,
                                    "entries_format": "tsv"})
    print("glossary 已创建:", g["glossary_id"], "条目", g["entry_count"])
    return g["glossary_id"]


def translate(texts, glossary=None):
    body = {"text": texts, "target_lang": "ZH", "source_lang": "en",
            "preserve_formatting": True}
    if glossary:
        body["glossary_id"] = glossary
    d = req("/translate", "POST", body)
    return [t["text"] for t in d["translations"]]


def main():
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-") and argv[0] not in ("-f",):
        if not argv or argv[0] not in ("--usage", "--glossary-init", "--glossary-list", "-f"):
            print(__doc__)
            sys.exit(0 if argv else 2)
    if argv[0] == "--usage":
        u = req("/usage")
        print("已用 %s / %s 字符（剩 %s）" % (u["character_count"], u["character_limit"],
                                            u["character_limit"] - u["character_count"]))
    elif argv[0] == "--glossary-init":
        glossary_init()
    elif argv[0] == "--glossary-list":
        d = req("/glossaries")
        for g in (d.get("glossaries") if isinstance(d, dict) else d) or []:
            print(g["glossary_id"], g["name"], g["entry_count"])
    elif argv[0] == "-f":
        lines = open(argv[1], encoding="utf-8").read().splitlines()
        gid = find_glossary()
        out = []
        for i in range(0, len(lines), 25):  # DeepL 批量上限 50 条/请求，取 25 保险
            batch = lines[i:i+25]
            out.extend(translate(batch, gid))
        print("\n".join(out))
    else:
        gid = find_glossary()
        print("\n".join(translate([argv[0]], gid)))


if __name__ == "__main__":
    main()
