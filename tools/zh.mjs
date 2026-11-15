#!/usr/bin/env node
// 通用英译中 CLI —— 复用 dsh-ai-research 雷达的翻译通道（腾讯 Transmart + 专名保护 + 共享缓存）
//
// 用法：
//   node /root/workspace/tools/zh.mjs "OpenAI ships a new model"
//   node /root/workspace/tools/zh.mjs -f article.txt > article.zh.txt
//   cat lines.txt | node /root/workspace/tools/zh.mjs
//
// 行为：逐行翻译，空行原样保留，已有的中文行原样输出（不翻译）；翻译失败的行原样输出。
// 缓存：~/.dsh-ai-research/translate-cache.json（与雷达共用，重复文本零请求）。
//
// 2026-11-15 三项增强（AWS SageMaker 篇实测，数字错译 51→7 处且零数值错误，见 /root/workspace/tests/REPORT.md）：
//   ① 数字/型号 token 占位（NUMA…NUMZ）——价格、百分比、千分位、版本号、实例型号完全不过引擎
//   ② markdown 代码围栏（```）内整段跳过翻译
//   ③ 行数守恒——非空输入行必须得到非空译文，否则单行重翻兜底

import fs from "node:fs";

const MOD = "/root/workspace/tools/translate.mjs";
const GUARD = "/root/workspace/tools/name-guard.mjs";
const { NAME_MAP, NAME_RE, swapNames, restoreNames, lostPlaceholder } = await import(GUARD);
const { translateTexts } = await import(MOD);

const args = process.argv.slice(2);
let text = "";
if (args[0] === "-f" || args[0] === "--file") {
  if (!args[1]) { console.error("用法：zh.mjs -f <文件>"); process.exit(2); }
  text = fs.readFileSync(args[1], "utf8");
} else if (args.length) {
  text = args.join(" ");
} else {
  text = fs.readFileSync(0, "utf8");
}

// 已知引擎怪癖：动词 falls 会被音译成「福尔斯」（引擎把它当 Falls 专名）。
// 归一化成 declines 再翻，语义不变；"Niagara Falls" 这类专名（前一词大写开头）不动。
text = text.replace(/(?<![A-Z][a-z]+\s)\bfalls\b/g, "declines");

// 同类怪癖：动词 ships 会被音译成「船舶」（引擎当船的复数）。
// 科技语境里 ships 几乎总是动词（发布/推出），归一化成 releases；名词复数用法罕见，可接受。
text = text.replace(/\bships\b/g, "releases");

// 2026-11-13 批量实测（20 个高频新闻动词）后追加，都是低风险高收益项：
// teases 会被译成「挑逗」（科技语境=预告/预热），归一化成 previews；
// 缩写 exec 引擎不认识会原样保留（"former Amazon exec"），展开成 executive 就能译出「高管」。
text = text.replace(/\bteases\b/g, "previews");
text = text.replace(/\bexec\b/g, "executive");

// 2026-11-13 用户修例（法律与技术语境）：
// claim 在诉讼语境被引擎译成「索赔」（保险义），归一化成 alleges（主张/指控）；
// human action simulation 术语组合按通行译法改写成 behavior（人的行为模拟）。
text = text.replace(/\b(lawsuit|suit|complaint|case) claims\b/gi, "$1 alleges");
text = text.replace(/\bclaims\b(?=\s+(that|it|the|its)\b)/gi, "alleges");
text = text.replace(/\bhuman action simulation\b/gi, "human behavior simulation");

const lines = text.replace(/\r\n/g, "\n").split("\n");
const URL_RE = /https?:\/\/[^\s)\]）」”'"，。；、！？]+/gi;
// 数字 token：任何含数字的连续片段（$178.67、4.5%、1,205、ml.g5.8xlarge、GPT-4、2026-11-15、B2B 全部命中）
const NUM_RE = /[\w$€£¥%.,+-]*\d[\w$€£¥%.,+-]*/g;
// 量级词整组：$13 billion / 44.1 million / 2.3 trillion —— 数字+量级词必须一起占位（SCALE{X}），
// 因为引擎对量级词系统性错译且换措辞救不了（2026-11-15 实测：$13 billion→「10亿美元」、
// 13 billion dollars→「13亿美元」错 100 倍、40 billion dollars→「40 10亿美元」崩坏）。
// 翻后按 SI 确定性换算还原成中文量级（billion→N*10 亿、million→N*100 万、trillion→N 万亿）。
const SCALE_RE = /([$€£¥]?)(\d[\d,.]*)\s*(trillion|billion|million)\b(?:\s+(dollars?|euros?|pounds?|yen|yuan)\b)?/gi;
const SCALE_CUR = { $: "美元", "€": "欧元", "£": "英镑", "¥": "元" };
const SCALE_WORD = { dollar: "美元", dollars: "美元", euro: "欧元", euros: "欧元", pound: "英镑", pounds: "英镑", yen: "日元", yuan: "元" };
const scaleRestore = (cur, num, unit, word) => {
  const n = parseFloat(String(num).replace(/,/g, ""));
  if (!isFinite(n)) return null;
  let zhNum, unitZh = "";
  if (/^t/i.test(unit)) { zhNum = String(n); unitZh = "万亿"; }
  else if (/^b/i.test(unit)) { zhNum = String(n * 10); unitZh = "亿"; }
  else { zhNum = String(n * 100); unitZh = "万"; }
  if (zhNum.includes(".")) zhNum = zhNum.replace(/\.?0+$/, "");
  const curZh = cur ? SCALE_CUR[cur] || "" : (word ? SCALE_WORD[String(word).toLowerCase()] || "" : "");
  return `${zhNum}${unitZh}${curZh}`;
};

