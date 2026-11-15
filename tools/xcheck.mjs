// xcheck.mjs —— 机翻交叉校验（2026-11-15 用户定：以后腾讯翻完用 Google clients5 重翻比对，差异出人工审清单）
// 原理：腾讯 Transmart 是主通道（批量快、专名保护稳），Google clients5 是第二通道（长句语序顺、
// 金额保留原文单位不易错）。两译文数字按「数值×单位」归一到 SI 值后对比——90.4B ↔ 904亿 这类
// 合法换算自动放行，2T→2000亿（差10倍）这类真错才标出。差异条目写人工审清单，终审归人工。
// 稳定性：谷歌本次不可用（429/超时）时只记 skipped，绝不阻塞主产物；成功结果落缓存，重跑零请求。
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const GURL = "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=en&tl=zh-CN&q=";
// 第二通道默认彩云小译：国内直连、40 条/批约 0.6s、数字与单位保留好（人名会音译，不影响数字比对）；
// 谷歌 clients5 保留为备选（--xc-engine google），但会按 IP 限流 429。
const CAIYUN_URL = "https://api.interpreter.caiyunai.com/v1/translator";
const CAIYUN_TOKEN = "token 3975l6lr5pcbvidl6jl2";
const CAIYUN_BATCH = 40;
// 第三通道 MyMemory（翻译记忆库，质量最高、会正确换算 crore/lakh，但单条 2s 且免费额度约 5 万字符/天）
// 只做抽样复核，不跑全量。额度按请求字符计：匿名 5000 字符/天，带 de=<你的邮箱> 50000 字符/天（不需要注册，
// 官方说明见 doc/usagelimits.php）；本机可在环境里设 DSH_AI_RESEARCH_MM_EMAIL 提到 5 万。
const MMURL = "https://api.mymemory.translated.net/get";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36";
const CACHE = path.join(os.homedir(), ".dsh-ai-research", "xcheck-cache.json");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 数量级词（trillion/billion/万/亿…）与真计量单位（瓦/字节/赫兹）分开：
// 数量级词是「数值的一部分」，折算后按裸数字比（115,000 ↔ 11.5万）；真单位仍要求两边都有单位。
const EN_SCALE = {
  trillion: 1e12, billion: 1e9, million: 1e6, thousand: 1e3, hundred: 1e2,
  crore: 1e7, lakh: 1e5, lakhcrore: 1e12,
  k: 1e3, m: 1e6, b: 1e9, t: 1e12, bn: 1e9, mn: 1e6, tn: 1e12,
};
const EN_MEASURE = {
  gigawatt: 1e9, gigawatts: 1e9, gw: 1e9,
  megawatt: 1e6, megawatts: 1e6, mw: 1e6,
  kilowatt: 1e3, kilowatts: 1e3, kw: 1e3,
  ghz: 1e9, mhz: 1e6, khz: 1e3,
  gb: 1e9, mb: 1e6, kb: 1e3, tb: 1e12,
};
const EN_UNIT = { ...EN_SCALE, ...EN_MEASURE };
const ZH_SCALE = { 万亿: 1e12, 十亿: 1e9, 亿: 1e8, 千万: 1e7, 百万: 1e6, 万: 1e4, 千: 1e3, 百: 1e2 };
const ZH_MEASURE = { 万千瓦: 1e7, 吉瓦: 1e9, 兆瓦: 1e6, 千瓦: 1e3, 瓦: 1 };
const ZH_UNIT = { ...ZH_SCALE, ...ZH_MEASURE };
// 中文数字（用于「四大实验室」「第三季度」这类英文数字被译成汉字的情形）
const CN_SMALL = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };

const NUM = String.raw`(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)`;
const toN = (s) => Number(String(s).replace(/,/g, ""));

// 归一化：全角逗号/句点/空格当分隔符（腾讯会把 68,000 输出成 68，000）
function norm(s) {
  return String(s || "")
    .replace(/[，､]/g, ",")
    .replace(/[．]/g, ".")
    .replace(/[\u3000\u00a0]/g, " ")
    .replace(/\s+/g, " ");
}

