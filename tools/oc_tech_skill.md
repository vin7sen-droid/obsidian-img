---
name: tech-news-daily
description: 每日非 AI 科技新闻日报全流程 SOP——16 源抓取→4 队并行编辑→拼装→风向小结→浅色 HTML→链接核查→落盘 Obsidian。定时任务「每日非 AI 科技新闻抓取归档」或用户说「抓取/出今天的科技日报」时用。
---

# 每日非 AI 科技新闻日报 SOP

范围硬规则：只收非 AI 科技新闻（大模型/生成式 AI/AI Agent 为主体的不收，顺带提一句的可收但不展开）；**汽车与出行一律不收，例外：特斯拉可收（用户 2026-11-16 定）**；判断口径看新闻**主体**。不编造，每条可追溯来源；凑不满 100 条就如实写实际条数。

## 0. 资产清单（全部现成，勿重写）
- 抓取+过滤管线：`/root/workspace/news-tools/daily_fetch.py`（`python3 daily_fetch.py YYYY-MM-DD --out /root/workspace/dN` → raw_<date>.json + pool_<date>.json；16 源 = 15 RSS2.0 + verge atom + techmeme + readhub API）
- HTML 生成：`/root/workspace/news-tools/mkhtml_sticky.py`（读当前目录 out.md → out.html，**必须 cd 到 news-tools 跑**，它用相对路径）
- 链接核查：`/root/workspace/d2/linkcheck.py`（`python3 linkcheck.py out.md [sample]`，并发 12）
- 派单前预处理：`/root/workspace/news-tools/pipeline_split.py`（事件聚类 + 板块预分类 → pack_A-D.json + cluster.json + borderline.json）
- 队员自校验：`/root/workspace/news-tools/check_parts.py`（parts 格式校验，exit 0 才算交付）
- JSON→md 转换：`/root/workspace/news-tools/json2techmd.py`（队员 json/T*.json → parts/A-D.md，版式全脚本生成；借鉴 news-ai JSON 模式 2026-11-12）
- **一键发布：`/root/workspace/news-tools/publish.py`**（拼装+digest+HTML+核查+落盘一条命令，见第 3 节；旧的多脚本分步做法已废弃）
- 落盘目录：`/sdcard/Obsidian/dsh/新闻/`，命名 `科技新闻_YYYY-MM-DD_<N>条.md` + 同名 `.html`
- 可选补充通道（借鉴 news-ai 2026-11-13）：①雷达盲区源——ai_news_radar 的 28 个我们没有的源（Bloomberg/WSJ/NYT/FT/IEEE Spectrum/The Register/ZDNet/少数派/极客公园/智东西/钛媒体/新浪科技等），`--only` 限源不重复抓；②Google News 跳转链解码 `/root/workspace/tools/gnews_url.py`（全局可用，两步法解 `news.google.com/rss/articles/…` 新格式，`--theme`/`--match` 按主题关键词抽样，workers 2~3 稳，单条 5-12s，40 条 workers=6 → 97% 但 100 条只 57%）。十板块融资并购这类薄弱板块可按需补捞；解出的链接同样过时间窗+与池去重再进 raw（via=radar-gnews）；共享缓存 `/root/workspace/digests/<今天>.json` 4 小时内直接复用
- 样例 md **三行式**条目见最近一期日报（/sdcard/Obsidian/dsh/新闻/）：标题行 / 缩进 3 空格摘要行 / 缩进 3 空格源行 `（[源1](url)、[源2](url)） YYYY-MM-DD HH:MM`（源名带链接顿号分隔，时间只取主源、放括号外、带年份；2026-11-12 定版）

