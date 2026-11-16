// 修正镜像内部引用（输入为 website-scraper 落盘的 HTML，可用 mirror.mjs --html-only 重抓）。
// 落盘 HTML 里有两类相对引用，基址不同：
//   <a href>       已被 scraper 改写成「相对页面所在磁盘目录」的本地路径（常带 /index.html）
//   CSS/JS/图片等  仍是原始站点语义，基址是原始页面 URL 的父目录（og:url 去掉最后一段）
// 因此对两类引用分别用不同基址解析，并以「目标文件真实存在」为准，两个基址都尝试。
// 同时移除指回原站的 canonical / og:url / og:image 元信息（镜像不托管社交预览图）。
// 用法：node merge-html.mjs && node absolutize.mjs
import { readdirSync, statSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";

const ROOT = path.resolve(process.env.MIRROR_ROOT || "site");
const SOURCE_HOST = new URL(process.env.MIRROR_SOURCE || "https://zhimai-rag-knowledge.pages.dev").host;
const encodePath = (p) => p.split("/").map(encodeURIComponent).join("/");

let pages = 0;
let rewritten = 0;
const unresolved = [];

const splitRef = (ref) => {
  const i = ref.search(/[?#]/);
  return { rawPath: i >= 0 ? ref.slice(0, i) : ref, suffix: i >= 0 ? ref.slice(i) : "" };
};

// 依次尝试各基址，返回第一个真实存在的目标；都不存在返回 null
const resolveRef = (ref, bases) => {
  const { rawPath, suffix } = splitRef(ref);
  let decoded;
  try { decoded = decodeURIComponent(rawPath); } catch { decoded = rawPath; }
  for (const base of bases) {
    let target = decoded.startsWith("/") ? decoded : path.posix.resolve(base, decoded);
    if (existsSync(path.join(ROOT, target, "index.html"))) {
      target = target.replace(/\/?$/, "/") + "index.html";
    } else if (!path.posix.extname(target) && existsSync(path.join(ROOT, target + ".html"))) {
      target += ".html";
    }
    if (existsSync(path.join(ROOT, target))) return encodePath(target) + suffix;
  }
  return null;
};

const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) { walk(full); continue; }
    if (!name.endsWith(".html")) continue;
    pages++;
    const html = readFileSync(full, "utf8");
    const relDir = path.relative(ROOT, dir).split(path.sep).join("/");
    const diskBase = "/" + relDir;
    const og = html.match(/<meta property="og:url" content="([^"]+)"/);
    let originalBase = path.posix.dirname(diskBase);
    if (og) {
      let p;
      try { p = decodeURIComponent(new URL(og[1]).pathname); }
      catch { p = new URL(og[1]).pathname; }
      originalBase = p.endsWith("/") ? p : path.posix.dirname(p);
    }
    const record = (ref, bases) => unresolved.push({
      page: path.relative(ROOT, full), ref, tried: splitRef(ref).rawPath,
    });
    const out = html
      .replace(/<link rel="canonical"[^>]*>\s*/g, "")
      .replace(/<meta (?:property="og:url"|property="og:image"|name="twitter:image")[^>]*>\s*/g, "")
      .replace(new RegExp(`<meta\\b[^>]*${SOURCE_HOST.replace(/\./g, "\\.")}[^>]*>\\s*`, "g"), "")
      // 第一遍：<a> 链接按磁盘目录解析（scraper 已本地化），原始基址兜底
      .replace(/(<a\b[^>]*?\shref=)(["'])((?:\.\.\/|\.\/|\/)[^"']*)\2/g,
        (m, pre, quote, ref) => {
          if (ref.startsWith("//")) return m;
          const next = resolveRef(ref, [diskBase, originalBase]);
          if (!next) { record(ref); return m; }
          if (pre + quote + next + quote !== m) rewritten++;
          return pre + quote + next + quote;
        })
      // 第二遍：其余引用按原始站点基址解析；已是根绝对的链接保持不变
      .replace(/(["'(])((?:\.\.\/|\.\/)+[^"'()\s<>]+|\/[^"'()\s<>][^"'()\s<>]*)/g,
        (m, quote, ref) => {
          if (ref.startsWith("//")) return m;
          const next = resolveRef(ref, [originalBase, diskBase]);
          if (!next) { record(ref); return m; }
          const out = quote + next;
          if (out !== m) rewritten++;
          return out;
        });
    if (out !== html) writeFileSync(full, out);
  }
};
walk(ROOT);
writeFileSync("absolutize-report.json", JSON.stringify({ pages, rewritten, unresolved }, null, 2));
console.log(`DONE pages=${pages} rewritten=${rewritten} unresolved=${unresolved.length}`);
for (const u of unresolved.slice(0, 15)) console.log("unresolved:", u.ref, "<-", u.page);
