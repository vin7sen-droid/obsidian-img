#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
firecrawl_cache.py —— Firecrawl 当日去重缓存
避免 AI 日报/科技日报/雷达 三条线同天抓同一 URL 浪费 credits

设计：
- 进程内内存缓存（_mem_cache）：同一进程多次调用瞬间命中
- 磁盘日级缓存（scraped_YYYY-MM-DD.json）：跨进程/跨会话共享
- key = md5(url)[:16]，碰撞概率极低，存集合即可
- 无锁写盘，偶尔丢几条无伤大雅
"""
import json
import os
import time
import hashlib
from pathlib import Path

CACHE_DIR = Path("/workspace/tools/.firecrawl_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DAILY_FILE = CACHE_DIR / f"scraped_{time.strftime('%Y-%m-%d')}.json"

# 进程内热缓存
_mem_cache = set()

# 启动时加载磁盘缓存
if DAILY_FILE.exists():
    try:
        _mem_cache.update(json.load(open(DAILY_FILE)))
    except Exception:
        pass


def _key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def is_scraped_today(url: str) -> bool:
    """今天是否已抓过（内存优先，无则回磁盘一次）"""
    k = _key(url)
    if k in _mem_cache:
        return True
    # 磁盘可能被别的进程更新过，重新加载一次
    if DAILY_FILE.exists():
        try:
            disk = set(json.load(open(DAILY_FILE)))
            if k in disk:
                _mem_cache.add(k)
                return True
        except Exception:
            pass
    return False


def mark_scraped_today(url: str):
    """标记今日已抓（内存+异步落盘）"""
    k = _key(url)
    if k in _mem_cache:
        return
    _mem_cache.add(k)
    # 无锁写盘，失败不报错
    try:
        json.dump(list(_mem_cache), open(DAILY_FILE, "w"))
    except Exception:
        pass


def get_cache_stats():
    return {
        "mem": len(_mem_cache),
        "disk_file": str(DAILY_FILE),
        "disk_exists": DAILY_FILE.exists(),
        "disk_size": DAILY_FILE.stat().st_size if DAILY_FILE.exists() else 0,
    }


def clear_today_cache():
    """调试用：清空今日缓存"""
    global _mem_cache
    _mem_cache.clear()
    if DAILY_FILE.exists():
        try:
            DAILY_FILE.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(get_cache_stats(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "clear":
        clear_today_cache()
        print("cleared")
    else:
        print("Usage: python3 firecrawl_cache.py [stats|clear]")