## 1. 抓取（daily_fetch.py 一键）
1. `mkdir -p /root/workspace/d<N>/parts && cd /root/workspace/news-tools && python3 daily_fetch.py <date> --out /root/workspace/d<N>`
2. 产出的 pool 已做：时间窗（前一日 18:00 起）、AI/汽车/导购正则、vs 最近两期日报去重（URL+标题前 14 字）、池内标题前 18 字去重。coverage 反向校验（防源静默失效）：daily_fetch.py 落 raw 后自动与上一期 raw 对比——单源骤降 ≥50% 或总数骤降 ≥30% 打 WARN（cnbeta RSS 曾静默失效 150→2，靠此兜住）。导购/科普诱饵正则已并入 JUNK（buying guide/roundup/hands-on/black friday/explained/everything you need to know/worth buying/how to choose 等；`review` 一词不加，防误杀 EU review 类新闻）。
3. **人工扫一遍 pool**：正则拦不住的垃圾（餐饮/教育/纯导购/答题提示类）手动删；readhub 条目按「独立事件」合并视角看，同话题多源留代表。

已知源特性（踩坑记录，改动前先读）：
- solidot 的 `<link><![CDATA[url]]></link>`——正则已容忍 CDATA，勿改回。
- techmeme 的 item.link 是站内锚点，原文在 description 第一个外部 `<A HREF>`，来源名取 title 尾括号。
- readhub：`topic/daily?date=` → 每日 14 话题 → `topic/detail?uid=` → `{"data":{items:[…newsAggList]}}`；同事件多源同质报道极多，单日非 AI 合格通常只 5-6 个事件。
- verge 是 atom（entry/link@href/published）；快科技镜像 anyfeeder 仅 5 条、pubDate 已是北京时间。
- IT之家 pubDate 是 GMT 需 +8（脚本已处理）；Phoronix 对一切 UA 403 属正常，勿改。
- node 子进程输出偶含非法 UTF-8：必须 stdout 落文件再 `decode(errors="replace")`，`text=True` 会在 128KB 边界抛 UnicodeDecodeError（借鉴 news-ai 2026-11-13；调 zh.mjs/翻译通道时适用）。
- 环境无 curl，脚本用 python3 urllib+gzip；**不要用 web_fetch**（本机 provider 歧义会报错）。

## 1.5 派单前预处理（pipeline_split.py，先聚类再派单）
1. `python3 /root/workspace/news-tools/pipeline_split.py <pool>.json` → 各队素材 pack_{A,B,C,D}.json（每条附 board 初判/board_conf/cluster 组号）+ cluster.json（多源合并候选组）+ borderline.json（低置信条目，约占 16%）。
2. **领队只审 borderline**：真边角料（评测/导购/娱乐/慈善/汽车）直接删，能判的从 borderline 挑回 pack（脚本里有组内板块投票兜底）。
3. **派单铁律：同实体整族交同一队员**——派单前必看 `families.md`：同公司/人物跨板块跨队的条目（如「苹果 18 条 → 三、二、六（A/B/C）⚠️」）必须整族给一个队员写，别按板块切给不同队（否则拼装时同事件去重会白写四分之一）。聚类组（cluster.json）同理整组交一人。
4. **聚类只做提示不做决定**：跨语种同事件（英文源+中文源）标题字面几乎无重叠，2-gram 认不出——真正的合并判断仍由队员写作时做（派单模板已含「同事件多源合并」），领队拼装时仍要扫一遍跨队重复。
5. 词表（KW/OVERRIDE/ENTITIES）在脚本顶部，判错的条目优先补词而不是人工搬——补一次长期生效。

- 可选提速（2026-11-16 借鉴 news-ai「机翻打底全上」）：派单前可对英文素材批量预翻——把 pack 里的英文 title+desc 喂 zh.mjs（缓存命中秒回，180s 超时降级英文原文），把「摘要素材（机翻打底，先校再压缩）+原文」两行附进派单条目；中文源（zh 比例 ≥0.25 或 desc<60 字）不送翻、直接 desc 打底。队员以译文打底校数字/人名/语序后压缩到 40-120 字。

