// 把镜像整理成可部署目录：node pack.mjs
// 排除 *-og-image.*（仅社交分享缩略图，约 47MB，排除后 ZIP 才能低于 50MiB 上限）
import { cpSync, readdirSync, statSync, rmSync } from "node:fs";
import path from "node:path";

const SRC = path.resolve("site");
const DST = path.resolve("dist");
rmSync(DST, { recursive: true, force: true });

cpSync(SRC, DST, {
  recursive: true,
  filter: (src) => !path.basename(src).includes("-og-image."),
});

let files = 0;
let bytes = 0;
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p);
    else { files++; bytes += st.size; }
  }
};
walk(DST);
console.log(`dist: ${files} files, ${(bytes / 1048576).toFixed(1)} MB raw`);
