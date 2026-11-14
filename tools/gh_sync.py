#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具集 GitHub 同步：本机 <-> vin7sen-droid/obsidian-img:tools/

用法：
  python3 gh_sync.py push    # 本地领先 → 上传
  python3 gh_sync.py pull    # 远端领先 → 下载（先备份 + py_compile 校验 + 原子替换）
  python3 gh_sync.py sync    # push 后 pull（clip.py 启动时异步调用这个）
  --quiet                    # 静默（clip.py 后台触发用）

原理：manifest.json 记录每个文件的 sha256。push=本地 sha 与 manifest 不符才 PUT；
pull=远端 sha 与本地不符才下载。两台机器经 GitHub 中转收敛一致，单用户场景 last-write-wins。

边界（不要越线）：
  本同步器只管「内部工具」，不管下载器。*_dl.py（weibo_dl / douyin_dl / social_dl / ins_dl）
  走各自的版本检查（tools/<name>.json + 脚本内 check_update），发版必须人工抬 VERSION。
  原因：本同步器是 last-write-wins 且 pull 会自动覆盖本机文件，而下载器要求
  「只有版本更新才上传」——两者规则相反，混在一起会互相踩。
  FILES 里一旦出现 *_dl.py，脚本直接拒绝运行（见下方护栏）。