## 2. 三队并行编辑（持久队员优先；队员输出 JSON，不写 markdown）
优先用持久队员（spawn_teammate，会话内跨期复用、可 interrupt_agent 中断、可 send_message 续任务/补捞）；无持久队员时退回一次性 subagent。首次创建给完整 SOP 提示词，之后每期派单只发素材路径+输出路径+当日板块分配（规则队员已知）。
每队提示词必须自包含（队员看不到会话）：
- A=一 芯片与硬件 + 二 消费电子与终端；B=三 互联网与大厂 + 四 通信与网络 + 五 软件游戏开源；CD=六 网络安全与数据 + 七 航天能源前沿科学 + 八 生物科技医疗硬件 + 九 政策监管商业 + 十 融资并购（2026-11-12 用户定：C/D 两队任务轻，合并为 tech-cd 一队；原 11-12 的「二↔八」调换随之撤销，「二」回归 A）
- 输入：pipeline_split 的 pack_{A,B,C,D}.json（含 board 初判/cluster 组号），随派单提示词内嵌或让队员读文件
- **输出：`/root/workspace/dN/json/T{1..3}.json`（严格 JSON；T1→A 队、T2→B 队、T3→CD 队）**，schema：
  `{"items":[{"board":"一","title":"中文标题≤45字","url":"主源URL原样","summary":"30–80字含具体数字","sources":[{"name":"IT之家","url":"...","time":"11-12 08:47"}]}],"dropped":[{"title":"...","reason":"..."}]}`
- 队员只做内容判断（挑条/译中/同组多源合并/写摘要），**版式一律不碰**——分节标题/缩进/编号/来源括注全由 json2techmd.py 生成（news-ai A/B 实测：md 版 100% 格式返工 174s vs JSON 0 返工 70s）
- 中文源 desc 打底（2026-11-16 借鉴 AI 日报会话折中方案，省约 1/3 写作时间）：**中文源**（IT之家/cnBeta/快科技/虎嗅等）的条目不必从零写摘要——以 RSS desc 为底稿，**校对数字与事实一致 + 压缩到 40–120 字**即可；**英文源**照旧精编（译中+重写）。质量损失很小，desc 本就是媒体编辑摘要。
- **URL 纪律（2026-11-16 强化，tech-cd2 实测编造 2 条后定）**：`url` 与 `sources[].url` 一律**原样复制自素材池条目**（pack_*.json / pool_*.json），禁止手打、拼接、改 slug、改年份栏目；主 URL 用哪条素材就与该素材完全一致。领队发布前用 linkcheck 兜底，但 404 拦截不能替代队员源头纪律。
- 硬规则：英文译中——**zh.mjs 机翻打底，人工只修四类硬伤（人名/数字/否定/双关）**，短梗/表情/口号不进机翻直接写（按长度分流，见 hot-comments 技能「英文内容的翻译规矩」第 5 条）；专名保留；摘要必须含正文语句，**绝不允许只剩来源括注**；同组多源合并 1 条、用到的源全放 sources；不同组各 1 条；非科技主体（导购/娱乐/慈善/汽车，特斯拉除外可收）放 dropped 并写原因；宁缺毋滥每板块 8–14；不联网不 spawn 只写自己 json
- **翻译缓存与数字机检三条纪律**（借鉴 mm.mjs 抽样纪律 2026-11-15，zh.mjs 同样有共享缓存 `~/.dsh-ai-research/translate-cache.json`）：
  1. **缓存错译不自愈**——重测可疑译文必须显式清缓存（删整个 translate-cache.json 或先备份），直接重跑同一句只会命中同一个错译，得出「稳定错误」的错误结论；
  2. **短句+常见搭配是机翻旧语料重灾区，且本机 Transmart 比借鉴的 MyMemory 更糟**（2026-11-15 全量实测，清缓存后 fresh 请求复现）：`raised $13 billion`→「10亿美元」、换措辞 `13 billion dollars`→「13亿美元」（量级仍错 100 倍）、`40 billion dollars`→「40 10亿美元」崩坏——**含 billion/million/trillion 的数字一律人工按原文重写进译文，不信任机翻数字**；换措辞对照只作定性参考，「换措辞即对」仅对 MyMemory 成立，对 Transmart 不成立；
  3. **数字判定用机检不用人眼**——numcheck.py **v2**（2026-11-15 升级）含**量级换算检测**：`printf '<原文>' > /tmp/n1.txt && printf '<译文>' > /tmp/n2.txt && python3 /root/workspace/tools/numcheck.py /tmp/n1.txt /tmp/n2.txt`（参数是两个文件路径）；输出 scale_risk 字段（billion→预期译文含 N*10 亿或 N*1000 万、million→N 百万或 N*100 万），负例（130 亿美元/500 万美元）实测不误报，正例（13亿/10亿/崩坏串）全抓。
