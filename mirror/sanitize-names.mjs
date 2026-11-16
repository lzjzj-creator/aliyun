// 清理镜像里打包器拒绝的文件名字符（\p{Cc}/\p{Cf} 等），并同步修正文本中的引用
// 用法：node sanitize-names.mjs  （在 mirror 目录，作用于 site/，之后重新 pack）
import { readdirSync, lstatSync, renameSync, readFileSync, writeFileSync, statSync } from "node:fs";
import path from "node:path";

const ROOT = path.resolve(process.env.MIRROR_ROOT || "site");
const badName = new RegExp("[\\\\%?#:\\p{Cc}\\p{Cf}]", "u");
const strip = new RegExp("[\\p{Cc}\\p{Cf}]", "gu");
const renamed = [];

// 自底向上重命名，避免父目录先改名后子路径失效
const renameWalk = (dir) => {
  for (const n of readdirSync(dir)) {
    const full = path.join(dir, n);
    const st = lstatSync(full);
    if (st.isDirectory()) renameWalk(full);
    if (badName.test(n)) {
      const clean = n.replace(strip, "").replace(/[\\%?#:]/g, "_");
      if (!clean || clean === n) continue;
      renameSync(full, path.join(dir, clean));
      renamed.push({ from: n, to: clean });
      console.log("renamed:", n, "->", clean);
    }
  }
};
renameWalk(ROOT);

// 文本引用：去掉零宽/控制字符及其 URL 编码形式
let files = 0;
const textWalk = (dir) => {
  for (const n of readdirSync(dir)) {
    const full = path.join(dir, n);
    if (statSync(full).isDirectory()) { textWalk(full); continue; }
    if (!/\.(html|js|css|json)$/.test(n)) continue;
    const before = readFileSync(full, "utf8");
    let out = before.replace(strip, "");
    out = out.replace(/%(e2%80%8b|e2%80%8c|e2%80%8d|ef%bb%bf)/gi, "");
    if (out !== before) { writeFileSync(full, out); files++; }
  }
};
textWalk(ROOT);
console.log(`DONE renamed=${renamed.length} textFilesFixed=${files}`);