// 英文原文数字：数字 + 紧跟单位词（允许空白）→ si 值；无单位数字（日期/序号/百分比）si=数值本身
export function parseNums(text) {
  const out = [];
  // 年代词两边写法天然不同（2010s ↔ 21世纪10年代），剥掉不参与数字比对
  const s = norm(text).replace(/\b(19|20)\d0s\b/gi, " ").replace(/\d{1,2}\s*世纪\s*\d{1,2}\s*年代|\d{4}\s*年代/g, " ");
  const re = new RegExp(
    "(" + NUM + ")\\s*(lakh\\s*crore|crore|lakh|trillion|billion|million|thousand|hundred|gigawatts?|megawatts?|kilowatts?|bn|mn|tn|gw|mw|kw|ghz|mhz|khz|gb|mb|kb|tb|[kmbt])(?![a-z0-9])",
    "gi"
  );
  let m;
  const consumed = new Set();
  while ((m = re.exec(s))) {
    const unit = m[2].replace(/\s+/g, "").toLowerCase();
    if (EN_MEASURE[unit]) {
      out.push({ n: toN(m[1]), si: toN(m[1]) * EN_MEASURE[unit], unit: true });
      for (let i = m.index; i < m.index + m[0].length; i++) consumed.add(i);
    } else if (EN_SCALE[unit]) {
      const si = toN(m[1]) * EN_SCALE[unit];
      out.push({ n: si, si, unit: false });
      for (let i = m.index; i < m.index + m[0].length; i++) consumed.add(i);
    }
  }
  const re2 = new RegExp("(" + NUM + ")(%|℃)?", "g");
  while ((m = re2.exec(s))) {
    if (consumed.has(m.index)) continue;
    out.push({ n: toN(m[1]), si: toN(m[1]), unit: false });
  }
  return out;
}

// 中文译文数字：数字 + 中文单位（万千瓦 先于 千瓦 匹配）→ si 值；无单位数字直接比
export function parseZhNums(text) {
  const out = [];
  const s = norm(text).replace(/\b(19|20)\d0s\b/gi, " ").replace(/\d{1,2}\s*世纪\s*\d{1,2}\s*年代|\d{4}\s*年代/g, " ");
  // 中文单位 + 拉丁单位都认（腾讯常把 27B 写成「27 B」、5.46M 写成「5.46 M」、4T 写成「4 T」），
  // 并允许「30多亿」「约 3 亿」这类量词插入（多/余/几/左右）。
  const re = new RegExp(
    "(" + NUM + "|[一二两三四五六七八九十])\\s*(?:多|余|几|左右)?\\s*(万千瓦|万亿|十亿|千万|百万|亿|万|千|百|吉瓦|兆瓦|千瓦|瓦|%|℃|lakh\\s*crore|crore|lakh|trillion|billion|million|thousand|hundred|gigawatts?|megawatts?|kilowatts?|bn|mn|tn|gw|mw|kw|ghz|mhz|khz|gb|mb|kb|tb|[kmbt](?![A-Za-z0-9]))?",
    "gi"
  );
  let m;
  while ((m = re.exec(s))) {
    // 中文数字（两万亿美元 / 三万 / 十亿）也要认，否则腾讯的汉字数字会被当成「数字全丢」误报 high
    const raw = m[1];
    const n = /[0-9]/.test(raw) ? toN(raw) : CN_SMALL[raw];
    const u = m[2];
    if (!u || u === "%" || u === "℃") out.push({ n, si: n, unit: false });
    else if (ZH_MEASURE[u]) out.push({ n, si: n * ZH_MEASURE[u], unit: true });
    else if (ZH_SCALE[u]) { const si = n * ZH_SCALE[u]; out.push({ n: si, si, unit: false }); }
    else {
      const key = String(u).replace(/\s+/g, "").toLowerCase();
      if (EN_MEASURE[key]) out.push({ n, si: n * EN_MEASURE[key], unit: true });
      else if (EN_SCALE[key]) { const si = n * EN_SCALE[key]; out.push({ n: si, si, unit: false }); }
      else out.push({ n, si: n, unit: false });
    }
  }
  return out;
}

function close(a, b) {
  if (!isFinite(a) || !isFinite(b)) return false;
  const d = Math.abs(a - b), s = Math.max(Math.abs(a), Math.abs(b), 1);
  return d / s < 0.02 || d < 1e-6;
}

