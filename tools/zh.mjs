#!/usr/bin/env node
// 通用英译中 CLI —— 复用雷达翻译通道（腾讯 Transmart + 专名保护 + 共享缓存）
// 真源模块：本目录 translate.mjs / name-guard.mjs（打包版与脚本同目录）
//
// 用法：
//   node zh.mjs "OpenAI ships a new model"
//   node zh.mjs -f article.txt [-o article.zh.txt]
//   cat lines.txt | node zh.mjs
//   node zh.mjs --fresh -f article.txt      # 不读/不写共享缓存（重测可疑译文）
//
// 行为：逐行翻译，空行原样保留，已有的中文行原样输出（不翻译）；翻译失败的行原样输出。
// 缓存：~/.dsh-ai-research/translate-cache.json（与雷达共用，重复文本零请求）。
//   ⚠️ 缓存 key=原文：TM 错译会一直命中，重测可疑译文必须 --fresh（否则只会拿到同一个错译）。
//
// 2026-11-15 三项增强（AWS SageMaker 篇实测，数字错译 51→7 处且零数值错误，见 tests/REPORT.md）：
//   ① 数字/型号 token 占位（NUMA…NUMZ）——价格、百分比、千分位、版本号、实例型号完全不过引擎
//   ② markdown 代码围栏（```）内整段跳过翻译
//   ③ 行数守恒——非空输入行必须得到非空译文，否则单行重翻兜底
//
// 2026-11-16 七项优化：
//   ① 修 fence 污染 bug：falls/ships/teases/exec/claims 的引擎怪癖归一原先在切行前对全文 replace，
//      代码围栏内的文本会被改写并原样输出 → 改为逐行、仅对送翻行做
//   ② SCALE_RE 补 lakh / crore / lakh crore / thousand 与 ₹ / Rs（印度东南亚稿），不再把量级词留给引擎乱译
//   ③ 量级换算统一走 SI：1300 billion 现在输出「1.3万亿」而不是「13000亿」
//   ④ 删死代码：bag.__scale / restoreScale / numOk 的 needS（SCALE 实际写在 bag.__num 的 ["__SCALE__",…]）
//   ⑤ --fresh / --no-cache：缓存指到临时目录（必须在 import translate.mjs 之前设 env）
//   ⑥ -o/--out 落文件、--help
//   ⑦ 整批全失败快速失败：引擎挂了不再逐行 2 次重翻 + 800ms 退避

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

// ---------- 代理（2026-11-21 ai-news-daily 版定制，2026-11-23 合并进真源）----------
// Node 原生 fetch（undici）默认不读 http(s)_proxy；node ≥24 带 --use-env-proxy 才会读。
// 实测（2026-11-21）：腾讯 Transmart 走 127.0.0.1:7890 反而翻不出来（境外出口被挡），
// 所以默认仍直连，只在显式要求时启用：
//   ZH_ENV_PROXY=1 node zh.mjs ...   或   node zh.mjs --env-proxy ...
// 启用后若代理变量存在，就用 --use-env-proxy 自重启一次（argv/stdin 原样透传）。
// 等价写法（不动脚本）：node --use-env-proxy zh.mjs ...
// 只想让墙外域名走代理：设 NO_PROXY=transmart.qq.com（Node 按 NO_PROXY 放行）。
const PROXY_KEYS = ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"];
const wantProxy = PROXY_KEYS.some((k) => (process.env[k] || "").trim());
const envProxyOn = process.env.ZH_ENV_PROXY === "1" || process.argv.includes("--env-proxy");
if (envProxyOn && wantProxy && !process.execArgv.includes("--use-env-proxy") && !process.argv.includes("--no-env-proxy")) {
  const r = spawnSync(process.execPath, ["--use-env-proxy", ...process.argv.slice(1)], {
    stdio: "inherit",
    env: process.env,
  });
  if (!r.error) process.exit(r.status ?? 1);
}

const MOD = path.join(path.dirname(new URL(import.meta.url).pathname), "translate.mjs");
const GUARD = path.join(path.dirname(new URL(import.meta.url).pathname), "name-guard.mjs");

// ---------- 参数 ----------
const argv = process.argv.slice(2);
const hasFlag = (...names) => names.some((n) => argv.includes(n));
const optValue = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null;
};

