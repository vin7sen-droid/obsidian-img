#!/usr/bin/env node
// 通用英译中 CLI —— MyMemory 通道（与雷达 xcheck 共用同一份 de 邮箱与使用约定）
//
// 用法：
//   node /root/workspace/tools/mm.mjs "OpenAI ships a new model"
//   node /root/workspace/tools/mm.mjs -f article.txt > article.zh.txt
//   cat lines.txt | node /root/workspace/tools/mm.mjs
//   node /root/workspace/tools/mm.mjs --refresh "Anthropic raised $13 billion"   # 删缓存重翻（TM 错译不自愈）
//   node /root/workspace/tools/mm.mjs --no-cache -f test.txt                     # 全量真请求（变体重测用）
//
// 2026-11-15 实测定性（用户「照你改」）：MyMemory 是翻译记忆引擎——短句+常见搭配会命中语料库
// 旧错译文、数字被硬套（实例 "raised $13 billion"→「30 亿美元」，改写措辞即对）。三条纪律：
//   ① 抽样遇可疑数字，改写措辞重测再下结论（--refresh / --no-cache 就是为这个加的）
//   ② 短句译文只看语义，数字一律以 numcheck.py 机检为准
//   ③ 缓存错译不会自愈，重测必须 --refresh（或手动删 mm-cache.json）
// 行为：逐行翻译，空行原样保留，已有中文的行原样输出；失败的行原样输出。
// 额度：匿名 5000 字符/天，带 de=<邮箱> 50000 字符/天（按请求字符计）。
//       邮箱读 ~/.dsh-ai-research/mm-email（600），环境变量 DSH_AI_RESEARCH_MM_EMAIL 可覆盖。
// 缓存：~/.dsh-ai-research/mm-cache.json —— 重复文本零请求，省额度（改前先备份不清缓存）。
// 定位：慢（约 2s/条）但质量高，适合抽样核对；长文批量翻请用 zh.mjs（腾讯通道）。

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const API = "https://api.mymemory.translated.net/get";
const CACHE_FILE = path.join(os.homedir(), ".dsh-ai-research", "mm-cache.json");
const EMAIL_FILE = path.join(os.homedir(), ".dsh-ai-research", "mm-email");

const args = process.argv.slice(2);
const workersArg = args.indexOf("--workers");
const workers = workersArg >= 0 ? Math.max(1, Math.min(6, Number(args[workersArg + 1])) || 3) : 3;
const refreshArg = args.indexOf("--refresh");
const noCache = args.includes("--no-cache");
const rest = args.filter((a, i) =>
  !(workersArg >= 0 && (i === workersArg || i === workersArg + 1)) &&
  !(refreshArg >= 0 && (i === refreshArg || i === refreshArg + 1)) && a !== "--no-cache");

let text = "";
let fromArgv = false;
if (refreshArg >= 0) {
  // --refresh "text"：删该句缓存后单句强制重翻（TM 错译不会自愈，必须显式清）
  if (!args[refreshArg + 1]) { console.error("用法：mm.mjs --refresh \"<原文>\""); process.exit(2); }
  text = args[refreshArg + 1];
  fromArgv = true;
} else if (rest[0] === "-f" || rest[0] === "--file") {
  if (!rest[1]) { console.error("用法：mm.mjs -f <文件>"); process.exit(2); }
  text = fs.readFileSync(rest[1], "utf8");
} else if (rest.length) {
  text = rest.join(" ");
  fromArgv = true;
} else {
  text = fs.readFileSync(0, "utf8");
}

function mmEmail() {
  if (process.env.DSH_AI_RESEARCH_MM_EMAIL) return process.env.DSH_AI_RESEARCH_MM_EMAIL.trim();
  try { return fs.readFileSync(EMAIL_FILE, "utf8").trim(); } catch { return ""; }
}

