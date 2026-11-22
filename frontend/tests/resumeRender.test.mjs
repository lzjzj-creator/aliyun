import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { parse, compileScript, compileStyle } from "@vue/compiler-sfc";
import { createRenderer, h, markRaw, nextTick, reactive } from "vue";

const rendererUrl = new URL("../../backend/resume_assets/render.mjs", import.meta.url);
const cssUrl = new URL("../../backend/resume_assets/resume.css", import.meta.url);
const previewUrl = new URL("../src/components/ResumePreview.vue", import.meta.url);
const template = JSON.parse(await readFile(new URL("../../backend/resume_template.json", import.meta.url), "utf8"));
// An absent implementation is an explicit red assertion, not an import-time crash.
let renderer = {};
try { renderer = await import(rendererUrl.href); }
catch (error) { if (error.code !== "ERR_MODULE_NOT_FOUND") throw error; }
const render = (...args) => {
  assert.equal(typeof renderer.renderResume, "function", "renderResume has not been implemented");
  return renderer.renderResume(...args);
};
const issues = (...args) => {
  assert.equal(typeof renderer.exportIssues, "function", "exportIssues has not been implemented");
  return renderer.exportIssues(...args);
};
const clone = (value) => structuredClone(value);
const basics = () => ({ name: "林同学", target_role: "前端开发工程师", email: "lin@example.com" });
const project = () => ({ name: "知识检索", role: "核心开发", period: "2025.03–2026.01", tech: ["Vue", "Python"], bullets: ["完成检索服务并覆盖 35 个测试"] });
const sample = () => ({
  basics: { ...basics(), phone: "13800000000", city: "杭州", github: "https://example.com/lin", blog: "https://example.com/portfolio" },
  education: [{ school: "示例大学", major: "计算机科学", degree: "本科", period: "2022.09–2026.06", gpa_or_rank: "GPA 3.8/4", courses: ["数据结构", "操作系统"] }],
  internships: [{ company: "示例公司", role: "研发实习生", period: "2025.07–2025.09", bullets: ["优化页面加载，耗时降低 30%"] }],
  projects: [project()],
  skills: { groups: [{ name: "语言", items: ["熟悉 JavaScript", "了解 Python"] }] },
  awards: { items: [{ text: "省级程序设计竞赛一等奖", period: "2024.05" }] },
  summary: { text: "关注可访问性和可维护性。" },
});
const escapeText = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const orderOf = (html) => [...html.matchAll(/<section\b[^>]*data-key="([^"]+)"/g)].map((match) => match[1]);