- 队员自检：`python3 -c "import json;d=json.load(open('json/T1.json'));print(len(d['items']))"`
- **编辑队员失联止损**（借鉴 news-ai 2026-11-13）：派单后约 6 分钟未交付且 list_agents 显示 inactive / 收到 failed 通知 → 队长直接接手：读 pack_N.json 素材按同 schema 自己写 json/TN.json（硬规则同队员契约），不干等不重派。
- **队员按需自解码 Google News**（借鉴 news-ai 2026-11-13 改版）：领队走雷达补充通道后跑 `python3 /root/workspace/news-tools/gnews_pool.py <date> --out /root/workspace/dN`——从 digests/<date>.json 筛出未解码的 news.google.com 跳转链，落 `dN/gnews_pool.json`（title/source/theme/time/url）。**派单提示词必须含自解码节**，告知队员按需解：
  ```bash
  python3 /root/workspace/tools/gnews_url.py --json /root/workspace/dN/gnews_pool.json --theme chips --limit 8 --workers 2   # 按主题
  python3 /root/workspace/tools/gnews_url.py --json /root/workspace/dN/gnews_pool.json --match "台积电|TSMC" --limit 5 --workers 2   # 按关键词
  ```
  **全队并发 ≤3**（多队员叠加会撞 Google 限流），解不出的跳过、**不许编 URL**；领队不再统一全量解。
- **队员交付后领队转换**：`python3 /root/workspace/news-tools/json2techmd.py dN/json/T1.json dN/json/T2.json dN/json/T3.json dN/json/T4.json` → parts/A-D.md（自动板块排序/计数/三行式/源行；未知板块与缺摘要条目会打警告），再 `python3 check_parts.py dN/parts/<X>.md` 复核 exit 0。

- 【2026-11-15 更新】量级词保护已固化进 zh.mjs（数字+trillion/billion/million 整组 NUM 占位 + SI 换算还原 + scaleOk 数值回检 + 重翻 2 次 + 原文兜底），正常语序量级句 100% 正确；被动语态顽固行兜底原文人工接手，或改主动语态重翻。占位坏译会固化进缓存——改占位逻辑后必须清 ~/.dsh-ai-research/translate-cache.json。