// 1) URL/数字/人名 → 占位符（按行独立编号；PERSON[A-Z] 给 URL/人名，NUM[A-Z] 给数字）
// URL 必须占位：实测腾讯会把 markdown 链接里的 https:// 全角化成 https：//（2026-11-13）；
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
  if (!l.trim() || /[\u4e00-\u9fff]/.test(l)) { guarded.push(l); bags.push(null); continue; }

  const bag = { __num: {}, __scale: {} };
  let i = 0, ni = 0;
  let g = l.replace(URL_RE, (m) => {
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
  guarded.push(g); bags.push(bag);
}

// 2) 整批翻译
const zh = await translateTexts(guarded, { verbose: !!process.env.ZH_VERBOSE });

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
  const needS = Object.keys(bag.__scale || {});
  if (!need.length && !needS.length) return true;
  const got = new Set((String(t).match(/NUM([A-Za-z])/g) || []).map((m) => m.slice(3).toUpperCase()));
  const gotS = new Set((String(t).match(/SCALE([A-Za-z])/g) || []).map((m) => m.slice(5).toUpperCase()));
  return need.every((c) => got.has(c)) && needS.every((c) => gotS.has(c));
};
for (let i = 0; i < guarded.length; i++) {
  if (fenceFlag[i] || !guarded[i].trim()) continue;
  let bad = zh[i] == null || !String(zh[i]).trim();
  if (!bad && bags[i]) bad = lostPlaceholder(String(zh[i]), bags[i]) || /(NUM[A-Za-z]|SCALE[A-Za-z])/.test(String(zh[i])) || !numOk(String(zh[i]), bags[i]);
  if (bad) {
    // 重翻输入加零宽空格扰动：单行 key 与批处理相同，会命中批处理写入的坏缓存，扰动后强制真请求。
    // 引擎对占位行有抽风（同一行两次请求结果不同，实测一次保留 NUMA 一次吞掉），扰动试 2 次提高恢复率。
    let oneT = null;
    for (const z of ["\u200b", "\u200b\u200b"]) {
      const one = await translateTexts([guarded[i] + z]);
      oneT = one && one[0] ? String(one[0]).replace(/\u200b/g, "") : null;
      if (oneT && oneT.trim() && !lostPlaceholder(oneT, bags[i]) && !/(NUM[A-Za-z]|SCALE[A-Za-z])/.test(oneT) && numOk(oneT, bags[i])) break;
      oneT = null;
    }
    if (oneT) {
      zh[i] = oneT;
    } else {
      zh[i] = null;   // 单行重翻也保不全 → 输出段按原文兜底
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
// SCALE 占位还原：按 SI 确定性换算成中文量级（$13 billion→130亿美元、44.1 million→4410万）
const restoreScale = (t, bag) => t.replace(/SCALE([A-Za-z])/g, (m, c) => {
  const v = (bag.__scale || {})[c.toUpperCase()];
  if (!v) return m;
  return scaleRestore(v[0], v[1], v[2]) ?? m;   // 解析失败原样还原原文片段
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
const out = [];
for (let i = 0; i < lines.length; i++) {
  const src = lines[i];
  if (fenceFlag[i]) { out.push(src); continue; }
  if (!src.trim() || /[\u4e00-\u9fff]/.test(src)) { out.push(swapNames(src)); continue; }
  let t = zh[i] == null ? null : String(zh[i]);
  if (t != null) {
    t = fixParens(restoreNames(swapNames(restoreNum(restoreScale(t, bags[i]), bags[i])), bags[i]), bags[i]);   // 先 SCALE 后 NUM 再 swapNames（避免 URL 里的 gates 等子串被换）
    if (t != null && !scaleOk(t, bags[i])) t = null;   // 量级换算值缺失（占位补位损坏）→ 走下方重翻兜底
  }
  if (t == null || /PERSON[A-Z]/i.test(t) || /(NUM[A-Za-z]|SCALE[A-Za-z])/.test(t) || !numOk(zh[i] ?? "", bags[i])) {
    const again = await translateTexts([guarded[i] + "\u200b"]);   // 零宽扰动绕过坏缓存（同上）
    const againT = again && again[0] ? String(again[0]).replace(/\u200b/g, "") : null;
    if (againT && numOk(againT, bags[i])) {
      t = fixParens(restoreNames(swapNames(restoreNum(restoreScale(againT, bags[i]), bags[i])), bags[i]), bags[i]);
      if (/(NUM[A-Za-z]|SCALE[A-Za-z])/.test(t) || !scaleOk(t, bags[i])) t = src;   // 终极兜底：数字还原失败或量级值缺失的行保留原文
    } else t = src;
  }
  out.push(t);
}
process.stdout.write(out.join("\n"));