if (hasFlag("-h", "--help")) {
  process.stdout.write(`用法：
  zh.mjs "文本"                         翻译一段文本
  zh.mjs -f <文件> [-o <输出文件>]       翻译文件
  cat lines.txt | zh.mjs                翻译 stdin
选项：
  -f, --file <路径>   从文件读入
  -o, --out <路径>    写到文件（缺省 stdout）
  --fresh, --no-cache 不读不写共享缓存（重测可疑/坏译时用）
  --env-proxy         读 http(s)_proxy（=ZH_ENV_PROXY=1，内部以 node --use-env-proxy 自重启）
  --no-env-proxy      强制直连，忽略代理变量
  -h, --help          显示本帮助
环境：
  ZH_VERBOSE=1        打印进度到 stderr
  DSH_AI_RESEARCH_CACHE  缓存目录（默认 ~/.dsh-ai-research）
`);
  process.exit(0);
}

// --fresh 必须在 import translate.mjs 之前生效：CACHE_DIR 是模块顶层常量
const FRESH = hasFlag("--fresh", "--no-cache");
let freshDir = null;
if (FRESH) {
  freshDir = fs.mkdtempSync(path.join(os.tmpdir(), "zhcache-"));
  process.env.DSH_AI_RESEARCH_CACHE = freshDir;
}

const { NAME_MAP, NAME_RE, swapNames, restoreNames, lostPlaceholder } = await import(GUARD);
const { translateTexts } = await import(MOD);

// ---------- 读入 ----------
const VALUE_OPTS = new Set(["-f", "--file", "-o", "--out"]);
// 只剔除认识的开关；其余以 - 开头的字符串当正文（"−5% inflation" 这类不能被静默吞掉）
const BARE_FLAGS = new Set(["--fresh", "--no-cache", "-h", "--help", "--env-proxy", "--no-env-proxy", "--use-env-proxy"]);
const positional = [];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (VALUE_OPTS.has(a)) { i++; continue; }
  if (BARE_FLAGS.has(a)) continue;
  positional.push(a);
}

let text = "";
const filePath = optValue("-f") || optValue("--file");
if (filePath) text = fs.readFileSync(filePath, "utf8");
else if (positional.length) text = positional.join(" ");
else text = fs.readFileSync(0, "utf8");

const outPath = optValue("-o") || optValue("--out");

// ---------- 引擎怪癖归一（逐行做，围栏内代码绝不动）----------
// 已知引擎怪癖：动词 falls 会被音译成「福尔斯」（引擎把它当 Falls 专名）。
// 归一化成 declines 再翻，语义不变；"Niagara Falls" 这类专名（前一词大写开头）不动。
// 同类：ships 会被音译成「船舶」（当船的复数）→ releases；teases 被译成「挑逗」→ previews；
// 缩写 exec 引擎不认识会原样保留 → executive；诉讼语境的 claims 被译成「索赔」→ alleges。
function normQuirks(line) {
  let s = line;
  s = s.replace(/(?<![A-Z][a-z]+\s)\bfalls\b/g, "declines");
  s = s.replace(/\bships\b/g, "releases");
  s = s.replace(/\bteases\b/g, "previews");
  s = s.replace(/\bexec\b/g, "executive");
  s = s.replace(/\b(lawsuit|suit|complaint|case) claims\b/gi, "$1 alleges");
  s = s.replace(/\bclaims\b(?=\s+(that|it|the|its)\b)/gi, "alleges");
  s = s.replace(/\bhuman action simulation\b/gi, "human behavior simulation");
  return s;
}

const lines = text.replace(/\r\n/g, "\n").split("\n");
const URL_RE = /!\[[^\]]*\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s)\]）」”'"，。；、！？]+/gi;
// 数字 token：任何含数字的连续片段（$178.67、4.5%、1,205、ml.g5.8xlarge、GPT-4、2026-11-15、B2B 全部命中）
const NUM_RE = /[\w$€£¥%.,+-]*\d[\w$€£¥%.,+-]*/g;
// 量级词整组：$13 billion / 44.1 million / Rs 18 crore / 2.3 trillion —— 数字+量级词必须一起占位（NUM{X}），
// 因为引擎对量级词系统性错译且换措辞救不了（2026-11-15 实测：$13 billion→「10亿美元」、
// 13 billion dollars→「13亿美元」错 100 倍、40 billion dollars→「40 10亿美元」崩坏）。
// 翻后按 SI 确定性换算还原成中文量级（见 zhAmount）。
// 2026-11-16：补 thousand / lakh / crore / lakh crore 与 ₹ / Rs（印度财经稿常见 Rs 18 crore）。
// 货币符与其后空格一起捕获（"Rs 18 crore" 的 Rs 与数字之间有空格），还原前 trim。
const SCALE_RE = /(Rs\.?\s*|₹\s*|[$€£¥]\s*)?(\d[\d,.]*)\s*(lakh\s+crore|crore|lakh|trillion|billion|million|thousand)\b(?:\s+(dollars?|euros?|pounds?|yen|yuan|rupees?)\b)?/gi;
const SCALE_MUL = { trillion: 1e12, billion: 1e9, million: 1e6, thousand: 1e3, "lakh crore": 1e12, crore: 1e7, lakh: 1e5 };
const SCALE_CUR = { $: "美元", "€": "欧元", "£": "英镑", "¥": "元", "₹": "卢比", Rs: "卢比", "Rs.": "卢比" };
const SCALE_WORD = { dollar: "美元", dollars: "美元", euro: "欧元", euros: "欧元", pound: "英镑", pounds: "英镑", yen: "日元", yuan: "元", rupee: "卢比", rupees: "卢比" };

