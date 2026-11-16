// 补齐镜像缺口：Quartz 动态 import 的 /static/scripts/*.js 不会出现在 HTML 里，抓取会漏
// 用法：node fill-gaps.mjs（在 mirror 目录，镜像主流程跑完后执行）
import { readdirSync, statSync, readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const BASE = process.env.MIRROR_SOURCE || "https://zhimai-rag-knowledge.pages.dev";
const ROOT = path.resolve("site");
const refs = new Set();

const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) { walk(p); continue; }
    if (!/\.(html|js|css|json)$/.test(name)) continue;
    const text = readFileSync(p, "utf8");
    for (const m of text.matchAll(/["'(]\s*\/?\.?\/?(static\/[A-Za-z0-9_/.-]+)/g)) refs.add("/" + m[1]);
    for (const m of text.matchAll(/["'(]\/([A-Za-z0-9_/.-]+\.(?:js|css|png|webp|json|woff2?))["')]/g)) refs.add("/" + m[1]);
  }
};
walk(ROOT);

const missing = [...refs].filter((r) => !existsSync(path.join(ROOT, decodeURIComponent(r))));
console.log(`refs=${refs.size} missing=${missing.length}`);
mkdirSync(path.join(ROOT, "static", "scripts"), { recursive: true });

let ok = 0;
let failed = 0;
for (const rel of missing) {
  const url = BASE + rel.split("/").map(encodeURIComponent).join("/");
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    const dest = path.join(ROOT, decodeURIComponent(rel));
    mkdirSync(path.dirname(dest), { recursive: true });
    writeFileSync(dest, buf);
    ok++;
    console.log(`OK ${rel} (${buf.length}B)`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${rel} :: ${e.message}`);
  }
}
console.log(`DONE ok=${ok} failed=${failed}`);
