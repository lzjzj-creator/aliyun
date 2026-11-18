<script setup>
// 契约驱动的单栏简历渲染器（B 域 #10/#20）：不为字段写死 markup，
// 按 resume_template.json 的 sections/type/fields v-for 渲染——改契约即改渲染。
import { computed } from "vue";

const props = defineProps({
  template: { type: Object, required: true },      // 模板契约
  fields: { type: Object, default: () => ({}) },   // fields 数据
  evidence: { type: Object, default: () => ({}) }, // evidence_map：字段路径→引用
  gaps: { type: Array, default: () => [] },        // [{field_path,label,reason}]
  compare: { type: Object, default: null },        // 优化对比模式 {path:before}
  focus: { type: String, default: "" },            // 左栏点选的区块 key → 高亮
});

const gapSet = computed(() => new Set(props.gaps.map((g) => g.field_path)));

// —— 字段路径工具（与 README/evidence_map 约定一致）——
const fp = (sec, i, key, j) =>
  j === undefined ? `${sec}.${i}.${key}` : `${sec}.${i}.${key}.${j}`;
const simpleFp = (sec, key) => `${sec}.${key}`;

function eviTitle(path) {
  const ev = props.evidence[path];
  if (!ev?.length) return "";
  const q = ev[0].quote || ev[0];
  return `出处：${q}`;
}
const isGap = (path) => gapSet.value.has(path);
const isCompared = (path) => props.compare && path in props.compare;
const beforeOf = (path) => (props.compare ? props.compare[path] : "");

// 值展示：空值 → 占位符
function showVal(path, val, label) {
  if (val === undefined || val === null || val === "" || (Array.isArray(val) && !val.length)) {
    return { text: `【待补充：${label}】`, gap: true };
  }
  return { text: Array.isArray(val) ? val.join("、") : String(val), gap: isGap(path) };
}

// —— 区块计算 ——
const sections = computed(() =>
  (props.template.sections || []).slice().sort((a, b) => a.order - b.order));
const basics = computed(() => sections.value.find((s) => s.key === "basics"));
const basicsData = computed(() => props.fields.basics || {});

// 头部：姓名 + 求职意向 + 联系信息行
const headFields = computed(() => {
  const sec = basics.value;
  if (!sec) return { name: "", role: "", contact: [] };
  const d = basicsData.value;
  const contact = sec.fields
    .filter((f) => !["name", "target_role"].includes(f.key))
    .map((f) => ({ f, ...showVal(`basics.${f.key}`, d[f.key], f.label) }))
    .filter((c) => !c.gap || isGap(`basics.${c.f.key}`) || true); // 联系字段空则不渲染（除gap标注）
  return {
    name: showVal("basics.name", d.name, "姓名"),
    role: showVal("basics.target_role", d.target_role, "求职意向"),
    contact: contact.filter((c) => !(c.gap && !isGap(`basics.${c.f.key}`))),
  };
});

// 非 basics 的可见区块
const bodySections = computed(() =>
  sections.value.filter((s) => s.key !== "basics" && sectionVisible(s)));

function sectionVisible(sec) {
  const data = props.fields[sec.key];
  if (sec.required !== false) return true;                    // 必填节恒显示（含占位）
  // 可选节：有内容才显示
  if (!data) return false;
  if (sec.type === "entries") return (data || []).length > 0;
  if (sec.type === "lines") return (data.items || []).length > 0;
  if (sec.type === "text") return !!(data.text || "").trim();
  if (sec.type === "skillgroups") return (data.groups || []).length > 0;
  return true;
}

// entries 型区块的展示模型：titleLine = 非 period/tech/bullets/tags 的文本字段
function entryModel(sec, e, i) {
  const get = (key) => e?.[key];
  const titleParts = [];
  const subParts = [];
  let period = null;
  let tech = null;
  let bullets = null;
  for (const f of sec.entry_fields || []) {
    const v = get(f.key);
    const path = fp(sec.key, i, f.key);
    if (f.key === "period") { period = { f, ...showVal(path, v, f.label) }; continue; }
    if (f.input === "bullets") {
      bullets = (v || []).map((b, j) => ({ ...showVal(`${path}.${j}`, b, `${f.label} ${j + 1}`), path: `${path}.${j}` }));
      if (!v?.length) bullets = [{ ...showVal(path, "", f.label), path }];
      continue;
    }
    if (f.input === "tags" || f.key === "tech") { tech = { f, items: v || [], path }; continue; }
    const part = { f, path, ...showVal(path, v, f.label) };
    (f.required ? titleParts : subParts).push(part);
  }
  return { titleParts, subParts, period, tech, bullets };
}