// 数字一致性：原文每个数（按 si）都要在腾讯译文里找到匹配；谷歌译文作为对照。
// 单位数字只比 si（防「18亿」靠裸 18 冒充 18 crore）；无单位数字比数值本身（≤10 时也接受汉字数字）。
export function numVerdict(en, zh, g) {
  const E = parseNums(en), T = parseZhNums(zh), G = parseZhNums(g || "");
  if (!E.length) return "ok";
  const zhText = norm(zh);
  const hit = (e, arr) =>
    arr.some((t) => (e.unit && t.unit ? close(e.si, t.si) : !e.unit && !t.unit ? close(e.n, t.n) : false)) ||
    // 小数字被译成汉字（top 4 → 四大、Q3 → 第三季度）
    (!e.unit && e.n >= 1 && e.n <= 10 && new RegExp(CN_SMALL_INV[e.n]).test(zhText));
  const hitT = E.filter((e) => hit(e, T)).length;
  const hitG = E.filter((e) => hit(e, G)).length;
  if (hitT === E.length) return "ok";
  if (hitT === 0 && hitG >= Math.ceil(E.length * 0.5)) return "high"; // 腾讯全丢、谷歌大半在
  if (hitT / E.length < 0.6) return "mid";
  return "low";
}
const CN_SMALL_INV = Object.fromEntries(Object.entries(CN_SMALL).map(([k, v]) => [v, k]));

