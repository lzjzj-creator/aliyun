// 知识库镜像品牌替换 + 配色统一到知脉前端（teal #14525a / accent #e0643a / paper #faf7f2）
// 用法：node rebrand.mjs（在 mirror 目录执行，作用于 site/、site-html/、dist/ 三份副本）
// - 全库文本「小鼎」→「鑫森」（html/json/js/css/xml/txt）
// - index-*.css：Quartz 主题变量、--zhimai-* 变量与硬编码暗色/粉色改为前端配色
import { readdirSync, statSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const ROOTS = ["site", "site-html", "dist"];
const TEXT_EXT = new Set([".html", ".json", ".js", ".css", ".xml", ".txt", ".svg", ".md"]);

// ---------- 1) 文本替换 ----------
let renamed = 0;
const replaceInTree = (dir) => {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) { replaceInTree(p); continue; }
    if (!TEXT_EXT.has(path.extname(name))) continue;
    const text = readFileSync(p, "utf8");
    if (!text.includes("小鼎")) continue;
    writeFileSync(p, text.replaceAll("小鼎", "鑫森"));
    renamed++;
  }
};
for (const root of ROOTS) replaceInTree(path.resolve(root));
// 后端 ingest 用的源索引同步改名，保持一致
const idx = path.resolve("../data/contentIndex.json");
const idxText = readFileSync(idx, "utf8");
if (idxText.includes("小鼎")) { writeFileSync(idx, idxText.replaceAll("小鼎", "鑫森")); renamed++; }
console.log(`rebrand: 小鼎→鑫森 done in ${renamed} files`);

// ---------- 2) 主题配色 ----------
const PAIRS = [
  // 亮色主题变量 → 前端配色
  [":root{--light:#08080d;--lightgray:#2a171d;--gray:#8c747a;--darkgray:#d9cbd0;--dark:#fff7f8;--secondary:#ff4d67;--tertiary:#a78bfa;--highlight:#ff4d6724;--textHighlight:#b3aa0288",
   ":root{--light:#faf7f2;--lightgray:#e5ddd2;--gray:#7a6f63;--darkgray:#1f2a2e;--dark:#14525a;--secondary:#14525a;--tertiary:#e0643a;--highlight:#14525a14;--textHighlight:#e0643a40"],
  // 暗色主题变量 → 同品牌暗色变体（teal 提亮保对比度）
  [":root[saved-theme=dark]{--light:#08080d;--lightgray:#2a171d;--gray:#8c747a;--darkgray:#d9cbd0;--dark:#fff7f8;--secondary:#ff4d67;--tertiary:#a78bfa;--highlight:#ff4d6724;--textHighlight:#b3aa0288}",
   ":root[saved-theme=dark]{--light:#1b2224;--lightgray:#2e3a3b;--gray:#8a8f88;--darkgray:#e8e1d4;--dark:#f2ecdf;--secondary:#5fb3bd;--tertiary:#e07b52;--highlight:#5fb3bd24;--textHighlight:#e0643a66}"],
  // Obsidian 风格 accent HSL → teal
  ["--accent-h:351;--accent-s:100%;--accent-l:65%", "--accent-h:189;--accent-s:64%;--accent-l:22%"],
  // zhimai 自定义面板变量（亮色）
  [":root{--zhimai-accent:#d97757;--zhimai-accent-2:#d97757;--zhimai-panel:#1c1e1fdb;--zhimai-panel-strong:#222425f7;--zhimai-border:#d7beab2e;--zhimai-glow:#d977571c;--zhimai-shadow:0 18px 50px #0000002e}",
   ":root{--zhimai-accent:#e0643a;--zhimai-accent-2:#e0643a;--zhimai-panel:#fffffff2;--zhimai-panel-strong:#ffffff;--zhimai-border:#e5ddd2;--zhimai-glow:#e0643a1a;--zhimai-shadow:0 18px 50px #1f2a2e14}"],
  // zhimai 自定义面板变量（暗色）
  [":root[saved-theme=dark]{--zhimai-accent:#e28b68;--zhimai-accent-2:#e28b68;--zhimai-panel:#1c1e1fdb;--zhimai-panel-strong:#222425f7;--zhimai-border:#d7beab2e;--zhimai-glow:#e28b681f;--zhimai-shadow:0 24px 70px #00000057}",
   ":root[saved-theme=dark]{--zhimai-accent:#e07b52;--zhimai-accent-2:#e07b52;--zhimai-panel:#242e30f0;--zhimai-panel-strong:#2b3538;--zhimai-border:#e8e1d41f;--zhimai-glow:#e07b521f;--zhimai-shadow:0 24px 70px #00000057}"],
  // 强制暗底改为主题变量
  ["html,body{color:#f3e9ed;background:#08080d!important}", "html,body{color:var(--darkgray);background:var(--light)!important}"],
  ["body .page,body .center,body .outer,body .page-header{background:#08080d!important}", "body .page,body .center,body .outer,body .page-header{background:var(--light)!important}"],
  // 图谱画布底色走主题面板色
  ["background:#05050aed", "background:var(--zhimai-panel)"],
  // 文章链接下划线 → teal
  ["text-decoration-color:#ff4d6752", "text-decoration-color:#14525a59"],
  // hero 光晕 → 橙
  ["radial-gradient(circle,#ff4d6747,#0000 68%)", "radial-gradient(circle,#e0643a40,#0000 68%)"],
  // hero 网格线 → teal 淡色
  ["#d7beab0f", "#14525a10"],
  // 按钮/卡片阴影与光晕 → 品牌色
  ["box-shadow:0 10px 30px #d9775738", "box-shadow:0 10px 30px #e0643a38"],
  ["box-shadow:0 12px 38px #33101912", "box-shadow:0 12px 38px #14525a14"],
  ["var(--card-glow,#ff4d672b)", "var(--card-glow,#e0643a2b)"],
  ["box-shadow:0 20px 48px #7c182d29", "box-shadow:0 20px 48px #14525a26"],
  ["--card-glow:#d977571f", "--card-glow:#e0643a24"],
  // 字体与前端统一
  ['font-family:"Source Sans 3",PingFang SC,Microsoft YaHei,system-ui,sans-serif', 'font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif'],
  ['--titleFont:"Schibsted Grotesk"', '--titleFont:"PingFang SC","Microsoft YaHei"'],
  ['--headerFont:"Schibsted Grotesk"', '--headerFont:"PingFang SC","Microsoft YaHei"'],
  ['--bodyFont:"Source Sans Pro"', '--bodyFont:"PingFang SC","Microsoft YaHei"'],
];

let patched = 0;
for (const root of ROOTS) {
  const dir = path.resolve(root);
  for (const name of readdirSync(dir)) {
    if (!/^index-.+\.css$/.test(name)) continue;
    const p = path.join(dir, name);
    let css = readFileSync(p, "utf8");
    let touched = false;
    for (const [from, to] of PAIRS) {
      if (!css.includes(from)) { console.warn(`  MISS in ${p}: ${from.slice(0, 60)}`); continue; }
      css = css.replaceAll(from, to);
      touched = true;
    }
    if (touched) { writeFileSync(p, css); patched++; }
  }
}
console.log(`rebrand: theme patched in ${patched} css files`);