defineExpose({ fp, simpleFp });
</script>

<template>
  <article class="resume-doc">
    <!-- 头部：姓名 / 求职意向 / 联系信息（basics 区块） -->
    <header class="rd-head">
      <h1 class="rd-name" :class="{ gap: headFields.name.gap }" :title="eviTitle('basics.name')">
        {{ headFields.name.text }}
      </h1>
      <p class="rd-role" :class="{ gap: headFields.role.gap }" :title="eviTitle('basics.target_role')">
        {{ headFields.role.text }}
      </p>
      <p class="rd-contact">
        <template v-for="(c, i) in headFields.contact" :key="c.f.key">
          <span v-if="i" class="sep">·</span>
          <span :class="{ gap: c.gap }" :title="eviTitle(`basics.${c.f.key}`)">{{ c.f.label }} {{ c.text }}</span>
        </template>
      </p>
    </header>

    <!-- 其余区块按契约 v-for -->
    <section v-for="sec in bodySections" :key="sec.key" class="rd-sec"
             :class="{ hl: focus === sec.key }" :data-key="sec.key">
      <h2 class="rd-sec-title">{{ sec.title }}</h2>

      <!-- 空 entries 区块：整节占位 -->
      <p v-if="sec.type === 'entries' && !(fields[sec.key] || []).length"
         class="gap rd-sec-empty" :title="(gaps.find(g => g.field_path === sec.key) || {}).reason || ''">
        【待补充：{{ sec.title }}】
      </p>

      <!-- entries：教育/实习/项目 -->
      <div v-for="(e, i) in (sec.type === 'entries' ? (fields[sec.key] || []) : [])" :key="i" class="rd-entry">
        <template v-if="entryModel(sec, e, i) && true">
          <div class="rd-entry-head">
            <b>
              <span v-for="(p, pi) in entryModel(sec, e, i).titleParts" :key="p.f.key">
                <span v-if="pi" class="sep"> · </span>
                <span :class="{ gap: p.gap, compared: isCompared(p.path) }" :title="isCompared(p.path) ? `原文：${beforeOf(p.path)}` : eviTitle(p.path)">{{ p.text }}</span>
              </span>
            </b>
            <span v-if="entryModel(sec, e, i).period" class="rd-period"
                  :class="{ gap: entryModel(sec, e, i).period.gap }"
                  :title="eviTitle(fp(sec.key, i, 'period'))">{{ entryModel(sec, e, i).period.text }}</span>
          </div>
          <p v-if="entryModel(sec, e, i).subParts.length" class="rd-sub">
            <span v-for="(p, pi) in entryModel(sec, e, i).subParts" :key="p.f.key">
              <span v-if="pi" class="sep"> · </span>
              <span :class="{ gap: p.gap }" :title="eviTitle(p.path)">{{ p.f.label }} {{ p.text }}</span>
            </span>
          </p>
          <p v-if="entryModel(sec, e, i).tech?.items.length" class="rd-tech">
            <span v-for="(t, ti) in entryModel(sec, e, i).tech.items" :key="ti" class="tag">{{ t }}</span>
          </p>
          <ul v-if="entryModel(sec, e, i).bullets" class="rd-bullets">
            <li v-for="(b, bi) in entryModel(sec, e, i).bullets" :key="bi"
                :class="{ gap: b.gap, compared: isCompared(b.path) }"
                :title="isCompared(b.path) ? `原文：${beforeOf(b.path)}` : eviTitle(b.path)">{{ b.text }}</li>
          </ul>
        </template>
      </div>

      <!-- skillgroups -->
      <div v-if="sec.type === 'skillgroups'" class="rd-skills">
        <p v-for="(g, gi) in (fields[sec.key]?.groups || [])" :key="gi" class="rd-skill-line">
          <b>{{ g.name }}：</b>
          <span v-for="(it, ii) in (g.items || [])" :key="ii">
            <span v-if="ii">、</span>
            <span :title="eviTitle(`${sec.key}.groups.${gi}.items.${ii}`)">{{ it }}</span>
          </span>
        </p>
        <p v-if="!(fields[sec.key]?.groups || []).length" class="gap rd-sec-empty">【待补充：{{ sec.title }}】</p>
      </div>

      <!-- lines：荣誉奖项 -->
      <ul v-if="sec.type === 'lines' && (fields[sec.key]?.items || []).length" class="rd-lines">
        <li v-for="(it, i) in fields[sec.key].items" :key="i">
          <span :class="{ gap: isGap(`${sec.key}.items.${i}.text`) }"
                :title="eviTitle(`${sec.key}.items.${i}.text`)">{{ it.text }}</span>
          <span v-if="it.period" class="rd-period">{{ it.period }}</span>
        </li>
      </ul>

      <!-- text：自我评价 -->
      <p v-if="sec.type === 'text'" class="rd-text"
         :class="{ gap: isGap(`${sec.key}.text`) || !(fields[sec.key]?.text || '').trim() }"
         :title="eviTitle(`${sec.key}.text`)">
        {{ (fields[sec.key]?.text || "").trim() || `【待补充：${sec.title}】` }}
      </p>
    </section>
  </article>
