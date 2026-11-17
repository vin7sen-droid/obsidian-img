// 评论/正文译文交叉校验桥（2026-11-17）：读 {en,zh,name}[] → xcheck.crossCheck → 人工审清单
import fs from "fs";
import { crossCheck, writeReviewList } from "/root/workspace/tools/xcheck.mjs";

const file = process.argv[2];
const pairs = JSON.parse(fs.readFileSync(file, "utf8"));
const res = { items: pairs.map((p, i) => ({ title: p.en, title_zh: p.zh, url: p.name || ("#" + i) })) };
const out = await crossCheck(res, { sample: 999, mmSample: 40 });
const stamp = new Date().toISOString().slice(0, 10);
const p = writeReviewList("/root/workspace/digests", stamp + "_笔记译文", out.flagged || [], {
  checked: out.checked, skipped: out.skipped, engine: out.engine,
});
console.log(`校验 ${pairs.length} 行 → flagged ${(out.flagged || []).length} 条` + (p ? `，清单：${p}` : "，无需人工审"));
