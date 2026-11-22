import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { parse, compileScript, compileStyle } from "@vue/compiler-sfc";
import { createRenderer, h, markRaw, nextTick, reactive } from "vue";
import * as renderer from "../../backend/resume_assets/render.mjs";

const utilityUrl = new URL("../src/utils/resumePhoto.js", import.meta.url);
const rendererUrl = new URL("../../backend/resume_assets/render.mjs", import.meta.url);
let utility = {};
try { utility = await import(utilityUrl.href); }
catch (e) { if (e.code !== "ERR_MODULE_NOT_FOUND") throw e; }
const prepare = (...args) => {
  assert.equal(typeof utility.prepareResumePhoto, "function", "photo conversion is not implemented");
  return utility.prepareResumePhoto(...args);
};
const valid = (value) => {
  assert.equal(typeof renderer.isValidResumePhoto, "function", "shared photo validation is not implemented");
  return renderer.isValidResumePhoto(value);
};
// Header-only fixtures exercise the bounded parser; raster decoding is mocked below.
// These are deliberately NOT evidence of a real browser/Pillow JPEG decode.
function jpeg(width = 240, height = 320, size = 32) {
  const bytes = new Uint8Array(size);
  bytes.set([255, 216, 255, 192, 0, 17, 8, height >> 8, height & 255, width >> 8, width & 255, 3, 1, 17, 0, 2, 17, 0, 3, 17, 0]);
  bytes.set([255, 217], size - 2);
  return bytes;
}
function png(width, height) {
  const bytes = new Uint8Array(33);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82]);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width); view.setUint32(20, height);
  bytes.set([8, 6, 0, 0, 0], 24);
  return bytes;
}
const uri = (bytes = jpeg()) => `data:image/jpeg;base64,${Buffer.from(bytes).toString("base64")}`;
const file = (bytes = jpeg(), type = "image/jpeg") => new File([bytes], "portrait", { type });
const template = { sections: [{ key: "basics", type: "kv", fields: [
  { key: "name", label: "姓名" }, { key: "target_role", label: "岗位" }, { key: "email", label: "邮箱" },
] }] };
const fields = () => ({ basics: { name: "林", target_role: "开发".repeat(100), email: "long".repeat(80) + "@example.com" } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 12; i++) await nextTick(); };

const globalSnapshots = new WeakMap();
function globals(t, values) {
  if (!globalSnapshots.has(t)) {
    const originals = new Map();
    globalSnapshots.set(t, originals);
    t.after(() => { for (const [key, descriptor] of originals) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } });
  }
  const originals = globalSnapshots.get(t);
  for (const [key, value] of Object.entries(values)) {
    if (!originals.has(key)) originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
}
function graphics(t, options = {}) {
  const events = [];
  const image = { width: 1200, height: 1600, close: () => events.push("close"), ...options.image };
  const canvas = { width: 0, height: 0,
    getContext: () => ({ set fillStyle(value) { events.push(["background", value]); }, fillRect: (...args) => events.push(["fill", ...args]), drawImage: (...args) => { events.push(["draw", ...args]); if (options.drawError) throw new Error("draw failed"); } }),
    toDataURL: (mime, quality) => { events.push(["encode", mime, quality]); return options.encode ? options.encode(mime, quality, canvas) : uri(jpeg(canvas.width, canvas.height)); },
  };
  globals(t, { createImageBitmap: options.decode || (async () => { events.push("decode"); return image; }), document: { createElement: (tag) => { assert.equal(tag, "canvas"); return canvas; } } });
  return { image, events, canvas };
}