// 统一 SI → 中文量级（≥1e12 万亿 / ≥1e8 亿 / ≥1e4 万，其余原数）。
// 用 toFixed(4) 压浮点尾差，再去掉尾随零；不改用「原数×10 亿」的老办法是为了让 1300 billion → 1.3万亿。
const zhAmount = (si) => {
  const fmt = (v) => {
    let s = v.toFixed(4).replace(/\.?0+$/, "");
    return s === "" || s === "-0" ? "0" : s;
  };
  if (si >= 1e12) return `${fmt(si / 1e12)}万亿`;
  if (si >= 1e8) return `${fmt(si / 1e8)}亿`;
  if (si >= 1e4) return `${fmt(si / 1e4)}万`;
  return fmt(si);
};
const scaleRestore = (cur, num, unit, word) => {
  const n = parseFloat(String(num).replace(/,/g, ""));
  if (!isFinite(n)) return null;
  const key = String(unit).toLowerCase().replace(/\s+/g, " ");
  const mul = SCALE_MUL[key];
  if (!mul) return null;
  const curRaw = cur ? String(cur).trim() : "";
  const curZh = curRaw ? SCALE_CUR[curRaw] || "" : (word ? SCALE_WORD[String(word).toLowerCase()] || "" : "");
  return `${zhAmount(n * mul)}${curZh}`;
};