</template>

<style scoped>
/* 单栏中文校招版式：参照 sb2nov/Jake's 风格与 billryan 中文排版，普通 CSS 重写（无 Sass） */
.resume-doc {
  background: #fff; color: #111; padding: 28px 32px;
  font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 10.5pt; line-height: 1.5;
  border: 1px solid var(--line, #e5ddd2); border-radius: 4px;
  max-width: 760px; margin: 0 auto;   /* A4 画布感：纸面居中浮在纸底上 */
  box-shadow: 0 2px 6px rgba(31,42,46,.06), 0 10px 28px rgba(31,42,46,.08);
}
.rd-head { text-align: center; border-bottom: 2px solid #333; padding-bottom: 8px; margin-bottom: 10px; }
.rd-name { font-size: 20pt; margin: 0; font-weight: 700; letter-spacing: 2px; }
.rd-role { margin: 4px 0 2px; font-size: 11pt; color: #333; }
.rd-contact { margin: 2px 0 0; font-size: 9pt; color: #444; }
.sep { color: #999; margin: 0 6px; }

.rd-sec { margin-top: 12px; }
.rd-sec-title {
  font-size: 12pt; font-weight: 700; margin: 0 0 6px;
  border-bottom: 1px solid #999; padding-bottom: 2px; letter-spacing: 1px;
}
.rd-entry { margin-bottom: 8px; break-inside: avoid; }
.rd-entry-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.rd-entry-head b { font-size: 10.5pt; }
.rd-period { flex: none; font-size: 9.5pt; color: #555; }
.rd-sub { margin: 1px 0 0; font-size: 9.5pt; color: #444; }
.rd-tech { margin: 2px 0 0; }
.tag {
  display: inline-block; margin: 0 5px 3px 0; padding: 0 7px;
  border: 1px solid #ccc; border-radius: 3px; font-size: 8.5pt; color: #333; background: #f7f7f7;
}
.rd-bullets { margin: 3px 0 0; padding-left: 1.3em; }
.rd-bullets li { margin-bottom: 2px; }
.rd-skills .rd-skill-line { margin: 2px 0; }
.rd-lines { margin: 0; padding-left: 1.3em; }
.rd-lines li { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 2px; }
.rd-text { margin: 0; }

/* 占位/对比标记 */
.gap {
  background: var(--warn-bg); color: var(--warn); padding: 0 3px; border-radius: 3px;
  box-shadow: inset 0 0 0 1px var(--warn-line);   /* 琥珀虚线感占位：是标注不是错误 */
}
.rd-sec-empty { padding: 4px 8px; border: 1px dashed var(--warn-line); border-radius: 6px; }
.compared { background: var(--good-bg); border-bottom: 1px dashed var(--good); }
.rd-sec.hl { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 4px; }

/* 打印：白底、去交互、不跨页断行 */
@media print {
  .resume-doc { border: none; border-radius: 0; padding: 0; font-size: 10pt; }
  .gap, .compared { background: none; border-bottom: none; }
  .rd-entry, .rd-sec { break-inside: avoid; }
}
</style>