// 谷歌翻译（带缓存 + 429 退避；失败返回 null，绝不抛）
let cache = null, cacheDirty = false;
function loadCache() {
  if (cache) return cache;
  try { cache = JSON.parse(fs.readFileSync(CACHE, "utf8")); } catch { cache = {}; }
  return cache;
}
// 2026-11-15 同 translate.mjs：写前重读磁盘做增量合并，临时名带 pid 防进程互相踩
function saveCache() {
  if (!cacheDirty || !cache) return;
  try {
    fs.mkdirSync(path.dirname(CACHE), { recursive: true });
    let disk = {};
    try { disk = JSON.parse(fs.readFileSync(CACHE, "utf8")) || {}; } catch {}
    const merged = Object.assign({}, disk, cache);
    const tmp = `${CACHE}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(merged), "utf8");
    fs.renameSync(tmp, CACHE);
    cacheDirty = false;
  } catch {}
}

async function googleOnce(text) {
  const r = await fetch(GURL + encodeURIComponent(String(text).slice(0, 3600)), {
    headers: { "User-Agent": UA }, signal: AbortSignal.timeout(15000),
  });
  if (r.status === 429) { const e = new Error("429"); e.retry = true; throw e; }
  if (!r.ok) throw new Error("HTTP " + r.status);
  const j = await r.json();
  const t = Array.isArray(j) ? (typeof j[0] === "string" ? j[0] : (j[0] || []).join("")) : null;
  if (!t) throw new Error("empty");
  return t;
}

async function googleT(text) {
  const c = loadCache();
  const k = "google:" + String(text);
  if (typeof c[k] === "string" && c[k]) return c[k];
  for (let i = 0; i < 3; i++) {
    try {
      const t = await googleOnce(text);
      c[k] = t; cacheDirty = true;
      return t;
    } catch (e) {
      if (i === 2) return null;
      await sleep(e && e.retry ? 2500 * (i + 1) : 600);
    }
  }
  return null;
}

// 彩云小译（默认第二通道）：整批提交，40 条/批；人名会音译但不影响数字比对
async function fillCaiyun(pairs, ctx) {
  const c = loadCache();
  const todo = [];
  for (const p of pairs) {
    if (p.g) continue;                       // 链式补漏：已有译文的对不再重复提交
    const k = "caiyun:" + p.en;
    if (typeof c[k] === "string" && c[k]) p.g = c[k];
    else todo.push(p);
  }
  let batchFails = 0;
  for (let i = 0; i < todo.length; i += CAIYUN_BATCH) {
    if (Date.now() > ctx.deadline) { ctx.limited = true; break; }
    const chunk = todo.slice(i, i + CAIYUN_BATCH);
    try {
      const r = await fetch(CAIYUN_URL, {
        method: "POST",
        headers: { "User-Agent": UA, "Content-Type": "application/json", "x-authorization": CAIYUN_TOKEN },
        body: JSON.stringify({
          source: chunk.map((p) => String(p.en).slice(0, 3000)),
          trans_type: "en2zh", request_id: "dsh-" + Date.now(), detect: true, media: "text",
        }),
        signal: AbortSignal.timeout(20000),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      if (j.rc !== 0 || !Array.isArray(j.target)) throw new Error("rc " + (j.rc ?? "?"));
      let got = 0;
      chunk.forEach((p, k) => {
        const t = j.target[k];
        if (t) { p.g = t; c["caiyun:" + p.en] = t; cacheDirty = true; got++; }
      });
      // 整批空响应（rc=0 但 target 全空）等同失败，否则会一路把 20 批都请求完却没拿到任何译文
      if (!got) throw new Error("空响应");
      batchFails = 0;
    } catch (e) {
      ctx.failed += chunk.length;
      if (++batchFails >= 3) { ctx.limited = true; break; } // 连续 3 批失败=通道不可用
      await sleep(800 * batchFails);
    }
  }
}

// 谷歌 clients5（备选）：逐条并发，连续失败熔断
async function fillGoogle(pairs, ctx) {
  const queue = pairs.filter((p) => !p.g);
  const workers = Array.from({ length: ctx.conc }, async () => {
    while (queue.length && !ctx.limited) {
      if (Date.now() > ctx.deadline) { ctx.limited = true; break; }
      const p = queue.shift();
      p.g = await googleT(p.en);
      if (!p.g) {
        ctx.failed++;
        if (++ctx.consec >= ctx.failBudget) ctx.limited = true;
      } else ctx.consec = 0;
      await sleep(120);
    }
  });
  await Promise.all(workers);
}

// MyMemory 的 de 邮箱：环境变量优先，其次读 ~/.dsh-ai-research/mm-email（600 权限，不进补丁）
function mmEmail() {
  if (process.env.DSH_AI_RESEARCH_MM_EMAIL) return process.env.DSH_AI_RESEARCH_MM_EMAIL.trim();
  try {
    return fs.readFileSync(path.join(os.homedir(), ".dsh-ai-research", "mm-email"), "utf8").trim();
  } catch { return ""; }
}

// MyMemory（抽样通道）：单条请求约 2s，按固定顺序取前 sample 条，配额内使用
async function fillMyMemory(pairs, ctx) {
  const c = loadCache();
  const todo = [];
  for (const p of pairs) {
    if (p.g) continue;                       // 链式补漏：彩云已拿到的对不再消耗 MyMemory 额度
    const k = "mm:" + p.en;
    if (typeof c[k] === "string" && c[k]) p.g = c[k];
    else todo.push(p);
  }
  const sample = todo.slice(0, ctx.mmSample || ctx.sample || 60);
  const de = mmEmail();
  ctx.de = de;
  // MyMemory 逐条约 2s，串行跑 400 条要十几分钟；开 3 路并发 + 每路 250ms 节流，
  // 实测不触发限流，400 条约 4~5 分钟。
  const workers = Math.min(ctx.mmWorkers || 3, Math.max(1, sample.length));
  let cursor = 0;
  const one = async (p) => {
    try {
      const u = MMURL + "?q=" + encodeURIComponent(String(p.en).slice(0, 500)) + "&langpair=" + encodeURIComponent("en|zh-CN")
        + (de ? "&de=" + encodeURIComponent(de) : "");
      const r = await fetch(u, { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(15000) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      const t = j && j.responseData && j.responseData.translatedText;
      if (!t) throw new Error("empty");
      p.g = t; c["mm:" + p.en] = t; cacheDirty = true;
    } catch (e) {
      ctx.failed++;
      if (++ctx.consec >= ctx.failBudget) ctx.limited = true;
    }
    await sleep(250);
  };
  await Promise.all(Array.from({ length: workers }, async () => {
    while (!ctx.limited && Date.now() <= ctx.deadline) {
      const i = cursor++;
      if (i >= sample.length) break;
      await one(sample[i]);
    }
  }));
}

// 主入口：对 res 里全部已译条目交叉校验，返回 { checked, skipped, flagged:[{kind,idx,level,en,zh,g,reason}] }
// ── 本地质检（2026-11-15 加）：不依赖第二引擎，对每一对原文/译文都能跑。
// 阈值用 2026-11-14 的 636 对真实译文校准过：长度比与中文占比在正常译文上误报 26%/24%，
// 不能当指标（中文里专名多时占比天然低）；能用的只有下面两条——
//   ① 英文从句残留：连续 ≥5 个英文词且带虚词（专名堆叠不含 the/of/and，不会误报）
//   ② 否定词丢失：原文有否定、译文一个否定字都没有
const FUNC_WORD = /\b(the|of|and|to|in|for|with|that|this|is|are|was|were|will|would|from|by|on|as|at|it)\b/i;
const ENG_RUN = /(?:[A-Za-z][A-Za-z'’\-]+[ ,]+){4,}/g;
const NEG_EN = /\b(not|no|never|denies|denied|refus\w*|without|fails? to|failed to|cannot|can't|n't|isn't|aren't|won't|doesn't|don't)\b/i;
const NEG_ZH = /[不没未无否拒禁非]/;
// 站点样板句（Google News / 聚合站尾巴）：先把它们从原文剥掉再判断否定，
// 否则「Audio playback is not supported on your browser」这类样板会被当成主句否定而误报。
const BOILER_EN = /(?:audio playback is not supported on your browser[^.]*\.?|please upgrade[^.]*\.?|(?:this|the) (?:article|post) [^.]{0,60}appeared first on [^.]*\.?|subscribe (?:now|to|for)[^.]*\.?|read more(?:\s*→)?|click here[^.]*\.?|share this(?: article)?|sign up for[^.]*\.?)/gi;
const stripBoiler = (t) => String(t || "").replace(BOILER_EN, " ");

export function qualityCheck(en, zh) {
  const s = String(zh || "");
  if (!s.trim()) return null;
  ENG_RUN.lastIndex = 0;
  const runs = s.match(ENG_RUN) || [];
  if (runs.some((seg) => FUNC_WORD.test(seg))) return { level: "mid", reason: "英文从句未翻译（疑似站点样板或整句漏译）" };
  if (NEG_EN.test(stripBoiler(en)) && !NEG_ZH.test(s)) return { level: "mid", reason: "原文是否定句，译文没有否定词（疑似翻反）" };
  // 时间习语反转（2026-11-15 抽查实抓：AI Takeover Could Be Months Away 被两个引擎都译成「几个月后/需要数月」，
  // 而 away 在新闻语境=「距离现在只剩」；窄规则只盯 「be + 时长 + away」句式）：
  if (/\b(?:months?|weeks?|days?|years?)\s+away\b/i.test(en) && !/(月|周|天|年)内|就会|即将|临近|只剩|不到|还有|要过/.test(s))
    return { level: "mid", reason: "「be X away」时间习语：away=只剩X就要发生，译文疑似译成「X后/需要X时间」（方向反了）" };
  return null;
}

const ENGINE_ZH = {
  caiyun: "彩云小译（失败项自动回退 MyMemory）",
  "caiyun+mymemory": "彩云小译（失败项自动回退 MyMemory）",
  google: "Google clients5",
  mymemory: "MyMemory",
};

export async function crossCheck(res, opts = {}) {
  const engine = String(opts.engine || "caiyun").toLowerCase();
  const conc = opts.concurrency || 3;
  const failBudget = opts.failBudget ?? 8;
  const deadline = Date.now() + (opts.budgetMs ?? 180000); // 墙钟上限：超时即停止，不让质检拖死管线
  const pairs = [];
  (res.items || []).forEach((it, i) => {
    if (it.title_zh && it.title) pairs.push({ kind: "title", idx: i, en: it.title, zh: it.title_zh });
    if (it.summary_zh && it.summary) pairs.push({ kind: "summary", idx: i, en: it.summary, zh: it.summary_zh });
  });
  for (const k of ["hn", "reddit", "x", "weixin"]) {
    ((res.buzz && res.buzz[k]) || []).forEach((b, i) => {
      const en = b.title || b.name || b.text || "";
      if (b.title_zh && en) pairs.push({ kind: "buzz-" + k, idx: i, en, zh: b.title_zh });
    });
  }
  const ctx = { conc, failBudget, failed: 0, consec: 0, limited: false, deadline };
  ctx.sample = opts.sample || 60;
  ctx.mmSample = opts.mmSample || 40;        // 备份通道抽样上限：40 条约 3–4k 字符，远小于 5 万/天额度
  // 引擎链（2026-11-15 用户定）：彩云是主通道，MyMemory 作**自动备份**——
  // 彩云整批失败/熔断时，没拿到译文的对自动回退 MyMemory 逐条翻（额度敏感，抽样上限 mmSample）。
  // 彩云正常时全部对都有译文，备份通道 0 请求。
  const chain = engine === "google" ? ["google"]
    : engine === "mymemory" ? ["mymemory"]
    : engine === "caiyun" ? ["caiyun", "mymemory"]
    : [engine];
  for (const eng of chain) {
    if (eng === "google") await fillGoogle(pairs, ctx);
    else if (eng === "mymemory") await fillMyMemory(pairs, ctx);
    else await fillCaiyun(pairs, ctx);
    if (pairs.every((p) => p.g)) break;      // 全齐则不必再走备份
  }
  saveCache();
  // 失败数按「最终仍没拿到译文的对」算：彩云失败但 MyMemory 补上了，就不该报失败
  const failures = pairs.filter((p) => !p.g).length, limited = ctx.limited;

  const flagged = [];
  let checked = 0, skipped = 0;
  // 本地质检先过一遍：不依赖引擎，因此连第二引擎没覆盖的对也能查
  for (const p of pairs) {
    const q = qualityCheck(p.en, p.zh);
    if (q) flagged.push({ ...p, kind: p.kind, level: q.level, reason: q.reason });
  }
  for (const p of pairs) {
    if (!p.g) { skipped++; continue; }
    checked++;
    const v = numVerdict(p.en, p.zh, p.g);
    if (v === "ok") continue;
    const ratio = p.zh.length / Math.max(p.g.length, 1);
    const shorty = ratio < 0.45 && p.en.length > 60;
    if (v === "high" || shorty)
      flagged.push({ ...p, level: "high", reason: shorty && v !== "high" ? "疑似漏译（长度比 " + ratio.toFixed(2) + "）" : "数字/单位与原文对不上" });
    else if (v === "mid") flagged.push({ ...p, level: "mid", reason: "部分数字对不上（可能换算差异，请人工判）" });
  }
  // —— 第三票裁决（2026-11-15 用户定「3 个翻译器怎么处理」优化）：被 flag 的条目自动补跑
  // MyMemory 当第三票（腾讯✗彩云✗时 mm 实测能翻对 away 类习语），三票并排进人工审清单。
  // 数字类分歧不送（mm 数字不可靠，$13 billion→30 亿美元，跑了也白跑）；失败静默跳过。
  const thirdVote = [];
  const mmBatch = [];
  for (const f of flagged) {
    if (/\d/.test(f.reason) || /数字|单位/.test(f.reason)) continue;
    mmBatch.push({ en: f.en });
    thirdVote.push(f);
  }
  if (mmBatch.length) {
    const ctx2 = { ...ctx, mmSample: mmBatch.length, mmWorkers: 3, deadline: Date.now() + 60000 };
    try {
      await fillMyMemory(mmBatch, ctx2);
      thirdVote.forEach((f, i) => { if (mmBatch[i] && mmBatch[i].g) f.mm = mmBatch[i].g; });
    } catch { /* 第三票尽力而为 */ }
  }
  return { checked, skipped, failed: failures, limited, engine: chain.join("+"), de: ctx.de || null, flagged };
}

// —— 兜底行补译（2026-11-15 用户定：zh.mjs 按「占位符对账不齐→保留英文」兜底的行，
//     优先彩云、失败回退 MyMemory，走本文件既有引擎链；URL 先占位防被引擎改坏）——
const FILL_URL_RE = /https?:\/\/[^\s)\]）」”'"，。；、！？]+/gi;
export async function refillFallback(srcLines, zhLines, opts = {}) {
  const pairs = [];
  const urlMaps = new Map();
  for (let i = 0; i < srcLines.length; i++) {
    const en = String(srcLines[i] ?? ""), z = String(zhLines[i] ?? "");
    if (!en.trim() || z !== en) continue;                    // 只补「译文==原文」的兜底行
    if (!/[A-Za-z]/.test(en) || /[\u4e00-\u9fff]/.test(z)) continue;
    const head = en.trim()[0] || "";
    if ("#!>|`-".includes(head)) continue;                   // 标题/图片/引用/围栏/表格行不补
    const urls = en.match(FILL_URL_RE) || [];
    let work = en;
    urls.forEach((u, k) => { work = work.split(u).join(`⟦U${k}⟧`); });
    urlMaps.set(i, urls);
    pairs.push({ kind: "refill", idx: i, en: work });
  }
  if (!pairs.length) return { out: zhLines.slice(), filled: 0, skippedLink: 0, failed: 0 };
  const ctx = { conc: opts.concurrency || 3, failBudget: opts.failBudget ?? 8, failed: 0,
    consec: 0, limited: false, deadline: Date.now() + (opts.budgetMs ?? 120000) };
  ctx.sample = 999;                                          // 补译不抽样，全量走彩云
  ctx.mmSample = opts.mmSample ?? 60;                        // 回退 MyMemory 时才受抽样限制
  for (const eng of ["caiyun", "mymemory"]) {                // 优先彩云，失败回退 MyMemory
    if (eng === "caiyun") await fillCaiyun(pairs, ctx);
    else await fillMyMemory(pairs, ctx);
    if (pairs.every((p) => p.g)) break;
  }
  const out = zhLines.slice();
  let filled = 0, failed = 0, skippedLink = 0;
  for (const p of pairs) {
    const i = p.idx, urls = urlMaps.get(i) || [];
    let t = typeof p.g === "string" ? p.g : "";
    if (t) {                                                 // 还原 URL 占位
      urls.forEach((u, k) => { t = t.split(`⟦U${k}⟧`).join(u); });
      const bad = urls.some((u) => !t.includes(u));          // 引擎改坏了 URL → 弃译文保原文
      const okZh = /[\u4e00-\u9fff]/.test(t) || !/[\u4e00-\u9fff]/.test(zhLines[i]);
      if (bad || !okZh) { t = ""; if (bad) skippedLink++; }
    }
    if (t && t.trim() && t !== srcLines[i]) { out[i] = t; filled++; }
    else { out[i] = zhLines[i]; failed++; }
  }
  return { out, filled, skippedLink, failed };
}

