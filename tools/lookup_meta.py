#!/usr/bin/env python3
# 无 url 笔记回源（v2）：m.baidu.com（挂 7890）site:ithome.com "标题前25字" → newsid → PC URL → 抓 author/created → 回填
# 用法：python3 tools/lookup_meta.py [N]   N=只处理前 N 篇（试跑）
import sys,re,os,glob,time,urllib.parse,subprocess
sys.path.insert(0,'/root/workspace/tools')
import clip
PROXY="http://127.0.0.1:7890"
BAIDU="https://m.baidu.com/s?word="
import urllib.request,gzip
def baidu_get(url):
    H={"User-Agent":"Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
       "Accept":"text/html,*/*;q=0.8","Accept-Language":"zh-CN,zh;q=0.9",
       "Referer":"https://m.baidu.com/","Connection":"close"}
    op=urllib.request.build_opener(urllib.request.ProxyHandler({"http":PROXY,"https":PROXY}))
    d=op.open(urllib.request.Request(url,headers=H),timeout=25).read()
    if d[:2]==b"\x1f\x8b": d=gzip.decompress(d)
    return d
INBOX="/sdcard/Obsidian/dsh/ReadItLater Inbox"
os.chdir(INBOX)
todo=[]
for f in glob.glob("*.md"):
    t=open(f,encoding="utf-8",errors="replace").read()
    if not t.startswith("---"): continue
    e=t.find("\n---",3); fm=t[:e+4] if e>0 else ""
    if not re.search(r"^created:",fm,re.M): continue
    a=re.search(r"^author:\s*(.*)$",fm,re.M)
    if (not a or not a.group(1).strip()) and not re.search(r"^url:",fm,re.M):
        todo.append(f)
N=int(sys.argv[1]) if len(sys.argv)>1 else 0
todo=todo[:N] if N else todo
print(f"待处理 {len(todo)} 篇")
ok=amb=fail=0
for f in todo:
    title=re.sub(r"\.md$","",f)
    T=re.sub(r"[：:，,。！!？?\s]+"," ",title)[:25]
    q=urllib.parse.quote(f'site:ithome.com {T}')
    nid=None
    for attempt in range(3):
        try:
            p=baidu_get(BAIDU+q)
            if isinstance(p,bytes): p=p.decode("utf-8","replace")
            if "百度安全验证" in p:
                time.sleep(20); continue
            ids=re.findall(r'ithome\.com/html/(\d+)\.htm',p)
            if ids: nid=ids[0]
            break
        except Exception:
            time.sleep(8)
    if not nid:
        print(f"- {f[:40]} 未见 newsid"); fail+=1; time.sleep(5); continue
    s=str(nid); pc=f"https://www.ithome.com/{s[0]}/{s[1:4]}/{s[4:]}.htm"
    try:
        pg=clip.fetch(pc)[0]
        if isinstance(pg,bytes): pg=pg.decode("utf-8","replace")
    except Exception as e:
        print(f"× {f[:40]} PC页失败 {e}"); fail+=1; continue
    tm=re.search(r'<title>(.*?)\s*[-|]\s*IT之家',pg,re.S)
    got=tm.group(1).strip() if tm else "?"
    ta=re.sub(r"\W+","",title); tb=re.sub(r"\W+","",got)
    if min(len(ta),len(tb))>=4 and ta[:8] not in tb and tb[:8] not in ta:
        print(f"~ {f[:40]} 标题不符 → {got[:40]} ({pc})"); amb+=1; continue
    a=re.search(r'<span id="author_baidu">.*?<strong>(.*?)</strong>',pg,re.S)
    d=re.search(r'<span id="pubtime_baidu">(.*?)</span>',pg,re.S)
    author=clip.strip_tags(a.group(1)).strip() if a else ""
    dt=clip.strip_tags(d.group(1)).strip() if d else ""
    st=os.stat(f)
    subprocess.run(["python3","/root/workspace/tools/bak.py",os.path.abspath(f)],capture_output=True)
    txt=open(f,encoding="utf-8").read()
    fm=txt[:txt.find("\n---",3)+4]; rest=txt[len(fm):]
    if not re.search(r"^url:",fm,re.M):
        fm=fm.replace("\n---",f"\nurl: {pc}\n---",1)
    if author and not re.search(r"^author:",fm,re.M):
        fm=fm.replace("\n---",f"\nauthor: {author}\n---",1)
    open(f,"w",encoding="utf-8").write(fm+rest); os.utime(f,(st.st_atime,st.st_mtime))
    print(f"✓ {f[:38]} → {pc} author={author} date={dt}")
    ok+=1; time.sleep(8)
print(f"\n完成 {ok} / 标题不符 {amb} / 失败 {fail}")
