// 英文内容中文化：腾讯 Transmart 批量翻译 + 本地磁盘缓存（2026-11-13）
//
// 目标：抓来的英文标题 / 摘要 / 社区帖标题，落盘前翻成中文，让 Obsidian 里能直接读。
// 原则：
//   1) **不改原字段**：译文写 it.title_zh / it.summary_zh（buzz 条目写 b.title_zh），
//      title/summary 保留英文原文，渲染层用「_zh 优先、回落原文」，可追溯、可复算。
//   2) **失败绝不阻塞落盘**：网络错、限流、超预算 → 跳过该条保持英文，saveDigest 照常写。
//   3) **缓存优先**：同一标题/摘要只翻一次（~/.dsh-ai-research/translate-cache.json），
//      重复跑同一批源几乎零请求；每批落一次缓存，中途中断也不白干。
//   4) 开关：DSH_AI_RESEARCH_TRANSLATE=off|0|false|no 可整体关掉；预算 DSH_AI_RESEARCH_TRANSLATE_BUDGET_MS（默认 180000）。
//
// 通道说明（2026-11-13 实测）：Google gtx 429（本机 IP 限流）、Bing ttranslatev3 401、
// DeepLX 公共实例返回广告；腾讯 Transmart 稳定可用，50 条标题一批约 4.3s。
//
// 2026-11-16 优化（备份 bak/translate.mjs.050900.opt.bak）：
//   ① 抽出 runBatches()：translateTexts 与 translateRes 原本各抄一份批处理循环，
//      导致 falls 归一、占位符泄漏重翻、sameText 统计只在 translateRes 有 → 统一后两条通道行为一致
//      （实测旧版 translateTexts 把 "falls" 译成「福尔斯」，因为它没有 falls→declines 归一）
//   ② translateTexts 现在也有占位符泄漏单条重翻（原只有 translateRes 有）
//   ③ protect() 加 URL/图片语法整体占位（腾讯会把 https:// 全角化、把 ! 转成全角 ！）
//   ④ SCALE_RE 货币符与数字间空格并进货币组（"Rs  18 crore" 双空格也命中）；换算统一 toFixed(4)
//      压浮点尾差（旧版 1.005 billion → 「10.049999999999999亿」，且实测整句被引擎译成「1000万美元」）
//   ⑤ 源码从 431 行降到 435 行但消除约 70 行重复（净增来自新增的 URL 保护与注释）
// 关键坑：文本必须放 source.text_list，放顶层 text_list 或 source.text 会报
//   "Invalid value of source.text_list"；返回数组与入参同序。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { NAME_MAP, NAME_RE, swapNames } from "./name-guard.mjs";

const ENDPOINT = "https://transmart.qq.com/api/imt";
const CACHE_DIR = process.env.DSH_AI_RESEARCH_CACHE || path.join(os.homedir(), ".dsh-ai-research");
const CACHE_FILE = path.join(CACHE_DIR, "translate-cache.json");
const HAS_CJK = /[\u4e00-\u9fff]/;
const S = (x) => (x == null ? "" : String(x));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const BATCH_MAX_ITEMS = 40;     // 单次请求最多几条（实测 50 也 OK，留余量）
const BATCH_MAX_CHARS = 6000;   // 单次请求总字符上限（摘要偏长，按字符切更稳）
const TEXT_MAX_CHARS = 1200;    // 单条文本上限，超出截断（标题不会触发，摘要最长约 400）

