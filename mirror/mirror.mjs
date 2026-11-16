// 知识库整站镜像：node mirror.mjs（全量） / node mirror.mjs --probe（浅层试跑）
// 依赖：npm i website-scraper（GitHub: website-scraper/node-website-scraper）
// 输出到 mirror/site/，已 gitignore；重跑前需删除该目录（工具要求空目录）
// 本地预览：python -m http.server 4310 --directory mirror/site
import scrape from "website-scraper";
import sanitizeFilename from "sanitize-filename";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const BASE = process.env.MIRROR_SOURCE || "https://zhimai-rag-knowledge.pages.dev";
const OUT = path.resolve(process.env.MIRROR_OUT || "site");
const PROBE = process.argv.includes("--probe");
// 只抓 HTML（不抓 CSS/JS/图片），用于重新获得未被改写的原始页面
const HTML_ONLY = process.argv.includes("--html-only");
const MAX_DEPTH = Number(process.env.MIRROR_DEPTH || 10);

// 保留原始 URL 路径（含中文目录名），否则后端按 slug 拼出的来源链接会 404
const keepUrlPaths = {
  apply(registerAction) {
    registerAction("generateFilename", ({ resource }) => {
      const u = new URL(resource.getUrl());
      let pathname;
      try { pathname = decodeURIComponent(u.pathname); } catch { pathname = u.pathname; }
      if (!/\.[a-zA-Z0-9]{1,8}$/.test(pathname)) {
        pathname = pathname.replace(/\/?$/, "/") + "index.html";
      }
      if (u.search) {
        const h = crypto.createHash("md5").update(u.search).digest("hex").slice(0, 8);
        pathname = pathname.replace(/(\.[a-zA-Z0-9]{1,8})?$/, `_${h}$1`);
      }
      const parts = pathname.split("/").filter(Boolean)
        .map((p) => sanitizeFilename(p, { replacement: "_" }));
      return { filename: parts.join(path.sep) };
    });
  },
};

// 中文 URL 直接放进 referer 会被 Node 拒绝（header 只允许 latin1），在 got 发出前编码
const encodeReferer = {
  apply(registerAction) {
    registerAction("beforeRequest", ({ requestOptions }) => ({
      requestOptions: {
        ...requestOptions,
        hooks: {
          beforeRequest: [
            (options) => {
              const ref = options.headers?.referer;
              if (ref && /[^\x00-\xff]/.test(ref)) options.headers.referer = encodeURI(ref);
            },
          ],
        },
      },
    }));
  },
};

let saved = 0;
let failed = 0;
const errors = [];
const progress = {
  apply(registerAction) {
    registerAction("onResourceSaved", ({ resource }) => {
      saved++;
      if (saved % 200 === 0) console.log(`saved=${saved} failed=${failed} latest=${resource.url.slice(0, 110)}`);
    });
    registerAction("onResourceError", ({ resource, error }) => {
      failed++;
      errors.push({ url: resource.url, error: String(error).slice(0, 200) });
      if (failed <= 15) console.log(`ERROR ${resource.url} :: ${String(error).slice(0, 120)}`);
    });
  },
};

const started = Date.now();
const result = await scrape({
  urls: [
    { url: `${BASE}/`, filename: "index.html" },
    { url: `${BASE}/static/contentIndex.json`, filename: "static/contentIndex.json" },
  ],
  directory: OUT,
  ...(HTML_ONLY ? { sources: [] } : {}),
  recursive: true,
  maxRecursiveDepth: PROBE ? 2 : MAX_DEPTH,
  requestConcurrency: 6,
  subdirectories: [],
  ignoreErrors: true,
  urlFilter: (url) => url.startsWith(BASE),
  plugins: [keepUrlPaths, encodeReferer, progress],
});

writeFileSync(PROBE ? "probe-report.json" : "mirror-report.json", JSON.stringify({
  source: BASE, saved, failed, seconds: Math.round((Date.now() - started) / 1000),
  resources: result.length, errors: errors.slice(0, 200),
}, null, 2));
console.log(`DONE saved=${saved} failed=${failed} resources=${result.length} secs=${Math.round((Date.now() - started) / 1000)}`);