## 2.7 xcheck 交叉校验（**必跑步骤**；2026-11-16 借鉴 news-ai 前置：领队先跑、清单随复核单发给复核员，复核员只精查 flagged+抽查其余、不再逐条回源；发布前再跑一遍终版兜底。来不及就发布后补跑再就地修）
- **每期必跑**，真问题修完再发；它是复核员的互补——复核员管跨队合并/分类/回源，xcheck 管「中文摘要 vs 英文原文」一致性，独有覆盖 URL 错配与编造细节两类（人工通读最难发现）。
- 共享引擎 /root/workspace/tools/xcheck.mjs（彩云小译第二通道 + Google clients5 备选 + MyMemory 抽样回退，SI 归一数字对比，缓存 ~/.dsh-ai-research/xcheck-cache.json）；科技日报适配版 /root/workspace/news-tools/xcheck_run.mjs。
- 用法：`cd /root/workspace/news-tools && node xcheck_run.mjs <workdir> [YYYY-MM-DD]`（date 缺省取 workdir 最新 raw_*.json；配对用 merged.json 优先、否则合并 json/T*.json）。产物 <workdir>/xcheck_人工审.md，退出码恒 0 不阻塞发布。
- 三层放行（浓缩摘要产物的核心适配）：①中文源跳过（zhRatio≥0.25）；②「疑似漏译（长度比」全放行；③译文数字是原文数字子集放行（含摘要把数字删光的 T 空场景）；④版本号形态（Wine 11.22/Plasma 6.9）两侧都在 → 放行（版本号是 ID 非数量级）；⑤实体关键词丢失（标题只写产品名不写公司名）且译文仍是正常中文 → 放行（2026-11-16 抄 news-ai 适配层）。
- 分工定性（2026-11-16 借鉴 AI 日报会话）：**机检兜底，占位/词表防错，人工终审**——xcheck 只抓「已知的错」（ALIAS_BAD/习语表里没有的坏译名抓不到，如 AP→美联社这类语义错），别把机检当保险；语义错主力仍是送翻前的主题占位词表+人工抽查。
- **养表是每期收尾默认动作**（2026-11-16 用户定）：每期复核/质检发现真错译名或坏习语后，顺手回写 /root/workspace/tools/xcheck.mjs 的 ALIAS_BAD（坏译名→规范名）与 translate.mjs 的 IDIOM_ZH（习语定译），越用越准；改表后清对应缓存（translate-cache / xcheck-cache）。
## 2.8 摘要数字机检（必跑步骤；2026-11-16 借鉴 AI 日报会话「摘要数字机检」提案；单一真源 /root/workspace/tools/summary_check.py，与 numcheck/xcheck 质检家族并列）
- **管的是「摘要 vs 正文」数字一致性**，与 xcheck（译文 vs 英文原文）互补；抓人工最容易漏的「over six million→650 万」约数被合理化成精确数。
- 用法：`python3 /root/workspace/tools/summary_check.py <workdir> [YYYY-MM-DD]`（workdir 如 /root/workspace/d10）；基准=raw_<date>.json 的原始 title+desc（merged title 是队员改写版不作依据）；判定方向=摘要数字 ⊆ 素材数字（SI 归一复用 xcheck.mjs 的 parseNums/parseZhNums，双方纯数比数值、任一带单位比 SI）。
- **只出清单不当阻断闸**（摘要是作者改写，允许补背景数/跨币种换算；flagged 由人 30 秒过一遍放行或修）；产物 <workdir>/summary_人工审.md，exit 恒 0。
- 已知噪音与防线：desc 截断（cnBeta/Tom's 短 desc）会造成「摘要有据但 desc 无」的假 flag——清单已附「源 desc 不在 raw」标注；长度单位（英寸/寸/"）自动归一成毫米比对；型号数字（QN10/KB5132057）与 x86/x64、中文习语（二次开发）自动剥离。
- 三道闸分工：摘要数字机检（摘要 vs 正文，清单提示 30 秒）｜xcheck（译文 vs 英文原文，交付前兜底）｜人工抽查五处（语义/专名，终审）。
- 实测 11-14 期：78 条 → 英文配对 23（中文源 55 跳）→ flagged 10→4（放行规则升级后）。剩余 flagged 判读要点（11-14 期实测，flagged 9 条逼出 3 条真问题）：①先查「摘要内容 vs raw desc 是否同一事件侧面」——真问题三类：URL 与摘要内容错配（KMS 摘要配了打印文章 URL）、编造细节（「无人持有钥匙」原文没有）、无据细节（「物流拦截」raw 无依据）；②误报三类：浓缩删数字（CMF 只写造型删 $39/$20）、多源信息差（摘要含 cnBeta 源细节但 desc 是 bleeping 的）、彩云自身错（Microsoft 365 译成「365 次停机」）。修复走 T*.json 改字段后重跑 publish.py，不手改 md。
## 2.5 复核员（三 JSON 齐 → 拼装前，借鉴 news-ai ai-news-review 模式 2026-11-12）
- 触发（2026-11-16 借鉴 news-ai「复核流水化」改）：**来一批审一批**——某队 JSON 交付即先送复核员，不等三队齐（三队交付与复核重叠，省墙钟）；复核员对已到的 JSON 先跑跨队合并，后续队到达再增量复核。领队无空时可亲自按同规则复核。
- 四职责（复核员不写条目、不改四队原始 JSON、不写 Obsidian，落盘由领队负责）：
  1. **跨队同事件合并**——A 队某条主链出现在 B 队某条 sources 里且标题 2-gram 重合 ≥0.5 → 合并成一条（sources 并集）。
  2. **分类边界复核**——固定规则：半导体涨价/架构/产能归「一 芯片与硬件」，只有股价/评级/财报预期归「九 政策商业」或「十 融资并购」；技术方案与开源项目归「五 软件游戏开源」；立法执法归「九 政策监管」。
  3. **摘要数字回源核对**——摘要中的金额/百分比/数量逐条对照 sources；找不到依据就删掉或标注。**精确数必须回源**（2026-11-16 借鉴）：英文原稿常是 "over/about/nearly six million" 这类约数，被队员合理化成「650 万」精确数——发现精确数而对应用文原文是约数时改回约数表述。
  4. **非科技主体剔除**——评测/导购/娱乐/慈善/AI 主体补漏丢弃。