// ── 专名保护 ──────────────────────────────────────────────────────────────
// 通用机翻会把专名当普通词意译：Anthropic→「拟人化」、Gemini→「双子座」、
// Gemini 4 Hydrogen→「双子座4号氢气」。翻前把专名换成 zqx<N>zqx 占位符
// （实测腾讯会原样保留该 token），翻完再还原，专名就不会被译坏。
const PROTECT_WORDS = [
  // 公司 / 机构
  "OpenAI", "Anthropic", "Google", "DeepMind", "Meta", "Microsoft", "Apple", "Amazon", "AWS",
  "NVIDIA", "AMD", "Intel", "TSMC", "Samsung", "Qualcomm", "Broadcom", "ARM", "xAI", "Mistral", "Cohere",
  "Perplexity", "Midjourney", "Runway", "Databricks", "Snowflake", "Palantir", "Salesforce", "Oracle", "IBM",
  "Baidu", "Alibaba", "Tencent", "ByteDance", "Huawei", "Xiaomi", "Moonshot", "Zhipu", "MiniMax", "StepFun",
  "DeepSeek", "SenseTime", "Cambricon", "GitHub", "Hugging Face", "Reddit", "Hacker News", "Stack Overflow",
  "arXiv", "NeurIPS", "ICML", "ICLR", "CVPR", "ACL", "Stability AI", "Cerebras", "Groq", "SambaNova",
  "Uber", "Netflix", "Spotify", "Stripe", "Airbnb", "Tesla", "SpaceX", "TikTok", "Wikipedia", "Figma",
  // 媒体 / 论坛（HN 系保留英文）
  "Show HN", "Ask HN", "HN",
  // 技术缩写
  "LLM", "AGI", "API", "SDK", "GPU", "TPU", "MoE", "RAG", "LoRA", "GGUF", "TTS", "ASR", "OCR",
  // 交易所 / 市场代码 / 机构缩写（2026-11-15 语义抽查实测：NasdaqGM 被译成「纳斯达克通用汽车公司」、
  // Anthropic PBC 的 PBC 被展开成「中国人民银行」——这类两三个字母的金融缩写引擎一律猜错）
  "NasdaqGM", "NasdaqGS", "NasdaqCM", "NYSE", "AMEX", "OTC", "PBC", "LLC", "Inc", "Ltd", "Corp",
  // 模型 / 产品（2026-11-15 实测：Titan 删除——腾讯裸翻小写 titan→「巨头」、专名上下文自己保留，
  // 放进保护表反而把 "AI titan"（普通名词）也钉成英文）
  "GPT", "ChatGPT", "DALL-E", "Sora", "Claude", "Gemini", "Gemma", "Llama", "Qwen", "Kimi", "GLM",
  "Ernie", "Doubao", "Grok", "Copilot", "Phi", "Nemotron", "Granite", "Whisper",
  "Codex", "Veo", "Imagen", "NotebookLM", "Jules", "Cursor", "Windsurf", "Replit", "Vercel", "Ollama",
  "vLLM", "LangChain", "LlamaIndex", "PyTorch", "TensorFlow", "JAX", "CUDA", "MCP", "Model Context Protocol",
  "Stable Diffusion", "ElevenLabs", "Suno", "Synthesia", "Scale AI",
  // 芯片 / 硬件
  "H100", "H200", "B200", "GB200", "Blackwell", "Hopper", "Trainium", "Inferentia", "RISC-V",
  // 语义抽查发现的专名错字（Microduck 被译成 Microbuck）
  "Microduck",
  // 地名（引擎会把 Falls 音译成「福尔斯」）
  "Niagara Falls", "Falls Church",
  // AI 专用术语：保留英文不译（用户 2026-11-20 定「ai 新闻专用词不翻译」，agents 为例）
  // 只收两类：①agent 家族黑话（直译成「代理/智能体」不自然或非译不可）②业内通说不译的缩写。
  // 其余 AI 术语（prompt/token/embedding/fine-tuning/multimodal/context window…）按收件箱
  // 181 篇 IT之家文章的实际译法走中文定译，见 IDIOM_ZH 的「AI 术语（IT之家口径）」段。
  "agents", "agentic", "agent", "multi-agent", "swarm", "agentic workflow",
  "tokens", "token", "tokenizer", "tokenizers", "tokenization",
  "Transformer", "transformers", "RLHF", "RLVR", "DPO", "GRPO", "SOTA",
  // 2026-11-20 语义初筛实测补（60 条术语类 flagged 里重复出现的专名/术语）：
  // Neon（Gemini 4 Neon 被译「氖/氖灯」4 次）、Muse（Meta Muse 被译「缪斯/博物馆式」3 次）、
  // Claude Code（被译「Claude代码」）、harness（被译「线束」2 次）、Kioxia、Run:ai（被拆成「运行：ai」）
  "Neon", "Muse", "Claude Code", "harness", "Kioxia", "Run:ai",
  "Strands", "Strands Agents", "Bedrock AgentCore", "AgentCore",
  // 2026-11-20 补：机构名（实测「Foresight's AI Salon」被引擎读成「Footer的」，属视觉近形误读）
  // 注：Signal/ANT 这类「普通英文词形」的产品名不进表——PROTECT_RE 是大小写不敏感的，
  // 会把 signal（信号）/ant（蚂蚁）这些普通词一起钉成英文，代价大于收益。
  "Foresight", "Janus Henderson",
].filter(Boolean);
export { PROTECT_WORDS };
// 长词优先，避免 "Meta" 抢在 "Metadata" 前半段之类；用字母数字边界判定
const PROTECT_RE = new RegExp(
  "(" + [...new Set(PROTECT_WORDS)].sort((a, b) => b.length - a.length).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")" +
  "(?![A-Za-z0-9])", "gi");
