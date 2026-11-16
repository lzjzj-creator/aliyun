// 把重抓的原始 HTML 覆盖回镜像目录（资源文件沿用已有镜像，不重复下载）
// 用法：node merge-html.mjs
import { readdirSync, statSync, copyFileSync } from "node:fs";
import path from "node:path";

const SRC = path.resolve("site-html");
const DST = path.resolve("site");
let copied = 0;

const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) { walk(full); continue; }
    if (!name.endsWith(".html")) continue;
    copyFileSync(full, path.join(DST, path.relative(SRC, full)));
    copied++;
  }
};
walk(SRC);
console.log(`DONE copied=${copied}`);