- 交付：`dN/review/merged.json`（合并后终版，schema 同队员 JSON）+ `dN/review/report.md`（四节：合并/改判/摘要/丢弃，每条可追溯）。
- 领队对 report.md 四节逐条过目后再进转换。
> **增量补量同样必须过本流程**（2026-11-12 晚踩坑：下午已发 73 条版直接 merge_inc 拼接没走复核，结果 126 条里查出 7 处增量/基线同事件重复——Meta 卸任两条、N-central 零日、eMudhra、T-Mobile、微软更新、R 星 GTA6、欧盟 DFA，复核后删到 119 条）。
> **复核员唤醒失败止损**：send_message 唤醒 inactive 复核员可能连续两次"failed before it finished"且不落盘——不要干等第三次，领队用脚本自跑四职责（URL + 标题 2-gram + 实体关键词三级撞车扫描，见 d6b 内联用法）。

## 2.6 同日增量补量（下午已发版 → 晚间补料 → 改名替换）
用户当天第二次说「抓取」时走本节，**不要整期重做**：
1. `python3 daily_fetch.py <date> --out /root/workspace/d<N>b` 重抓（RSS 滑动窗口，raw 会比上午多几十条，pool 大概率翻倍）。
2. **不要用差集逻辑**：两次 daily_fetch 的 pool 不互为超集（实测：上午 pool 与已发 md 重合 89 条、晚间 pool 重合 0 条），只能用「已发 md 的 URL + 标题前 14 字」逐条比对筛增量。
3. 领队**预挑**：按标题关键词 KEYS 字典从 pool 挑出候选（miss 会打印，便于发现新事件），砍掉导购/评测/黑五/优惠码/汽车/AI 主体再派单——预挑 + 队员复核双层过滤（实测：209 条预挑后队员又砍 73 条）。
4. 派 4 个持久队员写 json/T{1..4}.json（**也必过复核员**，见 2.5；增量与下午基线撞车是复核重点：GTA6/微软补丁/马士基/无印良品/TrendForce 这类基线已有条目最容易被二次收）。
5. 合并用 `/root/workspace/d6b/merge_inc.py <base md> <digest> T1.json T2.json …`：板块内并入 + URL 去重 + 全文重编号 + 重算板块计数 + digest 重写。
6. 复核后领队改稿参考 `/root/workspace/d6b/fix_review.py`（删增量条 / 摘要扩写 / 源行并集 / 重编号），注意：删除标题条目时**先 find 再删**，同名标题可能撞上想保留的那条（Meta 卸任条目 ithome 版与 TechCrunch 版同事件不同标题，合并要保留信息全的那版）。
7. 落盘后按「同一天条数变化直接改名替换旧文件」规则删掉上午版本（不留旧条数副本）。