const PREFIX_RE = new RegExp(
  "\\b(GPT|Gemini|Claude|Llama|Qwen|Grok|Gemma|DeepSeek|Phi|Mistral|Kimi|GLM|Ernie|Doubao)[- ]?\\d+(?:\\.\\d+)?" +
  "(?:[ -](?:Opus|Sonnet|Haiku|Pro|Ultra|Flash|Mini|Nano|Max|Turbo|Preview|Hydrogen|thinking))?", "gi");

// 单位中文化（2026-11-15 用户要求）：腾讯常把 gigawatts/megawatts 这类单位原样留在中文句子里
// （彩云会译成「吉瓦/兆瓦」）。只在**译文含中文**时替换，英文原文整段不动；
// 缩写成 GW/MW/kW 的保留原样（中文媒体也这么写），只处理完整单词。
const UNIT_ZH = {
  "terawatt-hours": "太瓦时", "terawatt-hour": "太瓦时", "terawatts": "太瓦", "terawatt": "太瓦",
  "gigawatt-hours": "吉瓦时", "gigawatt-hour": "吉瓦时", "gigawatts": "吉瓦", "gigawatt": "吉瓦",
  "megawatt-hours": "兆瓦时", "megawatt-hour": "兆瓦时", "megawatts": "兆瓦", "megawatt": "兆瓦",
  "kilowatt-hours": "千瓦时", "kilowatt-hour": "千瓦时", "kilowatts": "千瓦", "kilowatt": "千瓦",
  "watt-hours": "瓦时", "watt-hour": "瓦时", "watts": "瓦", "watt": "瓦",
};
// 前后用「非字母」边界而不是 \b：腾讯常输出「5gigawatts」这种数字直接粘单位的写法，
// \b 在 5 与 g 之间不成立（两侧都是 \w），会把整句漏掉。
const UNIT_RE = /(?<![A-Za-z])(terawatt-hours?|terawatts?|gigawatt-hours?|gigawatts?|megawatt-hours?|megawatts?|kilowatt-hours?|kilowatts?|watt-hours?|watts?)(?![A-Za-z])/gi;
// 腾讯偶尔把 megawatts 译成「兆瓦tts」（吞词留下的尾巴），顺手清掉
const UNIT_TAIL_RE = /([太吉兆千万]?瓦)(?:tts|ts|es|s)(?![A-Za-z])/gi;
function unitZh(s) {
  if (!/[\u4e00-\u9fff]/.test(s)) return s; // 没中文＝原文整段，不动
  return s
    .replace(UNIT_RE, (m) => UNIT_ZH[m.toLowerCase()] || m)
    .replace(UNIT_TAIL_RE, "$1")
    .replace(/(\d)\s+(?=[太吉兆千万]?瓦)/g, "$1")   // 「1.5 吉瓦」→「1.5吉瓦」
    .replace(/(\d)个(?=[太吉兆千万]?瓦)/g, "$1");   // 引擎给占位符补的量词：「114个兆瓦」→「114兆瓦」
}
// 习语/惯用语定译表（2026-11-15 加）：腾讯对财经/科技习语逐词直译（实测 "Blessing in disguise"→
// 「伪装的祝福」应为因祸得福、"picks and shovels"→「挑选和铲」应为卖铲人/基础设施供应商、
// "no jerks culture"→「没有混蛋的文化」），整短语占位、还原定译中文，与量级词同机制。
const IDIOM_ZH = [
  // ── AI 术语（IT之家口径，2026-11-20）────────────────────────────────
  // 依据：收件箱 181 篇 IT之家文章的实际译法（智能体 11 篇、提示词 5、微调 4、多模态 4、
  // 上下文窗口 3、幻觉 2、检索增强生成 1；英文原词 agents/prompt/embedding/fine-tuning 各 0 篇）。
  // 口径：agent 家族保留英文（PROTECT_WORDS），其余 AI 术语走下列中文定译。
  // 长短语必须排在短短语前（IDIOM_RE 按数组顺序命中）。
  // ── 2026-11-20 语义初筛补的术语定译（都是 11-20 期实测真错，重复出现或高频的才进表）──
  [/open[-\s]weight\s+models?/i, "开放权重模型"],
  [/chief\s+legal\s+officer/i, "首席法务官"],
  [/pre[-\s]seed\s+(?:funding|round)/i, "种子前轮融资"],
  [/compute\s+crunch/i, "算力紧张"],
  [/kill\s+switch/i, "急停开关"],
  [/AI\s+washing/i, "AI 包装"],
  [/left\s+on\s+the\s+table/i, "白白错失"],
  [/lift(?:s|ed|ing)?\s+the\s+boat/i, "带动整个板块"],
  [/attys\b/i, "律师"],
  [/handoverism/i, "AI 交接主义"],
  [/GPU\s+fractioning/i, "GPU 切分"],
  [/\bcolo\b/i, "托管数据中心"],
  [/retrieval[-\s]augmented\s+generation/i, "检索增强生成"],
  [/large\s+language\s+models?/i, "大语言模型"],
  [/reinforcement\s+learning\s+from\s+human\s+feedback/i, "基于人类反馈的强化学习"],
  [/reasoning\s+models?/i, "推理模型"],
  [/vision\s+models?/i, "视觉模型"],
  [/foundation\s+models?/i, "基座模型"],
  [/base\s+models?/i, "基础模型"],
  [/vector\s+databases?/i, "向量数据库"],
  [/context\s+windows?/i, "上下文窗口"],
  [/system\s+prompts?/i, "系统提示词"],
  [/prompt\s+injection/i, "提示词注入"],
  [/prompt\s+engineering/i, "提示词工程"],
  [/tool[-\s]?calling|tool\s+use/i, "工具调用"],
  [/open[-\s]weight(?:ed)?/i, "开放权重"],
  [/open[-\s]source/i, "开源"],
  [/embeddings?/i, "嵌入"],
  [/prompts?/i, "提示词"],
  [/fine[-\s]?tun(?:e|ed|es|ing)/i, "微调"],
  [/multimodal/i, "多模态"],
  [/inference/i, "推理"],
  [/diffusion/i, "扩散"],
  [/checkpoints?/i, "检查点"],
  [/jailbreak(?:s|ing|ed)?/i, "越狱"],
  [/latency/i, "延迟"],
  [/throughput/i, "吞吐量"],
  [/attention\s+mechanisms?/i, "注意力机制"],
  [/hallucinations?/i, "幻觉"],
  // ── 通用习语 ─────────────────────────────────────────────────────────
  [/(?:a\s+)?blessing\s+in\s+disguise/i, "因祸得福"],
  [/picks?\s+and\s+shovels/i, "卖铲人（基础设施供应商）"],
  [/(?:doubl)(?:e|es|ed|ing)\s+down(?:\s+on)?/i, "加码"],
  [/game\s+changer/i, "游戏规则改变者"],
  [/silver\s+lining/i, "一线希望"],
  // 「moonshot」= 登月式豪赌（大手笔押注）；但公司名 Moonshot AI 必须保留英文
  // （2026-11-20 实测：无差别规则把「Moonshot AI Cancels $150M…」译成「登月式豪赌人工智能取消…」）
  [/(?<![\w-])moonshot(?!\s*(?:AI|ai\b|\s*人工智能))/i, "登月式豪赌"],
  [/sea\s+change/i, "根本性转变"],
  [/paradigm\s+shift/i, "范式转变"],
  [/elephant\s+in\s+the\s+room/i, "众人避谈的问题"],
  [/move\s+the\s+needle/i, "带来实质改变"],
  [/low[- ]hanging\s+fruit/i, "触手可及的成果"],
  [/golden\s+goose/i, "摇钱树"],
  [/tip\s+of\s+the\s+iceberg/i, "冰山一角"],
  [/flywheel\s+effect?/i, "飞轮效应"],
  [/\bmoat\b/i, "护城河"],
  [/land\s+grab/i, "市场抢夺战"],
  [/land\s+and\s+expand/i, "先落地再扩张"],
  [/race\s+to\s+the\s+bottom/i, "底线竞争"],
  [/winner[- ]takes[- ]all/i, "赢者通吃"],
  [/level\s+playing\s+field/i, "公平竞争环境"],
  [/nail\s+in\s+the\s+coffin/i, "定局的一击"],
  [/canary\s+in\s+the\s+coal\s+mine/i, "预警信号"],
  [/writing\s+on\s+the\s+wall/i, "不祥之兆"],
  [/house\s+of\s+cards/i, "纸牌屋（一推就倒）"],
  [/wild\s+west/i, "无监管地带"],
  [/smoking\s+gun/i, "铁证"],
  [/blowout\s+(?:quarter|earnings|results)/i, "远超预期的财报"],
  [/home\s+run/i, "大获成功"],
  [/slam\s+dunk/i, "十拿九稳"],
  [/hail\s+mary/i, "孤注一掷"],
  [/black\s+swan/i, "黑天鹅"],
  [/grey\s+rhino|gray\s+rhino/i, "灰犀牛"],
  [/whack[- ]a[- ]mole/i, "打地鼠（按下葫芦浮起瓢）"],
  [/no\s+strings\s+attached/i, "无附加条件"],
  [/pie\s+in\s+the\s+sky/i, "空中楼阁"],
  [/can\s+of\s+worms/i, "一摊子麻烦"],
  [/pandora's\s+box/i, "潘多拉魔盒"],
  [/arms\s+race/i, "军备竞赛"],
  [/leapfrog/i, "弯道超车"],
  [/unicorn\s+startup|unicorn\s+company|\bunicorn\b/i, "独角兽"],
  [/down\s+round/i, "估值缩水融资轮"],
  [/bloodbath/i, "血洗（暴跌）"],
  [/dead\s+cat\s+bounce/i, "死猫跳"],
  [/catch\s+a\s+falling\s+knife/i, "接飞刀"],
  [/\bgut[- ]punch\b/i, "沉重一击"],
  [/['"“”「]?\s*no\s+['"“”「]?\s*jerk(?:s)?['"“”」]?\s+(?:rule|policy|culture)/i, "「不混蛋」文化"],
];
const IDIOM_RE = new RegExp("(" + IDIOM_ZH.map((r) => "(?:" + r[0].source + ")").join("|") + ")(?![A-Za-z])", "gi");
const IDIOM_LOOKUP = IDIOM_ZH; // 依数组顺序命中（先长后短由条目自身正则保证）
function idiomZh(m) {
  for (const [re, zh] of IDIOM_LOOKUP) { re.lastIndex = 0; if (re.test(m)) return zh; }
  return m;
}

// million/crore/lakh 系统性错译 10~100 倍（$1.4 Trillion→「1.4美元」、Rs 18 crore→「18亿卢比」、
// ₹1.88 Lakh Crore→「18.8亿卢比」，连续两天 xcheck 各抓 3 条同类），换措辞救不了——整组占位，
// 还原值直接写**换算后的中文**（$1.4 Trillion→「1.4万亿美元」），restore 零改动自动还原。
// 2026-11-16 修两处：①货币符与数字间的空格并进货币组（"Rs 18 crore" 的 Rs 后有空格），
// 但不能加「无货币符时的前导 \s*」——那会吃掉前词与数字间的空格，还原后出「raised130亿美元」粘连；
// ②换算结果压浮点尾差（1.005 billion → 10.049999999999999 亿），统一 toFixed(4) 去尾零。
const SCALE_RE = /(Rs\.?\s*|[$€£¥₹]\s*)?(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|thousand|lakh\s+crore|crore|lakh)(?![A-Za-z])/gi;
const SCALE_MULT = { trillion: 1e12, billion: 1e9, million: 1e6, thousand: 1e3, "lakh crore": 1e12, crore: 1e7, lakh: 1e5 };
const SCALE_CUR = { "$": "美元", "€": "欧元", "£": "英镑", "¥": "日元", "₹": "卢比", "Rs": "卢比", "Rs.": "卢比" };
const fmtNum = (v) => {
  const s = v.toFixed(4).replace(/\.?0+$/, "");
  return s === "" || s === "-0" ? "0" : s;
};
function scaleZh(m, cur, num, unit) {
  const v = Number(num.replace(/,/g, "")) * SCALE_MULT[unit.toLowerCase().replace(/\s+/g, " ")];
  let zh;
  if (v >= 1e12) zh = fmtNum(v / 1e12) + "万亿";
  else if (v >= 1e8) zh = fmtNum(v / 1e8) + "亿";
  else if (v >= 1e4) zh = fmtNum(v / 1e4) + "万";
  else zh = num;
  if (cur) zh += SCALE_CUR[String(cur).trim()] || "";
  return zh;
}
// URL / markdown 链接整体占位（2026-11-16 加）：腾讯会把 https:// 全角化成 https：//、
// 把图片 ![alt](url) 的孤立 ! 转成全角 ！，直接调 translateTexts 的调用方会拿到坏链接。
// zh.mjs 在外层也占位一次，重复无害（外层先做，这里看到的是占位符或纯文本）。
const URL_RE = /!\[[^\]]*\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s)\]）」”'"，。；、！？]+/gi;
// 2026-11-17 补：缩写量级词 300B / $1.4T / 5.46M（引擎把「300B tokens」直译成「300亿个令牌」，
// 差 10 倍——B 被当成「亿」）。与 SCALE_RE 同样整组占位、还原成中文量级。
// 右边界 (?![A-Za-z0-9]) 保证 512MB/4TB 这类复合单位不被误伤（M/T 后紧跟 B 即不匹配）。
const ABBR_RE = /(Rs\.?\s*|[$€£¥₹]\s*)?(\d[\d,]*(?:\.\d+)?)\s?([KMBT])(?![A-Za-z0-9])/g;
const ABBR_MULT = { K: 1e3, M: 1e6, B: 1e9, T: 1e12 };
function abbrZh(cur, num, unit) {
  const v = Number(num.replace(/,/g, "")) * ABBR_MULT[unit.toUpperCase()];
  let zh;
  if (v >= 1e12) zh = fmtNum(v / 1e12) + "万亿";
  else if (v >= 1e8) zh = fmtNum(v / 1e8) + "亿";
  else if (v >= 1e4) zh = fmtNum(v / 1e4) + "万";
  else zh = num;
  if (cur) zh += SCALE_CUR[String(cur).trim()] || "";
  return zh;
}