// 1) URL/数字/人名 → 占位符（按行独立编号；PERSON[A-Z] 给 URL/人名，NUM[A-Z] 给数字）
// URL 必须占位：实测腾讯会把 markdown 链接里的 https:// 全角化成 https：//（2026-11-13）；
// markdown 图片 ![alt](url) 必须整体占位：实测整行送翻时腾讯把孤立的 ! 转成全角 ！，图片语法被破坏（2026-11-15）。
// 数字必须占位：实测腾讯会丢千分位大数、吞美元符、把 percent 错位（2026-11-15，AWS 篇基线丢 21 个数字）。
const guarded = [], bags = [], fenceFlag = [];
// 占位字母池避开 B：NUMB 恰是英文真词 numb，引擎会把它当词汇改写（实测 NUMB→$NUMBER，
// 占位符被吞导致对账失败整行兜底成原文）；其余 NUMx 不构成英文词，实测全保留。
const PPOOL = "ACDEFGHIJKLMNOPQRSTUVWXYZ";
const PLIM = PPOOL.length;
let inFence = false;
for (const l of lines) {
  if (/^\s*```/.test(l)) { inFence = !inFence; fenceFlag.push(true); guarded.push(l); bags.push(null); continue; }
  if (inFence) { fenceFlag.push(true); guarded.push(l); bags.push(null); continue; }   // 代码围栏内不翻
  fenceFlag.push(false);
  if (!l.trim()) { guarded.push(l); bags.push(null); continue; }

  // 混合行三态（2026-11-15 加）：旧逻辑「行里含中文就整行不翻」有两个实测坑——
  // ① 「摘要：In this post...」被整行跳过（前缀中文+英文主体）；② 源 feed 标题本身脏
  // （"...releasing results密集ly; the Federa"），2 个中文字符导致整行不翻。
  const zhCount = (l.match(/[\u4e00-\u9fff]/g) || []).length;
  let line = l, prefix = "", zhGuard = null;
  const mPre = l.match(/^([\u4e00-\u9fff]{1,8}\s*[：:]\s*)(.+)$/);
  if (mPre && mPre[2].length >= 30 && (mPre[2].match(/[\u4e00-\u9fff]/g) || []).length === 0) {
    prefix = mPre[1]; line = mPre[2];                                  // ① 剥中文前缀，只翻英文主体
  } else if (zhCount > 0 && (zhCount > 3 || l.length < 80)) {
    guarded.push(l); bags.push(null); continue;                        // 正常中文行：不翻（现状）
  } else if (zhCount > 0) {
    zhGuard = true;                                                    // ② 脏行：中文 ≤3 字符 → 中文片段也占位保护
  }

  line = normQuirks(line);   // 引擎怪癖归一：只对送翻行做（围栏行已在上面 continue）

  const bag = { __num: {} };
  if (prefix) bag.__prefix = prefix;
  let i = 0, ni = 0;
  let g = line.replace(URL_RE, (m) => {
    if (i >= PLIM) return m;                  // 单行占位符超字母池会越界，极端场景放弃保护
    const c = PPOOL[i]; bag[c] = m; i++; return `PERSON${c}`;
  });
  g = g.replace(SCALE_RE, (m, cur, num, unit, word) => {
    if (ni >= PLIM) return m;   // 量级组与数字 token 共用 NUM{X} 占位——引擎只认 NUM/PERSON 前缀，自造 SCALE 前缀会被音译（实测 SCALEA→「斯卡莱亚」）
    const c = PPOOL[ni]; bag.__num[c] = ["__SCALE__", cur, num, unit, word]; ni++; return `NUM${c}`;
  });
  g = g.replace(NUM_RE, (m) => {
    if (ni >= PLIM) return m;                 // 数字占位超限时保留原文（数字仍在，由 numcheck 校验兜底）
    const c = PPOOL[ni]; bag.__num[c] = m; ni++; return `NUM${c}`;
  });
  g = g.replace(NAME_RE, (m) => {
    if (i >= PLIM) return m;
    const c = PPOOL[i]; bag[c] = NAME_MAP.get(m.toLowerCase()) || m; i++; return `PERSON${c}`;
  });
  if (zhGuard) {
    // 脏行里的裸中文片段也占位保护（"…results密集ly; the Federa" 的「密集」必须原样回来）
    g = g.replace(/[\u4e00-\u9fff]{1,6}/g, (m) => {
      if (i >= PLIM) return m;
      const c = PPOOL[i]; bag[c] = m; i++; return `PERSON${c}`;
    });
  }
  guarded.push(g); bags.push(bag);
}

// 2) 整批翻译
const zh = await translateTexts(guarded, { verbose: !!process.env.ZH_VERBOSE });

// 2.5) 整批全失败快速失败：引擎/网络挂了时，逐行重翻只会把整体拖成 N 行 × 2 次请求 + 800ms 退避。
// 判据：所有需要翻的行（非围栏、非空、有 bag）译文都为空 → 判定本轮翻译不可用，直接原文输出。
const needIdx = [];
for (let i = 0; i < guarded.length; i++) if (!fenceFlag[i] && guarded[i].trim() && bags[i]) needIdx.push(i);
const alive = needIdx.filter((i) => zh[i] != null && String(zh[i]).trim()).length;
const ENGINE_DOWN = needIdx.length > 0 && alive === 0;
if (ENGINE_DOWN && process.env.ZH_VERBOSE) console.error("[zh] 整批翻译失败（引擎不可用），本轮原样输出");

// 3) 行数守恒 + 占位符计数：非空输入行必须得到非空译文，且 NUM 占位符一个不丢——
//    「被吞+附近数字串位」是静默损坏（实测 "RFC 7523"→"RFC 5."、"HTTP 401"→"HTTP 200"，
//    无残留、无空行，只查残留拦不住），必须按送出数逐行对账；对不齐单行重翻，仍不齐置 null
//    由输出段按原文兜底，绝不让错数字落盘。
// numOk：每个送出的 NUM 占位符字母都必须在译文里至少出现一次（集合覆盖）。
// 只数个数不够——腾讯会「复制」占位符补位（实测 "HTTP NUMA … HTTP NUMB"→"NUMA … NUMA"，
// 计数守恒但 401 变 200），必须逐字母对账。
const numOk = (t, bag) => {
  if (!bag) return true;
  const need = Object.keys(bag.__num);
  if (!need.length) return true;
  const got = new Set((String(t).match(/NUM([A-Za-z])/g) || []).map((m) => m.slice(3).toUpperCase()));
  return need.every((c) => got.has(c));
};
if (!ENGINE_DOWN) {
  for (let i = 0; i < guarded.length; i++) {
    if (fenceFlag[i] || !guarded[i].trim()) continue;
    let bad = zh[i] == null || !String(zh[i]).trim();
    if (!bad && bags[i]) bad = lostPlaceholder(String(zh[i]), bags[i]) || /(NUM[A-Za-z])/.test(String(zh[i])) || !numOk(String(zh[i]), bags[i]);
    if (bad) {
      // 重翻输入加零宽空格扰动：单行 key 与批处理相同，会命中批处理写入的坏缓存，扰动后强制真请求。
      // 引擎对占位行有抽风（同一行两次请求结果不同，实测一次保留 NUMA 一次吞掉），扰动试 2 次提高恢复率。
      let oneT = null;
      for (const z of ["\u200b", "\u200b\u200b"]) {
        const one = await translateTexts([guarded[i] + z]);
        oneT = one && one[0] ? String(one[0]).replace(/\u200b/g, "") : null;
        if (oneT && oneT.trim() && !lostPlaceholder(oneT, bags[i]) && !/(NUM[A-Za-z])/.test(oneT) && numOk(oneT, bags[i])) break;
        oneT = null;
      }
      if (oneT) {
        zh[i] = oneT;
      } else {
        zh[i] = null;   // 单行重翻也保不全 → 输出段按原文兜底
      }
    }
  }
}

// 4) 输出：还原占位符（数字 → 人名/URL）→ 补换残留英文人名 →（极端兜底）原文重翻
const fixParens = (t) => t.replace(/\]\s*[（(]\s*(https?:\/\/[^\s）)]+?)\s*[）)]/g, "]($1)");
const restoreNum = (t, bag) => t.replace(/NUM([A-Za-z])/g, (m, c) => {
  const v = bag.__num[c.toUpperCase()];
  if (Array.isArray(v)) return scaleRestore(v[1], v[2], v[3], v[4]) ?? m;   // 量级组 → SI 确定性换算中文（$13 billion→130亿美元）
  return v === undefined ? m : v;
});
// 量级还原后的数值回检：每个量级组的换算结果必须独立出现在译文里（前面不能还是数字——
// 防「占位符补位」损坏，实测引擎会在 NUMC 前补 100 变「10023亿美元」，includes 会误判含「23亿」）
const scaleOk = (t, bag) => {
  if (!bag) return true;
  return Object.values(bag.__num).filter(Array.isArray).every((v) => {
    const s = scaleRestore(v[1], v[2], v[3], v[4]);
    return s == null || new RegExp("(?<![0-9])" + s).test(t);
  });
};
const finish = (raw, bag) => {
  if (raw == null) return null;
  let t = fixParens(restoreNames(swapNames(restoreNum(String(raw), bag), bag), bag));
  if (/(NUM[A-Za-z])/.test(t) || !scaleOk(t, bag)) t = null;
  return t;
};
const out = [];
for (let i = 0; i < lines.length; i++) {
  const src = lines[i];
  if (fenceFlag[i]) { out.push(src); continue; }
  if (!src.trim() || bags[i] == null) { out.push(swapNames(src)); continue; }   // 空行/跳过行（bags==null=正常中文行）
  let t = finish(zh[i], bags[i]);
  if (t == null) {
    // 终极兜底重翻（同样零宽扰动绕过坏缓存）
    const again = ENGINE_DOWN ? null : await translateTexts([guarded[i] + "\u200b"]);
    const againT = again && again[0] ? String(again[0]).replace(/\u200b/g, "") : null;
    t = againT && numOk(againT, bags[i]) ? finish(againT, bags[i]) : null;
    if (t == null) t = src;   // 数字还原失败/量级值缺失/引擎不可用 → 保留原文
  }
  if (bags[i] && bags[i].__prefix) t = bags[i].__prefix + t;   // 剥掉的中文前缀（「摘要：」）拼回
  out.push(t);
}

const result = out.join("\n");
if (outPath) {
  fs.writeFileSync(outPath, result, "utf8");
  if (process.env.ZH_VERBOSE) console.error(`[zh] 已写入 ${outPath}`);
} else {
  process.stdout.write(result);
}

if (freshDir) { try { fs.rmSync(freshDir, { recursive: true, force: true }); } catch { /* 忽略 */ } }