## 3. 一键发布（publish.py —— 落盘优先，只有这一条命令）
- 发布前先跑 2.7 xcheck 交叉校验与 2.8 摘要数字机检 `python3 /root/workspace/tools/summary_check.py <workdir>`（均必跑），flagged 里真问题修完再发；来不及就发布后补跑、按第 5 节就地修。
复核产物（`dN/merged.json`）一出来**立刻跑**，不要等所有核查完美：
```bash
cd /root/workspace/news-tools
python3 publish.py /root/workspace/d9 --date 2026-11-14            # 完整：拼装→HTML→核查→落盘
python3 publish.py /root/workspace/d9 --date 2026-11-14 --dry      # 只生成 final/out.*，不落盘
python3 publish.py /root/workspace/d9 --date 2026-11-14 --no-linkcheck
# 增量模式：同日旧版 md 存在时自动只查新增 URL（delta=0 秒过）；首次发布自动全量查
```
输入二选一：`<workdir>/merged.json`（复核产物，优先）或 `<workdir>/json/T*.json`（无复核时按 URL 去重合并）；`<workdir>/digest.txt` 可选。
输出：`<workdir>/final/out.md` + `out.html`；落盘 `/sdcard/Obsidian/dsh/新闻/科技新闻_<date>_<N>条.{md,html}`，同日旧条数版本先移入 `/root/workspace/bak/news/` 再替换（不留旧条数副本、不改创建日期）。
脚本内置校验，任一失败即非零退出并说明原因：条目数=HTML `<li>` 数、空摘要 0、暗色残留 0、404/410 死链（Phoronix/Bloomberg 403 属正常）、缺板块只告警不拦。

> **为什么必须一条命令**（2026-11-14 复盘 11-13 期）：复核 71 条源数据 20:36 就绪，但拼装五步被拆进多轮对话，20:36→03:56 空转 7 小时没落盘，用户催了三次；这五步真跑只用 5 分钟。**规则：复核一出 merged.json 立即 `publish.py` 出 v1 落盘（哪怕条目还要调），后续就地改（`open(path,"w")` 同路径 / 改名用 `mv`，改前 `cp -p` 备份）。禁止「先完美后落盘」。**

## 4. 风向小结（digest.txt）
写 `<workdir>/digest.txt`，publish.py 会整块插到时间行之后：
```
> **当日风向小结**
>
> - **微软生态连遭冲击**：Microsoft 365 全球宕机逾 10 小时，故障报告一度接近 **14 万条**……
> - **安全事件高密度**：FBI 扣押币安被盗 **12 亿美元**……
```
- 方法：统计跨站引用（同事件出现在 2+ 家源）与源站频次，挑 5 条左右最热线索，每条一句话。
- **重点词加粗**：每条 1–3 处 `**重点**`（mkhtml_sticky.py 自动转紫色 `<b>`，勿在脚本里重复 escape）。
- 写完先 `head -12 <workdir>/final/out.md` 确认小结段在文件里（历史坑：digest 放错目录时 assemble 零警告、md 与 HTML 双双缺小结）。

## 5. 已发日报的修正（就地改，不重建）
- `open(path,"w")` 写回同路径（inode 不变、只有 mtime 变）；条数变化用 `mv` 改名（/sdcard FUSE 上 mv 是 rename，mtime/inode 保留）。
- **绝不写新文件+删旧文件**——重建会改创建日期，影响 Obsidian 排序；改前 `cp -p` 备份到 /root/workspace/bak/news/。
- 若已误重建，可用 `touch -r <备份>` 把 mtime 改回原时间恢复排序。
- 改完重跑 `python3 publish.py <workdir> --date <date> --dry` 复核校验，再手工覆盖落盘文件。