function protect(text, bag) {
  const put = (s) => { const i = bag.length; bag.push(s); return "zqx" + i + "zqx"; };
  let out = text.replace(URL_RE, (m) => put(m));      // 先护链接（含图片语法）
  out = out.replace(PREFIX_RE, (m) => put(m));        // 再护「模型名+版本+代号」
  // 习语定译（2026-11-15）：整短语占位、还原定译中文，排在人名之前（习语内无实体冲突）
  out = out.replace(IDIOM_RE, (m) => put(idiomZh(m)));
  // 人名（2026-11-14 接入 name-guard 共享 NAMES 表，与 zh.mjs 同源）：
  // 占位还原值是**中文译名**（Jensen Huang→黄仁勋），不是原词——雷达标题里的人名不再靠引擎音译。
  out = out.replace(NAME_RE, (m) => put(NAME_MAP.get(m.toLowerCase()) || m));
  // 量级词占位必须排在 UNIT_RE/PROTECT_RE 之前（词边界互不干扰，但顺序统一先数字后单位）
  out = out.replace(SCALE_RE, (m, cur, num, unit) => put(scaleZh(m, cur, num, unit)));
  out = out.replace(ABBR_RE, (m, cur, num, unit) => put(abbrZh(cur, num, unit)));
  // 2026-11-17 补：纯千分位数字（50,000 / 28,672）原样占位——引擎会输出全角逗号「50，000」，
  // 中文排版里既不规范也易被后续解析误判（已出现 4 处人工修）。
  out = out.replace(/\d{1,3}(?:,\d{3})+(?!\d)/g, (m) => put(m));
  // 单位必须**排在 PROTECT_RE 之前**：PROTECT_WORDS 里的 tts/watts 片段会先把 megawatts 切碎
  // （实测 megawatts → "megaw"+zqx 占位），落到 PROTECT_RE 后面就再也匹配不到完整单位词。
  out = out.replace(UNIT_RE, (m) => put(UNIT_ZH[m.toLowerCase()] || m));
  out = out.replace(PROTECT_RE, (m) => put(m));       // 再护单词表专名
  return out;
}
function restore(text, bag) {
  // 腾讯会把占位符里的数字拆出空格（zqx0zqx → "zqx 0 zqx" / "zqx 0zqx"），所以允许空白
  return unitZh(swapNames(S(text).replace(/zqx\s*(\d+)\s*zqx/gi, (m, i) => (bag[Number(i)] !== undefined ? bag[Number(i)] : m))));
  // swapNames 兜底：占位符被腾讯吞掉时人名会以英文原样残留在译文里，这里直接换成中文译名
}