"""
import base64, fcntl, hashlib, json, os, shutil, subprocess, sys, time
import urllib.error, urllib.request

REPO = "vin7sen-droid/obsidian-img"
MASTER_MARK = "/root/workspace/.sync-master"   # 本机权威源标志：存在→auto=push，不存在→auto=pull
BRANCH = "main"
DIR = "tools"
LOCAL = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = "/root/.config/ghclip/token"
BAK_SYNC = "/root/workspace/bak/sync"
FILES = ["clip.py", "clip_server.py", "gh_img.py", "title_fix.py", "bak.py",
         "refresh_all_comments.py", "gh_sync.py", "update_check_template.md"]
# 边界护栏：下载器一律不进本白名单（详见文件头 docstring）
_BAD = [f for f in FILES if f.endswith("_dl.py")]
if _BAD:
    sys.exit("gh_sync 拒绝运行：FILES 里混入了下载器 %s\n"
             "下载器请走版本检查（tools/<name>.json），不要纳入自动同步。"
             % ", ".join(_BAD))

QUIET = "--quiet" in sys.argv


def log(m):
    if not QUIET:
        print(m, flush=True)


def token():
    try:
        return open(TOKEN_FILE).read().strip()
    except Exception:
        return ""


def gh(path, method="GET", data=None):
    """GitHub Contents API。path 如 /repos/o/r/contents/tools/clip.py → (code, dict)"""
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        headers={"Authorization": "Bearer " + token(),
                 "Accept": "application/vnd.github+json", "User-Agent": "gh-sync"})
    body = json.dumps(data).encode() if data is not None else None
    try:
        r = urllib.request.urlopen(req, data=body, timeout=25)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        log("  网络：%s" % e)
        return 0, {}


def _put_file(name, content_bytes, msg, old_sha=None):
    payload = {"message": msg, "content": base64.b64encode(content_bytes).decode(),
               "branch": BRANCH}
    if old_sha:
        payload["sha"] = old_sha
    code, r = gh("/repos/%s/contents/%s/%s" % (REPO, DIR, name), "PUT", payload)
    return code in (200, 201), r


def _get_blob(name):
    """→ (sha256_hex, bytes) 或 (None, None)；经 contents API base64 取内容"""
    code, r = gh("/repos/%s/contents/%s/%s" % (REPO, DIR, name))
    if code != 200 or not isinstance(r, dict) or "content" not in r:
        return None, None
    raw = base64.b64decode(r["content"])
    return hashlib.sha256(raw).hexdigest(), raw


def _local_manifest():
    m = {}
    for f in FILES:
        p = os.path.join(LOCAL, f)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                b = fh.read()
            m[f] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
    return m


def _get_manifest():
    """→ (remote_manifest_dict, manifest_blob_sha)；无则 ({}, None)"""
    code, r = gh("/repos/%s/contents/%s/manifest.json" % (REPO, DIR))
    if code != 200 or not isinstance(r, dict) or "content" not in r:
        return {}, None
    try:
        return json.loads(base64.b64decode(r["content"]).decode()), r.get("sha")
    except Exception:
        return {}, r.get("sha")


def push():
    local = _local_manifest()
    remote, mani_sha = _get_manifest()
    files = remote.get("files", {})
    changed = [f for f, m in local.items() if files.get(f, {}).get("sha256") != m["sha256"]]
    if not changed:
        log("push：本地与远端一致（%d 个文件）" % len(local))
        return 0
    ok = 0
    for f in changed:
        with open(os.path.join(LOCAL, f), "rb") as fh:
            blob = fh.read()
        _, cur = gh("/repos/%s/contents/%s/%s" % (REPO, DIR, f))
        old = cur.get("sha") if code_ok(cur) else None
        done, _ = _put_file(f, blob, "sync tools: %s" % f, old)
        log("push %s %s" % ("✓" if done else "✗", f))
        ok += 1 if done else 0
    new_mani = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), "files": local}
    done, _ = _put_file("manifest.json",
                        json.dumps(new_mani, ensure_ascii=False, indent=1).encode(),
                        "sync tools: manifest", mani_sha)
    log("push：上传 %d/%d 个文件，manifest %s" % (ok, len(changed), "✓" if done else "✗"))
    return 0 if ok == len(changed) and done else 1


def code_ok(r):
    return isinstance(r, dict) and bool(r.get("sha"))


def pull():
    remote, _ = _get_manifest()
    files = remote.get("files", {})
    if not files:
        log("pull：远端无 manifest，跳过")
        return 0
    todo = []
    for f, meta in files.items():
        p = os.path.join(LOCAL, f)
        try:
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    if hashlib.sha256(fh.read()).hexdigest() == meta.get("sha256"):
                        continue
        except Exception:
            pass
        todo.append(f)
    if not todo:
        log("pull：本地已是最新（%d 个文件）" % len(files))
        return 0
    ts = time.strftime("%Y%m%d-%H%M%S")
    bakdir = os.path.join(BAK_SYNC, ts)
    ok = 0
    for f in todo:
        sha, blob = _get_blob(f)
        if not blob or sha != files[f].get("sha256"):
            log("pull ✗ %s（下载失败或校验不符）" % f)
            continue
        if f.endswith(".py"):
            tmp = os.path.join(LOCAL, f + ".sync-tmp")
            with open(tmp, "wb") as fh:
                fh.write(blob)
            try:
                subprocess.run([sys.executable, "-c",
                                "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)",
                                tmp], check=True, capture_output=True, timeout=30)
            except Exception as e:
                log("pull ✗ %s（py_compile 未过，放弃）%s" % (f, e))
                os.unlink(tmp)
                continue
            os.unlink(tmp)
        p = os.path.join(LOCAL, f)
        if os.path.exists(p):
            os.makedirs(bakdir, exist_ok=True)
            shutil.copy2(p, os.path.join(bakdir, f))
        tmp = os.path.join(LOCAL, f + ".sync-tmp")
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, p)
        log("pull ✓ %s（%d 字节）" % (f, len(blob)))
        ok += 1
    log("pull：更新 %d/%d 个文件%s" % (ok, len(todo), "，旧版在 %s" % bakdir if ok else ""))
    return 0


def main():
    lock = os.path.expanduser("~/.cache/ghsync.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    fh = open(lock, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)   # 拿不到=已有实例在跑，退出
    except Exception:
        log("sync：另一实例运行中，跳过")
        return 0
    try:
        cmd = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "auto"
        if cmd == "auto":
            # 本机（权威源，有 .sync-master 标志）只推；跟随端只拉——同顺序双向会互相覆盖
            cmd = "push" if os.path.exists(MASTER_MARK) else "pull"
            log("auto 角色：%s" % cmd)
        if cmd in ("pull", "sync") and os.path.exists(MASTER_MARK):
            # 护栏：权威源 pull 会用远端旧版覆盖本机未推的改动（08:26 踩过一轮）。
            # 灾难恢复确需拉取时：mv 标志文件移开 → 手工 pull → mv 回来。
            sys.exit("拒绝 pull：本机是权威源（%s 存在），只推不拉；"
                     "确需强制拉取请先移走标志文件。" % MASTER_MARK)
        if cmd == "push":
            return push()
        if cmd == "pull":
            return pull()
        if cmd == "sync":            # 手工双向：先拉后推
            rc = pull()
            return rc if rc else push()
        log("未知命令：%s（可用 push/pull/sync/auto）" % cmd)
        return 2
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)


if __name__ == "__main__":
    sys.exit(main())