test("strict photo URI accepts inclusive byte/dimension bounds and rejects injection/noncanonical base64", () => {
  assert.equal(valid(uri(jpeg(240, 320, 30000))), true);
  assert.equal(valid(uri(jpeg(1, 1))), true);
  const canonical = uri(jpeg(2, 2, 31));
  // 31 bytes: the final encoded byte has four unused bits; changing them is noncanonical.
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const at = canonical.length - 3;
  const noncanonical = canonical.slice(0, at) + alphabet[alphabet.indexOf(canonical[at]) + 1] + canonical.slice(at + 1);
  for (const bad of [undefined, null, "", {}, "https://evil.test/a.jpg", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,AAAA", "data:image/gif;base64,AAAA", uri() + '" onerror="alert(1)', uri() + "\n", uri().replace("jpeg", "JPEG"), uri().slice(0, -1), noncanonical, uri(jpeg(241, 320)), uri(jpeg(240, 321)), uri(jpeg(0, 1)), uri(jpeg(240, 320, 30001)), uri(new Uint8Array(32))]) assert.equal(valid(bad), false, String(bad).slice(0, 100));
});

test("shared parser bounds JPEG segments and refuses missing/truncated SOF", () => {
  for (const bytes of [new Uint8Array([255,216,255,224,255,255]), new Uint8Array([255,216,255,192,0,17,8]), new Uint8Array([255,216,255,218,0,2,255,217]), new Uint8Array([255,216,255,224,0,0,255,217])]) assert.equal(valid(uri(bytes)), false);
  const padded = new Uint8Array(38);
  padded.set([255,216,255,225,0,4,0,0]); padded.set(jpeg().slice(2), 8);
  assert.equal(valid(uri(padded)), true, "APP metadata before SOF must not confuse dimension inspection");
});

test("no-photo markup stays byte-identical; only own _meta.photo renders and bad existing photos block export", () => {
  const original = fields(), clean = renderer.renderResume(template, original);
  assert.doesNotMatch(clean, /<img|resume-photo|header-text/);
  assert.equal(renderer.renderResume(template, { ...original, _meta: { manual_override: ["SECRET"], photoUrl: "SECRET" } }), clean);
  assert.equal(renderer.renderResume(template, { ...original, _meta: Object.create({ photo: uri() }) }), clean);
  assert.deepEqual(renderer.exportIssues(template, original), []);
  for (const photo of ["", null, "https://evil.test/x", uri(jpeg(241, 320)), uri() + '\" onload=alert(1)']) {
    const data = { ...original, _meta: { photo, manual_override: ["SECRET"] } };
    assert.doesNotMatch(renderer.renderResume(template, data), /<img|SECRET|onload/);
    const issues = renderer.exportIssues(template, data);
    assert.equal(issues.length, 1);
    assert.equal(issues[0].field_path, "_meta.photo");
    assert.match(issues[0].reason, /重新上传.*移除|移除.*重新上传/);
  }
});

test("photo headers reserve a non-cropping right column in both layouts without dropping long text", async () => {
  const data = { ...fields(), _meta: { photo: uri(), manual_override: ["SECRET"], notes: "SECRET" } };
  for (const layout of ["classic", "modern"]) {
    const html = renderer.renderResume(template, data, layout);
    assert.match(html, /<header class="resume-header resume-header-with-photo"><div class="resume-header-text">/);
    assert.match(html, /<img class="resume-photo" src="data:image\/jpeg;base64,[A-Za-z0-9+/=]+" alt="简历照片"/);
    assert.ok(html.includes(data.basics.target_role)); assert.ok(html.includes(data.basics.email));
    assert.doesNotMatch(html, /SECRET/);
    assert.deepEqual(renderer.exportIssues(template, data), []);
  }
  const css = await readFile(new URL("../../backend/resume_assets/resume.css", import.meta.url), "utf8");
  const compiled = compileStyle({ source: css, filename: "resume.css", id: "photo-test" });
  const rules = {};
  compiled.rawResult.root.walkRules((rule) => { rules[rule.selector] = Object.fromEntries(rule.nodes.filter((n) => n.type === "decl").map((n) => [n.prop, n.value])); });
  assert.equal(rules[".resume-paper .resume-header-with-photo"]?.display, "grid");
  assert.match(rules[".resume-paper .resume-header-with-photo"]["grid-template-columns"], /minmax\(0,\s*1fr\) 24mm/);
  assert.equal(rules[".resume-paper .resume-photo"]?.["object-fit"], "contain");
  assert.equal(rules[".resume-paper .resume-photo"].width, "24mm");
  assert.equal(rules[".resume-paper .resume-photo"].height, "32mm");
});

test("upload rejects MIME, signatures, size and huge JPEG/PNG dimensions before raster decoding", async (t) => {
  const g = graphics(t);
  for (const input of [file(jpeg(), "image/gif"), file(jpeg(), "image/svg+xml"), file(jpeg(), "text/plain"), file(png(10, 10), "image/jpeg"), file(jpeg(), "image/png"), file(new Uint8Array(0)), file(new Uint8Array(5 * 1024 * 1024 + 1)), file(jpeg(4097, 1)), file(jpeg(4096, 3000)), file(png(1, 4097), "image/png"), file(png(4000, 4000), "image/png")]) await assert.rejects(() => prepare(input), /JPEG|PNG|5\s*MB|尺寸|像素|图片|照片/);
  assert.equal(g.events.length, 0);
});

test("header inspection reads a bounded slice and rejects uninspectable headers without expensive decode", async (t) => {
  const g = graphics(t), reads = [];
  const input = { type: "image/jpeg", size: 5 * 1024 * 1024, slice(start, end) { reads.push([start,end]); return new Blob([new Uint8Array([255,216,255,225,255,255])]); }, arrayBuffer() { assert.fail("must not read the entire file"); } };
  await assert.rejects(() => prepare(input), /图片|照片|尺寸/);
  assert.equal(reads.length, 1); assert.equal(reads[0][0], 0); assert.ok(reads[0][1] <= 256 * 1024);
  assert.equal(g.events.length, 0);
});

test("canvas flattens transparency on white, fits 240x320, lowers quality to <=30k and closes bitmap", async (t) => {
  let attempts = 0;
  const g = graphics(t, { image: { width: 1600, height: 800 }, encode: (_mime, _quality, canvas) => uri(jpeg(canvas.width, canvas.height, ++attempts === 1 ? 30001 : 30000)) });
  const result = await prepare(file(png(1600, 800), "image/png"));
  assert.equal(valid(result), true);
  assert.equal(g.canvas.width, 240); assert.equal(g.canvas.height, 120);
  assert.deepEqual(g.events.find((e) => e[0] === "background"), ["background", "#fff"]);
  assert.ok(g.events.findIndex((e) => e[0] === "fill") < g.events.findIndex((e) => e[0] === "draw"));
  const encodes = g.events.filter((e) => e[0] === "encode");
  assert.equal(encodes.length, 2); assert.equal(encodes[0][1], "image/jpeg"); assert.ok(encodes[1][2] < encodes[0][2]);
  assert.equal(g.events.at(-1), "close");
});

test("decoded dimensions are checked again, encoder failures retain actionable errors and always close bitmaps", async (t) => {
  for (const options of [{ image: { width: 9000 } }, { drawError: true }, { encode: () => uri(jpeg(240,320,30001)) }, { encode: () => "data:image/png;base64,AAAA" }]) {
    const g = graphics(t, options);
    await assert.rejects(() => prepare(file()), /尺寸|像素|照片|图片|draw failed|压缩/);
    assert.equal(g.events.at(-1), "close");
  }
});

test("Image fallback revokes object URLs on success, decode failure and canvas failure", async (t) => {
  for (const failure of ["", "decode", "canvas"]) {
    const g = graphics(t, { drawError: failure === "canvas" }), revoked = [];
    class ImageMock {
      naturalWidth = 1200; naturalHeight = 1600;
      set src(value) { if (value) queueMicrotask(() => failure === "decode" ? this.onerror?.() : this.onload?.()); }
    }
    globals(t, { createImageBitmap: undefined, Image: ImageMock, URL: { createObjectURL: () => "blob:photo", revokeObjectURL: (url) => revoked.push(url) } });
    if (failure) await assert.rejects(() => prepare(file())); else assert.equal(valid(await prepare(file())), true);
    assert.deepEqual(revoked, ["blob:photo"]);
    assert.ok(!g.events.includes("close"));
  }
});

test("fallback revokes its URL even if Image construction fails", async (t) => {
  const revoked = [];
  globals(t, { createImageBitmap: undefined, Image: class { constructor() { throw new Error("image unavailable"); } }, URL: { createObjectURL: () => "blob:construction", revokeObjectURL: (url) => revoked.push(url) } });
  await assert.rejects(() => prepare(file()), /image unavailable/);
  assert.deepEqual(revoked, ["blob:construction"]);
});

async function mountForm(t, modelValue = { ...fields(), _meta: { photo: uri(), manual_override: ["basics.name"], note: "keep" } }) {
  const url = new URL("../src/components/ResumeFieldsForm.vue", import.meta.url);
  const source = await readFile(url, "utf8");
  const { descriptor } = parse(source, { filename: url.pathname });
  const script = compileScript(descriptor, { id: "photo-form-test", inlineTemplate: true });
  const code = script.content.replace(/from\s+(["'])vue\1/g, `from ${JSON.stringify(import.meta.resolve("vue"))}`)
    .replace(/from\s+(["'])\.\.\/utils\/resumePhoto(?:\.js)?\1/g, `from ${JSON.stringify(utilityUrl.href)}`)
    .replace(/from\s+(["'])\.\.\/\.\.\/\.\.\/backend\/resume_assets\/render\.mjs\1/g, `from ${JSON.stringify(rendererUrl.href)}`);
  const component = (await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`)).default;
  const node = (type, text = "") => markRaw({ type, text, props: {}, children: [], parent: null });
  const host = createRenderer({ createElement: node, createText: (text) => node("#text", text), createComment: (text) => node("#comment", text),
    setText: (n, text) => { n.text = text; }, setElementText: (n, text) => { n.text = text; n.children = []; },
    patchProp: (n, key, _old, value) => { n.props[key] = value; },
    insert: (n, parent, anchor) => { n.parent = parent; const at = parent.children.indexOf(anchor); parent.children.splice(at < 0 ? parent.children.length : at, 0, n); },
    remove: (n) => { if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1); },
    parentNode: (n) => n.parent, nextSibling: (n) => n.parent?.children[n.parent.children.indexOf(n) + 1] || null,
  });
  const root = node("root"), events = [], updates = [];
  const state = reactive({ modelValue, context: 1, disabled: false, template });
  const app = host.createApp({ render: () => h(component, { ...state, "onUpdate:modelValue": (value) => { updates.push(value); state.modelValue = value; }, onPhotoBusy: (event) => events.push(event) }) });
  app.mount(root); let mounted = true;
  const dispose = () => { if (mounted) { mounted = false; app.unmount(); } };
  t.after(dispose);
  const all = (n = root) => [n, ...n.children.flatMap(all)];
  const upload = () => all().find((n) => n.type === "input" && n.props.type === "file");
  const select = (input = file()) => { assert.ok(upload(), "optional photo upload must be visible"); return upload().props.onChange({ target: { files: [input], value: "selected" } }); };
  const remove = () => { const button = all().find((n) => n.type === "button" && n.text.includes("移除")); assert.ok(button); button.props.onClick(); };
  return { source, state, events, updates, all, select, remove, dispose };
}

test("form labels optional photo/privacy, reports rejection, preserves original, and removes only photo", async (t) => {
  graphics(t);
  const p = await mountForm(t), original = JSON.stringify(p.state.modelValue);
  assert.match(p.source, /照片（可选）/); assert.match(p.source, /随简历保存/); assert.match(p.source, /不.*AI/);
  assert.match(p.source, /min-height:\s*44px/); assert.match(p.source, /:focus-visible/);
  await p.select(file(jpeg(), "image/svg+xml")); await nextTick();
  assert.equal(JSON.stringify(p.state.modelValue), original);
  assert.ok(p.all().some((n) => n.props.role === "alert" && n.text));
  assert.equal(p.events.at(-1).busy, false);
  p.remove(); await nextTick();
  assert.equal(Object.hasOwn(p.state.modelValue._meta, "photo"), false);
  assert.deepEqual(p.state.modelValue._meta.manual_override, ["basics.name"]);
  assert.equal(p.state.modelValue._meta.note, "keep");
});

test("encoding emits busy immediately and merges photo into latest fields/meta rather than captured text", async (t) => {
  const wait = deferred(), g = graphics(t, { decode: () => wait.promise });
  const p = await mountForm(t);
  const pending = p.select();
  assert.equal(p.events.at(-1).busy, true);
  p.state.modelValue = { ...p.state.modelValue, basics: { name: "edited during encoding" }, _meta: { ...p.state.modelValue._meta, note: "new note" } };
  await flush(); wait.resolve(g.image); await pending; await nextTick();
  assert.equal(p.state.modelValue.basics.name, "edited during encoding");
  assert.equal(p.state.modelValue._meta.note, "new note");
  assert.equal(valid(p.state.modelValue._meta.photo), true);
  assert.equal(p.events.at(-1).busy, false);
  assert.equal(p.updates.length, 1);
});

test("later selection wins; an older completion never unlocks or overwrites it", async (t) => {
  const a = deferred(), b = deferred(); let count = 0;
  const g = graphics(t, { decode: () => ++count === 1 ? a.promise : b.promise });
  const p = await mountForm(t);
  const first = p.select(); await flush();
  const second = p.select(); await flush();
  const newest = p.events.at(-1);
  a.resolve(g.image); await first;
  assert.equal(p.events.at(-1), newest); assert.equal(newest.busy, true); assert.equal(p.updates.length, 0);
  b.resolve({ ...g.image, width: 800, height: 1600 }); await second;
  assert.equal(p.events.at(-1).busy, false); assert.equal(p.updates.length, 1);
  assert.equal(g.events.filter((e) => e === "close").length, 2);
});

test("context ABA, disabling and unmount invalidate pending photos and do not mutate another draft", async (t) => {
  for (const leave of ["context", "disabled", "unmount", "remove"]) {
    const wait = deferred(), g = graphics(t, { decode: () => wait.promise });
    const p = await mountForm(t);
    const pending = p.select(); await flush();
    if (leave === "context") { p.state.context = 2; await nextTick(); p.state.context = 1; p.state.modelValue = fields(); await nextTick(); }
    else if (leave === "disabled") { p.state.disabled = true; await nextTick(); p.state.disabled = false; await nextTick(); }
    else if (leave === "remove") p.remove();
    else p.dispose();
    const before = JSON.stringify(p.state.modelValue), updates = p.updates.length, events = p.events.length;
    wait.resolve(g.image); await pending;
    assert.equal(JSON.stringify(p.state.modelValue), before); assert.equal(p.updates.length, updates);
    assert.equal(p.events.length, events, "late finally must not emit an unlock");
    assert.equal(g.events.at(-1), "close");
    p.dispose();
  }
});

test("decoder rejection retains the original photo; stale errors never replace the current result", async (t) => {
  const a = deferred(), b = deferred(); let count = 0;
  const g = graphics(t, { decode: () => ++count === 1 ? a.promise : b.promise });
  const p = await mountForm(t), before = JSON.stringify(p.state.modelValue);
  const rejected = p.select(); await flush();
  a.reject(new Error("invalid raster")); await rejected; await nextTick();
  assert.equal(JSON.stringify(p.state.modelValue), before);
  assert.ok(p.all().some((n) => n.props.role === "alert" && n.text.includes("invalid raster")));
  const later = p.select(); await flush();
  p.state.context = 2; p.state.modelValue = fields(); await nextTick();
  const events = p.events.length;
  b.reject(new Error("obsolete raster error")); await later; await nextTick();
  assert.equal(p.events.length, events);
  assert.ok(!p.all().some((n) => n.props.role === "alert"));
  assert.equal(g.events.length, 0);
});

test("disabled form rejects selection without setting busy or decoding; invalid saved URI never becomes img src", async (t) => {
  const g = graphics(t);
  const p = await mountForm(t, { ...fields(), _meta: { photo: "https://evil.test/image" } });
  p.state.disabled = true; await nextTick();
  await p.select();
  assert.equal(p.events.length, 0); assert.equal(g.events.length, 0);
  assert.ok(!p.all().some((n) => n.type === "img"));
});