export function translateEnabled() {
  const v = S(process.env.DSH_AI_RESEARCH_TRANSLATE).trim().toLowerCase();
  return !["0", "off", "false", "no", "none"].includes(v);
}

const keyOf = (t) => crypto.createHash("sha1").update(S(t)).digest("hex").slice(0, 16);

function loadCache() {
  try { return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) || {}; } catch { return {}; }
}
// 2026-11-15 修并发覆盖：此前直接把「进程启动时读到的内存快照」整体写回，
// 两个进程同时翻译时后写的会把对方新增条目全抹掉（实测 798 条被 27 条覆盖）；
// 现在写前重读磁盘做增量合并（内存优先），且临时文件名带 pid 防互相踩。
function saveCache(c) {
  try {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
    let disk = {};
    try { disk = JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) || {}; } catch { /* 坏文件当空 */ }
    const merged = Object.assign({}, disk, c);
    const tmp = `${CACHE_FILE}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(merged), "utf8");
    fs.renameSync(tmp, CACHE_FILE);
  } catch { /* 缓存写失败不影响主流程 */ }
}

async function callTencent(texts) {
  const body = {
    header: { fn: "auto_translation", client_key: "browser-chrome-" + process.pid + "-" + Date.now() },
    type: "plain",
    model_category: "normal",
    source: { lang: "en", text_list: texts },
    target: { lang: "zh" },
  };
  const r = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
      "Referer": "https://transmart.qq.com/",
      "Origin": "https://transmart.qq.com",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  if (!r.ok) throw new Error("transmart HTTP " + r.status);
  const j = await r.json();
  if (!j || !j.header || j.header.ret_code !== "succ") throw new Error("transmart: " + (j && (j.message || j.header && j.header.ret_code)));
  const out = j.auto_translation;
  if (!Array.isArray(out) || out.length !== texts.length) throw new Error("transmart 返回条数不符：" + (out && out.length) + " vs " + texts.length);
  return out.map(S);
}

/** 收集 res 里所有「需要翻译的英文文本」（去重）。
 * 2026-11-14 分流（用户定，借鉴热评报告规则）：buzz（社区热议）里纯表情/符号或 ≤12 字符的短梗，
 * 机翻只产噪音（纯表情被译成账号名、meme 直译成废话），不送翻译、渲染回落英文原文；
 * 资讯类长句照常机翻打底。items（新闻标题/摘要）不受此限。 */
function collectTexts(res) {
  const texts = new Set();
  let buzzSkipped = 0;
  const add = (v) => {
    const s = S(v).trim();
    if (!s || s.length < 2 || HAS_CJK.test(s)) return;
    texts.add(s.slice(0, TEXT_MAX_CHARS));
  };
  const addBuzz = (v) => {
    const s = S(v).trim();
    if (!s || s.length < 2 || HAS_CJK.test(s)) return;
    if (!/[A-Za-z]{2,}/.test(s) || s.length <= 12) { buzzSkipped++; return; }
    texts.add(s.slice(0, TEXT_MAX_CHARS));
  };
  for (const it of res.items || []) { add(it.title); add(it.summary); }
  for (const k of ["hn", "reddit", "x", "weixin"]) {
    for (const b of (res.buzz && res.buzz[k]) || []) {
      if (!b || typeof b !== "object") continue;
      addBuzz(b.title || b.name || b.text);
    }
  }
  texts.buzzSkipped = buzzSkipped;
  return texts;
}

function buildBatches(todo) {
  const batches = [];
  let cur = [], chars = 0;
  for (const t of todo) {
    const len = t.length + 8;
    if (cur.length && (cur.length >= BATCH_MAX_ITEMS || chars + len > BATCH_MAX_CHARS)) {
      batches.push(cur); cur = []; chars = 0;
    }
    cur.push(t); chars += len;
  }
  if (cur.length) batches.push(cur);
  return batches;
}

// 引擎怪癖归一：小写动词 falls 会被音译成「福尔斯」（引擎当 Falls 专名），归一成 declines。
// 必须在 protect 之前替换，缓存 key 仍用原文；"Niagara Falls" 大写不动（无 i 标志）。
// 2026-11-16：原只在 translateRes 里做、translateTexts 缺失 → 两条通道行为不一致，抽到公共层。
const normFalls = (t) => t.replace(/\bfalls\b/g, "declines");

/**
 * 统一的批处理循环（2026-11-16 抽取）：protect → 请求（2 次重试）→ restore →
 * 占位符泄漏单条重翻 → 落缓存。原本 translateTexts 与 translateRes 各抄一份，
 * 导致 falls 归一、占位泄漏重翻、sameText 统计三处只在其中一边存在。
 * @returns {{batches:number,done:number,failed:number,same:number,timedOut:boolean,phRetry:number}}
 */
async function runBatches(todo, cache, opts = {}) {
  const t0 = opts.t0 || Date.now();
  const budget = Number(opts.budgetMs || 180000);
  const verbose = !!opts.verbose;
  const batches = buildBatches(todo);
  let done = 0, failed = 0, same = 0, timedOut = false, phRetry = 0;
  for (const batch of batches) {
    if (Date.now() - t0 > budget) { timedOut = true; break; }
    // 专名保护：翻前换成 zqx<N>zqx 占位符，翻完还原（Anthropic→「拟人化」这类错译的根因）。
    const bag = [];
    const guarded = batch.map((t) => protect(normFalls(t), bag));
    let arr = null;
    for (let attempt = 0; attempt < 2 && !arr; attempt++) {
      if (attempt) await sleep(800);
      try { arr = await callTencent(guarded); } catch (e) {
        if (attempt === 1) { failed += batch.length; if (verbose) console.error("[translate] 失败：" + (e && e.message)); }
      }
    }
    if (!arr) continue;
    arr = arr.map((t) => restore(t, bag));
    // 占位符泄漏检测（2026-11-15）：译文里残留 zqx 字样 = 腾讯吞改了占位符，
    // 该行单独重翻一次，仍失败保留当前译文。
    for (let i = 0; i < arr.length; i++) {
      if (!/zqx/i.test(arr[i])) continue;
      if (Date.now() - t0 > budget) break;
      try {
        const bag2 = [];
        const one = await callTencent([protect(normFalls(batch[i]), bag2)]);
        if (one && one[0]) {
          const fixed = S(restore(one[0], bag2)).trim();
          if (fixed && !/zqx/i.test(fixed)) { arr[i] = fixed; phRetry++; }
        }
      } catch { /* 重翻失败保留原译文 */ }
      await sleep(300);
    }
    batch.forEach((t, i) => {
      const zh = S(arr[i]).trim();
      if (!zh) return;
      // 还原后与原文相同 = 整条几乎全是专名（如 "Gemini 4 Hydrogen"），腾讯原样返回；
      // 这类标题保留英文是正确的（渲染回落原文），不计入失败、也不写缓存。
      if (zh === t) { same++; return; }
      cache[keyOf(t)] = zh;
    });
    done += batch.length;
    saveCache(cache); // 每批落一次，中断也保留
    if (verbose) console.error("[translate] " + done + "/" + todo.length + " 用时 " + ((Date.now() - t0) / 1000).toFixed(1) + "s");
    await sleep(200);
  }
  if (timedOut) saveCache(cache);
  return { batches: batches.length, done, failed, same, timedOut, phRetry };
}

/**
 * 通用文本翻译：任意字符串数组 → 中文数组（同序；不需翻译或翻不出的位置为 null）。
 * 与雷达共用缓存与专名保护，供其他会话/脚本直接 import 使用。
 *   import { translateTexts } from ".../translate.mjs";
 *   const zh = await translateTexts(["OpenAI ships a new model"]);
 */
export async function translateTexts(texts, opts = {}) {
  const src = (texts || []).map((t) => S(t).slice(0, TEXT_MAX_CHARS));
  if (!translateEnabled()) return src.map(() => null);
  const budgetMs = Number(opts.budgetMs || process.env.DSH_AI_RESEARCH_TRANSLATE_BUDGET_MS || 180000);
  const t0 = Date.now();
  const cache = loadCache();
  const need = (t) => t.length >= 2 && !HAS_CJK.test(t);
  const todo = [...new Set(src.filter((t) => need(t) && !cache[keyOf(t)]))];
  if (opts.verbose) console.error("[translate] 待翻 " + todo.length + " 条，分 " + buildBatches(todo).length + " 批");
  await runBatches(todo, cache, { t0, budgetMs, verbose: opts.verbose });
  return src.map((t) => { const z = cache[keyOf(t)]; return need(t) && z ? unitZh(z) : null; });
}

/**
 * 原地补 title_zh / summary_zh（buzz 补 title_zh），返回 res 本身。
 * opts.budgetMs 默认 180000；超预算就停，剩余保持英文。
 * opts.verbose 打印进度（cli 用）。
 */
export async function translateRes(res, opts = {}) {
  if (!res || res.__translated) return res;
  if (!translateEnabled()) { res.translateStats = { skipped: "disabled" }; return res; }
  const budgetMs = Number(opts.budgetMs || process.env.DSH_AI_RESEARCH_TRANSLATE_BUDGET_MS || 180000);
  const t0 = Date.now();
  const cache = loadCache();
  const texts = collectTexts(res);

  let hit = 0;
  const todo = [];
  for (const t of texts) {
    if (cache[keyOf(t)]) hit++;
    else todo.push(t);
  }

  if (opts.verbose) console.error("[translate] 待翻 " + todo.length + " 条 / " + texts.size + " 条（缓存命中 " + hit + "），分 " + buildBatches(todo).length + " 批");
  const st = await runBatches(todo, cache, { t0, budgetMs, verbose: opts.verbose });

  const zhOf = (v) => {
    const s = S(v).trim();
    if (!s) return null;
    const z = cache[keyOf(s.slice(0, TEXT_MAX_CHARS))];
    return z ? unitZh(z) : null;
  };
  let nTitle = 0, nSum = 0, nBuzz = 0;
  for (const it of res.items || []) {
    const tz = zhOf(it.title);
    if (tz) { it.title_zh = tz; nTitle++; }
    const sz = zhOf(it.summary);
    if (sz) { it.summary_zh = sz; nSum++; }
  }
  for (const k of ["hn", "reddit", "x", "weixin"]) {
    for (const b of (res.buzz && res.buzz[k]) || []) {
      if (!b || typeof b !== "object") continue;
      const tz = zhOf(b.title || b.name || b.text);
      if (tz) { b.title_zh = tz; nBuzz++; }
    }
  }
  res.__translated = true;
  res.translateStats = {
    candidates: texts.size, cacheHit: hit, requested: todo.length,
    buzzSkipped: texts.buzzSkipped || 0,
    batches: st.batches, timedOut: st.timedOut, failed: st.failed, sameText: st.same, placeholderRetry: st.phRetry,
    titleZh: nTitle, summaryZh: nSum, buzzZh: nBuzz,
    elapsedMs: Date.now() - t0,
  };
  return res;
}