const EMAIL = mmEmail();
if (!EMAIL) console.error("提示：未找到邮箱（~/.dsh-ai-research/mm-email），按匿名额度 5000 字符/天运行。");

let cache = {};
try { cache = JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")); } catch { cache = {}; }

const isChinese = (s) => /[\u4e00-\u9fff]/.test(s);
const slug = (s) => s.replace(/\s+/g, " ").trim();

if (refreshArg >= 0) { delete cache[slug(text)]; console.error("[mymemory] --refresh：已清除该句缓存，强制重翻"); }

let usedChars = 0;
let hitCache = 0;
let requested = 0;

async function translateOne(src, force = false) {
  const key = src;
  if (!force && !noCache && cache[key]) { hitCache++; return cache[key]; }
  const u = `${API}?q=${encodeURIComponent(src.slice(0, 500))}&langpair=${encodeURIComponent("en|zh-CN")}` +
    (EMAIL ? `&de=${encodeURIComponent(EMAIL)}` : "");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(u, { headers: { "User-Agent": "Mozilla/5.0" } });
      const d = await r.json();
      const out = d?.responseData?.translatedText;
      usedChars += src.length;
      requested++;
      if (typeof out === "string" && out.trim() && !/^MYMEMORY WARNING/i.test(out)) {
        cache[key] = out;
        return out;
      }
      throw new Error(d?.responseStatus ? `status ${d.responseStatus}` : "empty");
    } catch (e) {
      if (attempt === 2) return null;
      await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
    }
  }
  return null;
}

const lines = text.split(/\r?\n/);
const todo = [];
lines.forEach((l, i) => {
  const s = slug(l);
  if (!s || isChinese(s) || !/[A-Za-z]{2}/.test(s)) return;
  if (noCache || refreshArg >= 0 || !cache[s]) todo.push(i);   // --no-cache/--refresh 时全部视为待翻
});

const uniq = [...new Set(todo.map((i) => lines[i].trim()))];
let cursor = 0;
const fresh = new Set();
async function worker() {
  while (cursor < uniq.length) {
    const src = uniq[cursor++];
    const r = await translateOne(src, noCache || refreshArg >= 0);
    if (r != null) fresh.add(src);          // 本次真请求成功（区别于命中的旧缓存）
  }
}
await Promise.all(Array.from({ length: Math.min(workers, uniq.length || 1) }, worker));

const out = lines.map((l) => {
  const s = slug(l);
  if (!s || isChinese(s) || !/[A-Za-z]{2}/.test(s)) return l;
  // --refresh/--no-cache 模式只认本次新请求的结果，绝不再回退到旧缓存（那正是要重测的错译）
  if (noCache || refreshArg >= 0) return fresh.has(s) ? cache[s] : l;
  return cache[s] || l;
});

try {
  // 2026-11-15 并发安全：写前重读磁盘合并（内存优先），临时名带 pid。
  // MyMemory 额度稀缺，别的进程刚翻的条目不能被这次写回抹掉。
  fs.mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
  let disk = {};
  try { disk = JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) || {}; } catch {}
  const merged = Object.assign({}, disk, cache);
  const tmp = `${CACHE_FILE}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(merged));
  fs.renameSync(tmp, CACHE_FILE);
} catch (e) { console.error("缓存写入失败：", e.message); }

process.stdout.write(out.join("\n") + (text.endsWith("\n") || fromArgv ? "\n" : ""));
console.error(`[mymemory] 请求 ${requested} 条 / ${usedChars} 字符 · 缓存命中 ${hitCache} 条 · 累计缓存 ${Object.keys(cache).length} 条` +
  (EMAIL ? ` · de=${EMAIL}` : " · 匿名额度"));
if (noCache || refreshArg >= 0) {
  console.error("[mymemory] 提示：短句译文只看语义，数字一律以 numcheck.py 机检为准（TM 可能套错语料，实测 \"raised $13 billion\"→「30 亿美元」）");
}