function deepFreeze(value) {
  if (value && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

test("renderer exposes the versioned pure HTML API", () => {
  assert.equal(renderer.RENDER_VERSION, "3.1.0");
  const html = render(template, { basics: basics() });
  assert.match(html, /^<article\b[^>]*class="resume-paper"/);
  assert.match(html, /data-layout="classic"/);
  assert.match(html, /data-render-version="3\.1\.0"/);
  assert.match(html, /<h1\b[^>]*>林同学<\/h1>/);
  assert.ok(html.endsWith("</article>"));
  assert.doesNotMatch(html, /<html|<head>|<style|<script|<link|title=|contenteditable|待补充/);
});

test("classic puts education first; modern puts internships and projects before education", () => {
  const fields = sample();
  const reversed = clone(template);
  reversed.sections.reverse();
  assert.deepEqual(orderOf(render(reversed, fields)), ["education", "internships", "projects", "skills", "awards", "summary"]);
  assert.deepEqual(orderOf(render(reversed, fields, "modern")), ["internships", "projects", "education", "skills", "awards", "summary"]);
  assert.match(render(template, fields, "modern"), /data-layout="modern"/);
  const unsafeLayout = render(template, fields, '\"><img src=x onerror=alert(1)>');
  assert.match(unsafeLayout, /data-layout="classic"/);
  assert.doesNotMatch(unsafeLayout, /<img|onerror/);
});

test("all actual contract content survives without field labels, internal annotations or truncation", () => {
  const fields = sample();
  fields.projects = Array.from({ length: 5 }, (_, i) => ({ ...project(), name: `项目 ${i}`, bullets: Array.from({ length: 7 }, (_, j) => `成果 ${i}-${j}`) }));
  fields.summary.text = "完整评价".repeat(80);
  fields.meta = { notes: "SECRET_META" };
  fields.gaps = [{ field_path: "projects", reason: "SECRET_GAP" }];
  fields.evidence_map = { "basics.name": "SECRET_EVIDENCE" };
  fields.basics.annotation = "SECRET_BASICS";
  fields.projects[0].tags = ["SECRET_TAG"];
  fields.projects[0].evidence = "SECRET_ENTRY";
  fields.skills.groups[0].meta = "SECRET_GROUP";
  fields.awards.items[0].note = "SECRET_AWARD";
  const html = render(template, fields);
  for (const text of ["GPA 3.8/4", "数据结构", "操作系统", "杭州", "https://example.com/portfolio", "语言", "熟悉 JavaScript", "省级程序设计竞赛一等奖", "2024.05", "项目 4", "成果 4-6", fields.summary.text]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /SECRET_|GPA\/排名|你的角色|技术栈|求职意向|出处|原文|class="(?:gap|compared|hl|tag)"/);
});

test("fields excluded by the template whitelist never render or block export", () => {
  const reduced = clone(template);
  reduced.sections = reduced.sections.filter((section) => section.key !== "summary");
  reduced.sections.find((section) => section.key === "basics").fields = reduced.sections.find((section) => section.key === "basics").fields.filter((field) => field.key !== "github");
  reduced.sections.find((section) => section.key === "education").entry_fields = reduced.sections.find((section) => section.key === "education").entry_fields.filter((field) => field.key !== "courses");
  reduced.sections.find((section) => section.key === "awards").entry_fields[0].line_fields = ["text"];
  const fields = sample();
  fields.basics.github = "【待补充：SECRET_LINK】";
  fields.education[0].courses = ["【待补充：SECRET_COURSE】"];
  fields.awards.items[0].period = "【待补充：SECRET_DATE】";
  fields.summary.text = "【待补充：SECRET_SUMMARY】";
  assert.doesNotMatch(render(reduced, fields), /SECRET_|待补充/);
  assert.deepEqual(issues(reduced, fields), []);
});

test("every text position escapes HTML, quotes, scripts, entities and URLs instead of loading them", () => {
  const attack = '<img src="https://evil.invalid/x" onerror=\'alert(1)\'><script>alert(2)</script>&quot; javascript:alert(3)';
  const fields = sample();
  fields.basics.name = attack;
  fields.basics.github = attack;
  fields.education[0].school = attack;
  fields.education[0].period = attack;
  fields.education[0].courses = [attack];
  fields.projects[0].tech = [attack];
  fields.projects[0].bullets = [attack];
  fields.skills.groups = [{ name: attack, items: [attack] }];
  fields.awards.items = [{ text: attack, period: attack }];
  fields.summary.text = attack;
  const escaped = escapeText(attack);
  const html = render(template, fields);
  assert.equal(html.split(escaped).length - 1, 12);
  // Attribute-like words inside escaped text are harmless; inspect actual tags.
  const tags = html.match(/<[^>]*>/g).join("");
  assert.doesNotMatch(tags, /<(?:img|script|iframe|a|link)\b|\s(?:href|src|onerror)=/);
  const hostileTemplate = clone(template);
  hostileTemplate.sections.find((section) => section.key === "summary").title = attack;
  hostileTemplate.sections.find((section) => section.key === "summary").entry_fields[0].label = attack;
  assert.ok(render(hostileTemplate, fields).includes(`<h2 class="resume-section-title">${escaped}</h2>`));
  assert.deepEqual(issues(hostileTemplate, fields), []);
});

test("empty sections, entries, bullets and groups produce neither placeholders nor empty markup", () => {
  const empty = {
    basics: {}, education: [{}, { school: "  ", courses: ["", null] }],
    internships: [{ bullets: ["", "\n", null] }], projects: [],
    skills: { groups: [{}, { name: " ", items: ["", null] }] },
    awards: { items: [{ text: " ", period: "" }, {}] }, summary: { text: "\n" },
  };
  const html = render(template, empty);
  assert.doesNotMatch(html, /<header|<section|<li|<p|<h1|待补充|undefined|null/);
  const fields = { basics: basics(), projects: [{ ...project(), bullets: ["", "真实成果", " ", null] }] };
  assert.equal((render(template, fields).match(/<li\b/g) || []).length, 1);
});

test("partial original content remains visible, including unnamed and date-only entries", () => {
  const fields = {
    basics: { name: "  林同学  " }, education: [{ gpa_or_rank: "排名 10%" }],
    projects: [{ bullets: ["提升效率【待补充：指标】"] }],
    skills: { groups: [{ name: "自定义分组", items: [] }, { items: ["Rust"] }] },
    awards: { items: [{ period: "2024.05" }] },
  };
  const html = render(template, fields);
  for (const value of ["  林同学  ", "排名 10%", "提升效率【待补充：指标】", "自定义分组", "Rust", "2024.05"]) assert.ok(html.includes(value), value);
  assert.equal((html.match(/待补充/g) || []).length, 1, "must preserve, not remove or invent placeholder text");
});

test("long Chinese/English titles, URLs and all bullets remain unshortened", () => {
  const fields = { basics: { ...basics(), blog: `https://example.com/${"long-path".repeat(100)}` }, projects: [{ ...project(), name: "长中文项目LongEnglishTitle".repeat(80), period: "2020.01–2026.11".repeat(20), bullets: ["多页正文\n".repeat(400)] }] };
  const html = render(template, fields);
  for (const value of [fields.basics.blog, fields.projects[0].name, fields.projects[0].period, fields.projects[0].bullets[0]]) assert.ok(html.includes(value));
});

test("renderer is deterministic, non-mutating and ignores inherited or malformed objects", () => {
  const fields = deepFreeze(sample());
  const frozenTemplate = deepFreeze(clone(template));
  assert.equal(render(frozenTemplate, fields), render(frozenTemplate, fields));
  assert.deepEqual(issues(frozenTemplate, fields), []);
  assert.doesNotMatch(render(template, Object.create(sample())), /林同学|知识检索/);
  assert.doesNotMatch(render(template, { basics: Object.create(basics()) }), /林同学/);
  for (const invalid of [null, undefined, 42, "bad", []]) assert.doesNotThrow(() => render(template, invalid));
  assert.doesNotMatch(render(template, { basics: { name: { html: "SECRET" } }, projects: "bad", skills: { groups: [null] }, awards: { items: [null] } }), /SECRET|\[object Object\]/);
});

test("nonempty legacy scalar tags and bullets are preserved as text, not dropped or spread into characters", () => {
  const fields = { basics: basics(), projects: [{ ...project(), tech: "Vue / Node", bullets: "完整的一条项目成果" }] };
  const html = render(template, fields);
  assert.ok(html.includes("Vue / Node"));
  assert.ok(html.includes("完整的一条项目成果"));
  assert.deepEqual(issues(template, fields), []);
});

test("export requires name, target role, and at least one of phone or email", () => {
  const missing = issues(template, {});
  assert.deepEqual(missing.map((issue) => issue.field_path).sort(), ["basics.name", "basics.phone", "basics.target_role"]);
  for (const issue of missing) {
    assert.deepEqual(Object.keys(issue).sort(), ["field_path", "label", "reason"]);
    assert.ok(issue.label && issue.reason);
  }
  assert.match(missing.find((issue) => issue.field_path === "basics.phone").reason, /电话.*邮箱/);
  assert.deepEqual(issues(template, { basics: basics() }), []);
  assert.deepEqual(issues(template, { basics: { name: "林", target_role: "开发", phone: "123" } }), []);
});

test("export validates required fields only on nonempty experiences and keeps original indices", () => {
  const fields = { basics: basics(), education: [{}], internships: [{ bullets: [" "] }, { company: "真实公司" }], projects: [{}, { name: "真实项目" }] };
  const found = issues(template, fields);
  assert.deepEqual(found.map((issue) => issue.field_path).sort(), ["internships.1.bullets", "internships.1.period", "internships.1.role", "projects.1.bullets", "projects.1.period", "projects.1.role", "projects.1.tech"]);
  const educationIssues = issues(template, { basics: basics(), education: [{ courses: ["操作系统"] }] });
  assert.deepEqual(educationIssues.map((issue) => issue.field_path).sort(), ["education.0.degree", "education.0.major", "education.0.period", "education.0.school"]);
});

test("export ignores min/adaptive counts and internal manual gaps, without inventing experience", () => {
  const fields = { basics: basics(), education: [], internships: [], projects: [project()], skills: { groups: [] }, gaps: [{ field_path: "projects", reason: "【待补充：至少三个项目】", source: "manual" }], meta: { gap: "【待补充】" } };
  fields.projects[0].evidence = "【待补充】";
  assert.deepEqual(issues(template, fields), []);
  fields.projects = [{ name: " ", bullets: ["", "\n"] }];
  assert.deepEqual(issues(template, fields), []);
  assert.equal(orderOf(render(template, fields)).length, 0);
});

test("placeholder markers in every allowed leaf block export with precise field paths", () => {
  const fields = sample();
  fields.basics.city = "待补充：城市";
  fields.education[0].courses[1] = "【待补充】";
  fields.projects[0].tech[0] = "[待补充: 技术栈]";
  fields.projects[0].bullets[0] = "提升效率【待补充：数值】%";
  fields.skills.groups[0].name = "待补充";
  fields.skills.groups[0].items[1] = "待补充:熟练度";
  fields.awards.items[0].text = "【 待补充：级别 】";
  fields.awards.items[0].period = "待补充：时间";
  fields.summary.text = "具备【待补充】能力";
  const before = clone(fields);
  const found = issues(template, fields);
  assert.deepEqual(found.map((issue) => issue.field_path).sort(), ["basics.city", "education.0.courses.1", "projects.0.tech.0", "projects.0.bullets.0", "skills.groups.0.name", "skills.groups.0.items.1", "awards.items.0.text", "awards.items.0.period", "summary.text"].sort());
  for (const issue of found) assert.match(issue.reason, /待补充/);
  assert.deepEqual(fields, before);
  assert.ok(render(template, fields).includes("提升效率【待补充：数值】%"));
  assert.deepEqual(issues(template, { basics: basics(), summary: { text: "维护待补充材料的审核流程" } }), []);
});

test("a marker in an optional field still blocks export even when other required facts are complete", () => {
  const fields = sample();
  fields.basics.phone = "【待补充】";
  assert.deepEqual(issues(template, fields).map((issue) => issue.field_path), ["basics.phone"]);
});

async function cssTree() {
  const source = await readFile(cssUrl, "utf8");
  const compiled = compileStyle({ source, filename: cssUrl.pathname, id: "resume-css-test" });
  assert.deepEqual(compiled.errors, []);
  return { source, root: compiled.rawResult.root };
}
function declarations(rule) {
  return Object.fromEntries(rule.nodes.filter((node) => node.type === "decl").map((node) => [node.prop, node.value]));
}
function isPrint(rule) {
  for (let parent = rule.parent; parent; parent = parent.parent) if (parent.type === "atrule" && parent.name === "media" && parent.params === "print") return true;
  return false;
}

test("shared CSS scopes all selectors to the paper, fixes A4 geometry and wraps long text", async () => {
  const { source, root } = await cssTree();
  const rules = [];
  root.walkRules((rule) => {
    for (const selector of rule.selector.split(",")) assert.match(selector.trim(), /^\.resume-paper(?:\b|\s|\[)/, selector);
    rules.push(rule);
  });
  const paper = declarations(rules.find((rule) => rule.selector === ".resume-paper" && !isPrint(rule)));
  assert.equal(paper.width, "210mm");
  assert.equal(paper["min-height"], "297mm");
  assert.equal(paper.padding, "14mm");
  assert.equal(paper["box-sizing"], "border-box");
  assert.match(paper["font-family"], /^"?ZhimaiResume/);
  assert.ok(["10.5pt", "11pt"].includes(paper["font-size"]));
  assert.equal(paper["line-height"], "1.4");
  assert.equal(paper["overflow-wrap"], "anywhere");
  const name = declarations(rules.find((rule) => rule.selector === ".resume-paper .resume-name"));
  const heading = declarations(rules.find((rule) => rule.selector === ".resume-paper .resume-section-title"));
  assert.equal(name["font-size"], "22pt");
  assert.equal(heading["font-size"], "12pt");
  assert.ok(heading["border-bottom"]);
  // Existing application h1/.pane h2 rules set a teal color; paper headings
  // must explicitly win the cascade rather than inherit those application rules.
  assert.equal(name.color, "inherit");
  assert.equal(heading.color, "inherit");
  assert.ok(rules.some((rule) => rule.selector.includes("resume-entry-head") && declarations(rule)["flex-wrap"] === "wrap"));
  // Local font URLs may be integrated by the host; remote resources are not allowed.
  assert.doesNotMatch(source, /url\(\s*["']?(?:https?:|\/\/)|@import|text-overflow:\s*ellipsis|line-clamp|overflow:\s*hidden|position:\s*absolute|max-height/);
});

test("print CSS uses A4 margins, removes paper padding/shadow and allows long entries across pages", async () => {
  const { root } = await cssTree();
  const rules = [], pages = [];
  root.walkAtRules("page", (rule) => {
    assert.equal(rule.params, "zhimai-resume", "must not change printing outside resume paper");
    pages.push(declarations(rule));
  });
  const paperRule = [];
  root.walkRules(".resume-paper", (rule) => paperRule.push(declarations(rule)));
  assert.ok(paperRule.some((rule) => rule.page === "zhimai-resume"));
  assert.ok(pages.some((page) => page.size === "A4" && page.margin === "14mm"));
  root.walkRules((rule) => rules.push(rule));
  const paper = declarations(rules.find((rule) => rule.selector === ".resume-paper" && isPrint(rule)));
  assert.equal(paper.width, "auto");
  assert.equal(paper["min-height"], "0");
  assert.equal(paper.padding, "0");
  assert.equal(paper["box-shadow"], "none");
  assert.ok(rules.some((rule) => rule.selector.includes("resume-section-title") && declarations(rule)["break-after"] === "avoid"));
  for (const rule of rules) {
    if (rule.selector.split(",").some((selector) => /\.(?:resume-section|resume-entry)\s*$/.test(selector))) {
      assert.notEqual(declarations(rule)["break-inside"], "avoid");
      assert.notEqual(declarations(rule)["page-break-inside"], "avoid");
    }
  }
});

// Compile the real SFC and run it with Vue's real reactivity/lifecycle. Only host
// geometry and ResizeObserver are simulated; this is not a browser layout test.
async function mountPreview(props, { observer = true } = {}) {
  const source = await readFile(previewUrl, "utf8");
  const { descriptor, errors } = parse(source, { filename: previewUrl.pathname });
  assert.deepEqual(errors, []);
  const compiled = compileScript(descriptor, { id: "resume-preview-test", inlineTemplate: true });
  const moduleSource = compiled.content
    .replace(/from\s+(["'])vue\1/g, `from ${JSON.stringify(import.meta.resolve("vue"))}`)
    .replace(/from\s+(["'])\.\.\/\.\.\/\.\.\/backend\/resume_assets\/render\.mjs\1/g, `from ${JSON.stringify(rendererUrl.href)}`);
  const component = (await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`)).default;
  const geometry = { viewportWidth: 400, paperWidth: 794, paperHeight: 1200 };
  const observers = [];
  const originalObserver = Object.getOwnPropertyDescriptor(globalThis, "ResizeObserver");
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const listeners = new Map();
  class Observer {
    constructor(callback) { this.callback = callback; this.targets = new Set(); this.disconnected = false; observers.push(this); }
    observe(target) { this.targets.add(target); } // Native observe is asynchronous, never fires in the constructor.
    disconnect() { this.disconnected = true; this.targets.clear(); }
    fire() { this.callback([...this.targets].map((target) => ({ target, contentRect: { width: target.offsetWidth, height: target.offsetHeight } }))); }
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, writable: true, value: observer ? Observer : undefined });
  Object.defineProperty(globalThis, "window", { configurable: true, writable: true, value: {
    addEventListener: (event, callback) => listeners.set(event, callback),
    removeEventListener: (event, callback) => { if (listeners.get(event) === callback) listeners.delete(event); },
  } });
  function node(type, text = "") {
    // Real DOM elements are not reactive proxies; keep host node identity stable.
    return markRaw({ type, text, props: {}, children: [], parent: null,
      get clientWidth() { return this.props.class?.includes("resume-preview-sheet") ? geometry.paperWidth : geometry.viewportWidth; },
      get offsetWidth() { return this.clientWidth; },
      get offsetHeight() { return geometry.paperHeight; },
    });
  }
  const host = createRenderer({
    createElement: node, createText: (text) => node("#text", text), createComment: (text) => node("#comment", text),
    setText: (target, text) => { target.text = text; }, setElementText: (target, text) => { target.text = text; },
    patchProp: (target, key, previous, value) => { target.props[key] = value && typeof value === "object" ? { ...value } : value; },
    insert: (target, parent, anchor = null) => { target.parent = parent; const index = parent.children.indexOf(anchor); parent.children.splice(index < 0 ? parent.children.length : index, 0, target); },
    remove: (target) => { if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1); },
    parentNode: (target) => target.parent,
    nextSibling: (target) => target.parent?.children[target.parent.children.indexOf(target) + 1] || null,
  });
  const root = node("root");
  const state = reactive(props);
  // A real parent render propagates changed primitive props (createApp root
  // props alone are a one-time snapshot, not a substitute for parent updates).
  const app = host.createApp({ render: () => h(component, { ...state }) });
  app.mount(root);
  await nextTick();
  const find = (className, target = root) => target.props.class?.split(" ").includes(className) ? target : target.children.map((child) => find(className, child)).find(Boolean);
  return { source, descriptor, root, state, geometry, observers, listeners, find,
    async resize() { observers.forEach((item) => item.fire()); listeners.get("resize")?.(); await nextTick(); },
    dispose() {
      app.unmount();
      if (originalObserver) Object.defineProperty(globalThis, "ResizeObserver", originalObserver); else delete globalThis.ResizeObserver;
      if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow); else delete globalThis.window;
    },
  };
}

test("preview is only a shared-renderer wrapper; auxiliary props never reach the paper", async () => {
  const fields = sample();
  const view = await mountPreview({ template, fields, layout: "modern", evidence: { "basics.name": [{ quote: "SECRET" }] }, gaps: [{ field_path: "basics.name", reason: "SECRET" }], compare: { "basics.name": "SECRET" }, focus: "education" });
  try {
    const sheet = view.find("resume-preview-sheet");
    assert.ok(sheet, "preview must host the shared paper");
    assert.equal(sheet.props.innerHTML, render(template, fields, "modern"));
    assert.doesNotMatch(sheet.props.innerHTML, /SECRET|\b(?:gap|compared|hl)\b|title=/);
    assert.ok(view.descriptor.styles.some((style) => style.src === "../../../backend/resume_assets/resume.css" && !style.scoped));
    for (const prop of ["template", "fields", "evidence", "gaps", "compare", "focus"]) assert.ok(view.source.includes(`${prop}:`));
    view.state.fields.basics.name = "更新后的姓名";
    view.state.layout = "classic";
    await nextTick();
    assert.equal(sheet.props.innerHTML, render(template, view.state.fields, "classic"));
  } finally { view.dispose(); }
});

test("ResizeObserver scales a fixed-width sheet and reserves scaled height on resize/content growth", async () => {
  const view = await mountPreview({ template, fields: sample() });
  try {
    const sheet = view.find("resume-preview-sheet"), frame = view.find("resume-preview-frame");
    assert.ok(sheet && frame, "fixed sheet and layout frame are required");
    assert.equal(sheet.props.style.transform, `scale(${400 / 794})`);
    assert.ok(Math.abs(parseFloat(frame.props.style.height) - 1200 * 400 / 794) < 0.01);
    assert.ok(view.observers.some((observer) => observer.targets.has(sheet)));
    view.geometry.viewportWidth = 320;
    view.geometry.paperHeight = 1800;
    await view.resize();
    assert.equal(sheet.props.style.transform, `scale(${320 / 794})`);
    assert.ok(Math.abs(parseFloat(frame.props.style.height) - 1800 * 320 / 794) < 0.01);
    assert.equal(sheet.offsetWidth, 794, "viewport never changes paper width");
    view.geometry.viewportWidth = 1100;
    await view.resize();
    assert.equal(sheet.props.style.transform, "scale(1)");
    assert.equal(parseFloat(frame.props.style.height), 1800);
    assert.equal(parseFloat(frame.props.style.width), 794);
    view.geometry.viewportWidth = 0;
    await view.resize();
    assert.doesNotMatch(JSON.stringify(frame.props.style), /NaN|Infinity/);
    view.geometry.viewportWidth = 360;
    await view.resize();
    assert.equal(sheet.props.style.transform, `scale(${360 / 794})`);
    assert.match(view.source, /\.resume-preview-sheet\s*\{[^}]*width:\s*210mm/s);
    // CSS transforms do not shrink the original scrollable overflow box.
    assert.match(view.source, /\.resume-preview\s*\{[^}]*overflow:\s*(?:hidden|clip)/s);
    assert.match(view.source, /@media print[\s\S]*overflow:\s*visible/);
    assert.match(view.source, /@media print[\s\S]*transform:\s*none\s*!important/);
  } finally { view.dispose(); }
  assert.ok(view.observers.length > 0 && view.observers.every((observer) => observer.disconnected));
  assert.equal(view.listeners.size, 0);
});

test("preview remains mountable without ResizeObserver and updates after fallback resize", async () => {
  const view = await mountPreview({ template, fields: { basics: basics() } }, { observer: false });
  try {
    assert.ok(view.find("resume-preview-sheet"));
    view.geometry.viewportWidth = 320;
    await view.resize();
    assert.equal(view.find("resume-preview-sheet").props.style.transform, `scale(${320 / 794})`);
  } finally { view.dispose(); }
  assert.equal(view.listeners.size, 0);
});