// 人工审清单落盘：<dir>/<stamp>_人工审.md
export function writeReviewList(dir, stamp, flagged, meta = {}) {
  if (!flagged.length) return null;
  const outDir = dir || "/root/workspace/digests";
  try { fs.mkdirSync(outDir, { recursive: true }); } catch {}
  const p = path.join(outDir, stamp + "_人工审.md");
  const lines = [
    "# AI 新闻雷达 人工审清单（" + stamp + "）",
    "",
    "> 腾讯机翻 vs 第二通道（" + (ENGINE_ZH[meta.engine] || "彩云小译") + "）交叉校验 + 本地质检（英文残留/否定词/时间习语）筛出的差异条目，**终审归人工**：核对原文后直接改日报 md（主文件 + 速览），改完本清单可删。",
    "> 非数字类分歧已自动补 **MyMemory 第三票**（腾讯✗彩云✗时它常能翻对习语），三票对照着看；数字类分歧不送 MyMemory（其 TM 语料数字不可靠）。",
    meta.checked ? "> 校验范围：" + meta.checked + " 条已译文本（第二通道未返回 " + (meta.skipped || 0) + " 条，未参与比对）。" : "",
    "",
  ];
  const order = { high: 0, mid: 1 };
  flagged.sort((a, b) => order[a.level] - order[b.level]);
  flagged.forEach((f, i) => {
    lines.push("## " + (i + 1) + ". [" + f.level.toUpperCase() + "] " + f.kind + "#" + f.idx + " — " + f.reason);
    lines.push("- **原文**：" + f.en);
    lines.push("- **腾讯**：" + f.zh);
    lines.push(f.g ? "- **第二通道**：" + f.g : "- **第二通道**：—（本地质检，未走引擎）");
    if (f.mm) {
      // 三票一致性标记（2026-11-15）：MyMemory 站哪边，帮人工快速定夺
      const sameT = norm(f.mm) === norm(f.zh);
      const sameG = norm(f.mm) === norm(f.g);
      const tag = sameT && !sameG ? "（MyMemory 与腾讯一致 → 彩云疑似噪音）"
        : sameG && !sameT ? "（**MyMemory 与第二通道一致 → 腾讯疑似错译，建议采纳第二通道**）"
        : sameT && sameG ? "（三票一致 → 大概率误报，可放行）" : "（三票各异 → 以人工判断为准）";
      lines.push("- **MyMemory（第三票）**：" + f.mm + " " + tag);
    }
    lines.push("");
  });
  fs.writeFileSync(p, lines.filter((l) => l !== "").join("\n"), "utf-8");
  return p;
}