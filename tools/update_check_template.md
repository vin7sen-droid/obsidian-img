# 版本检查提示模板（借鉴方案，2026-11-14 存档）

> 来源：用户提供的通用方案（含踩坑记录）。适用于「脚本会被复制到别处、可能拿不到最新改动」的场景。
> **本会话现状：clip.py / clip_server.py / gh_img.py 等全部只在本机跑，暂无接入对象。**
> 触发条件（满足任一再动手）：①脚本要复制到第二台设备跑；②要分发给他人；③同一脚本在多处有副本且各自演化。

## 硬要求清单

1. 顶部加 `VERSION = 'x.y.z'`，支持 `--version`。
2. 加 `UPDATE_CHECK_URL`，指向仓库里的 `{"version","note","url"}` json。
3. **24 小时内最多查一次**，缓存 `~/.cache/<name>/update.json` 记 `checked_at`。
4. `timeout=3`；断网 / 404 / JSON 坏**一律静默**，不影响主功能。
5. 只有「远端 > 本地」才打印（版本 + note + 取用地址），相同或更低什么都不打。
6. 加 `--no-update-check` 可跳过。
7. **只提示，不下载、不替换。**

## 实现要点（已踩的坑）

- 版本比较必须用**元组比较**（`(2,10,0) > (2,9,0)`），别用字符串——`"2.10" < "2.9"` 是经典错误。
- `checked_at` **只在请求成功后写**，否则一次网络抖动锁死 24 小时。
- 检查只放 `main()` 开头一次，别放进下载循环。
- 提示保持 1–2 行，加分隔线会污染正常输出。
- json 里的 `url` 要指向真实存在的脚本本体，否则提示里给的是死链。

## 代码骨架

```python
VERSION = '2.0.0'
UPDATE_CHECK_URL = ('https://gh-proxy.com/https://raw.githubusercontent.com/'
                    'vin7sen-droid/obsidian-img/main/tools/<name>.json')
CACHE_DIR = os.path.expanduser('~/.cache/<name>')
CHECK_INTERVAL = 86400

def _ver_tuple(s):
    return tuple(int(p) if p.isdigit() else 0
                 for p in re.split(r'[.\-+]', str(s)))

def check_update():
    cache_path = os.path.join(CACHE_DIR, 'update.json')
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if time.time() - cache.get('checked_at', 0) < CHECK_INTERVAL:
        return
    try:
        with _open(UPDATE_CHECK_URL, timeout=3) as r:
            info = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        return
    cache['checked_at'] = time.time()      # 只在成功后写
    latest = str(info.get('version') or '')
    if latest and _ver_tuple(latest) > _ver_tuple(VERSION):
        print('! 有新版本 v%s（当前 v%s）— %s'
              % (latest, VERSION, info.get('note') or ''))
        if info.get('url'):
            print('  取新版: %s' % info['url'])
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f)
```

`main()` 里：

```python
ap.add_argument('--no-update-check', action='store_true')
ap.add_argument('--version', action='version', version='<name> ' + VERSION)
args = ap.parse_args()
if not args.no_update_check:
    check_update()
```

## 验证清单（六项全过才算完成）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 远端版本更高 | 打印版本号 + note + 地址 |
| 2 | 24 小时内再跑 | 不请求、不打印 |
| 3 | 同版本 | 完全静默 |
| 4 | 404 / 断网 | 静默、不报错、主流程正常 |
| 5 | `--no-update-check` | 不请求、不建缓存 |
| 6 | 正常跑主功能 | 输出与改前一致 |

## 发新版三步

改脚本 `VERSION` → 上传脚本本体到 `tools/<name>.py`（先 GET 取 sha 再 PUT）→ 更新 `tools/<name>.json` 的 `version`/`note`。

## 两个边界提醒（原方案自带）

- 只在「脚本被复制到别处、拿不到最新改动」时有意义，脚本只在本机就没意义。
- 故意不做自动替换——真要自动更新，得加备份 → 临时文件 → `py_compile` 校验 → `os.replace`，失败回滚，风险高得多。
