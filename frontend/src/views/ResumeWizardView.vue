<script setup>
// 简历向导三步（B 域 #19/#20/#22）：
// ① 一键填写（会话素材→字段+出处）② 人工修改+实时预览+打印导出 ③ 逐段黑箱优化+对比+导出
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
import { downloadResumePdf } from "../utils/resumeDownload";
import { exportIssues } from "../../../backend/resume_assets/render.mjs";
import { getTemplate, autofill, optimizePlan, optimizeSection, exportDraftPdf } from "../api/resume";
import { getSession, listSessions } from "../api/sessions";
import {
  DRAFT_KEY, OPT_DRAFT_KEY, saveDraft, loadDraft, getCurrentSessionId,
} from "../storage";

const step = ref(1);
const tpl = ref(null);
const errMsg = ref("");
const busy = ref(false);
const exporting = ref(false);
const layout = ref("classic");
const mobilePanel = ref("edit");
const exportProblems = ref([]);
const saveState = ref("未保存");
const photoBusy = ref(false);
const photoContext = ref(0);
let photoRequest = null;
function onPhotoBusy(event) {
  if (event.context !== photoContext.value) return;
  if (event.busy) { photoRequest = event.request; photoBusy.value = true; }
  else if (event.request === photoRequest) { photoRequest = null; photoBusy.value = false; }
}
function resetPhotoContext() {
  photoContext.value++;
  photoRequest = null; photoBusy.value = false;
}
onBeforeUnmount(resetPhotoContext);
const formDisabled = computed(() => busy.value || optBusy.value || exporting.value);
const locked = computed(() => formDisabled.value || photoBusy.value);

// —— 草稿数据 ——
const fields = ref({});
const evidence = ref({});
const evidenceRows = computed(() => Object.entries(evidence.value || {})
  .map(([path, sources]) => ({ path, sources: Array.isArray(sources) ? sources.filter((s) => s && typeof s === "object") : [] }))
  .filter((row) => row.sources.length));
const gaps = ref([]);
const draftVer = ref("");

// —— 素材来源 ——
const sessions = ref([]);
const pickedSession = ref(getCurrentSessionId());
const convo = ref([]); // [{role,content,id}]

// —— 优化 ——
const plan = ref(null);
const results = ref({});       // section_key → response
const optFields = ref(null);   // 合并后的优化稿
const optBusy = ref(false);
const displaySaveState = computed(() => photoBusy.value ? "照片处理中…" : step.value === 3 && optFields.value ? "优化稿未保存 · 待采纳" : saveState.value);
const placeholders = computed(() =>
  Object.values(results.value).flatMap((r) => r.placeholders || []));
const pendingQuestions = computed(() =>
  Object.values(results.value).flatMap((r) => r.pending_questions || []));

function onFieldsUpdate(nextFields) {
  if (formDisabled.value) return;
  // 仅表单编辑使旧优化快照失效；采纳/保存不走此处，照片元数据随新字段保留。
  optFields.value = null; plan.value = null; results.value = {};
  errMsg.value = "";
  exportProblems.value = [];
  fields.value = nextFields;
}

const emit = defineEmits(["goto-library"]);
const STEP_CN = { 1: "① 一键填写", 2: "② 修改预览", 3: "③ 一键优化" };

// —— 工作台：区块导航 + 点击定位 ——
const focusSec = ref("");
const panelEl = ref(null);
const navSections = computed(() =>
  (tpl.value?.sections || [])
    .slice().sort((a, b) => a.order - b.order)
    .map((s) => ({
      key: s.key, title: s.title, required: s.required !== false,
      gapCount: gaps.value.filter((g) => g.field_path === s.key || g.field_path.startsWith(s.key + ".")).length,
      hasData: sectionHasData(s),
    })));
function sectionHasData(s) {
  const d = fields.value[s.key];
  if (d == null) return false;
  if (s.type === "entries") return d.length > 0;
  if (s.type === "lines") return (d.items || []).length > 0;
  if (s.type === "skillgroups") return (d.groups || []).length > 0;
  if (s.type === "text") return !!(d.text || "").trim();
  return Object.values(d).some((v) => v !== "" && v != null && !(Array.isArray(v) && !v.length));
}
// 导航仅定位编辑区；正式预览不带焦点轮廓。
function gotoSec(key) {
  mobilePanel.value = "edit";
  focusSec.value = key;
  panelEl.value?.querySelector(`.rff-sec[data-key="${key}"]`)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}

onMounted(() => { loadTemplate(); loadSessions(); });

// 存储层会静默降级，因此读回核对后才显示“已保存”，不把失败说成成功。
function saveLocalDraft() {
  if (photoBusy.value || !draftVer.value || !Object.keys(fields.value).length) return false;
  saveState.value = "保存中…";
  const snapshot = JSON.parse(JSON.stringify({
    template_version: draftVer.value, fields: fields.value,
    evidence_map: evidence.value, gaps: gaps.value,
  }));
  try {
    saveDraft(DRAFT_KEY, snapshot);
    const stored = loadDraft(DRAFT_KEY);
    if (!stored || stored.template_version !== snapshot.template_version ||
        JSON.stringify(stored.fields) !== JSON.stringify(snapshot.fields) ||
        JSON.stringify(stored.evidence_map) !== JSON.stringify(snapshot.evidence_map) ||
        JSON.stringify(stored.gaps) !== JSON.stringify(snapshot.gaps)) throw new Error("未写入本机");
    saveState.value = "已保存到本机";
    return true;
  } catch {
    saveState.value = "本机保存失败，请重试；仍可导出当前快照";
    return false;
  }
}
watch([fields, draftVer, photoBusy], saveLocalDraft, { deep: true });

async function loadTemplate() {
  try {
    tpl.value = await getTemplate();
  } catch {
    if (import.meta.env.DEV) {
      tpl.value = await fetch("/demo/v2/template.json").then((r) => r.json());
    } else {
      errMsg.value = "模板契约加载失败";
    }
  }
  // 恢复草稿：template_version 匹配才用，不匹配提示重新填写（§11）
  const d = loadDraft(DRAFT_KEY);
  if (d && d.template_version === tpl.value?.template_version) {
    resetPhotoContext();
    fields.value = d.fields; evidence.value = d.evidence_map; gaps.value = d.gaps;
    draftVer.value = d.template_version;
  } else if (d) {
    errMsg.value = "本地草稿的模板版本已过期，请重新一键填写。";
  }
}

async function loadSessions() {
  try {
    sessions.value = await listSessions();
  } catch {
    if (import.meta.env.DEV) {
      try { sessions.value = await fetch("/demo/v2/sessions.json").then((r) => r.json()); }
      catch { /* 忽略 */ }
    }
  }
}

async function readConvo() {
  errMsg.value = "";
  if (!pickedSession.value) { errMsg.value = "请先选择一个会话"; return false; }
  try {
    const bundle = await getSession(pickedSession.value);
    convo.value = bundle.messages || [];
  } catch (e) {
    if (import.meta.env.DEV) {
      const demo = await fetch("/demo/v2/session.json").then((r) => r.json());
      convo.value = demo.messages || [];
    } else {
      errMsg.value = e.status === 404 ? "会话不存在或无权访问" : "会话读取失败";
      return false;
    }
  }
  if (!convo.value.length) { errMsg.value = "该会话还没有消息"; return false; }
  return true;
}

async function useDemoConvo() {
  const demo = await fetch("/demo/v2/session.json").then((r) => r.json());
  convo.value = demo.messages || [];
  errMsg.value = "";
}

async function doAutofill() {
  if (!tpl.value || locked.value) return;
  busy.value = true; errMsg.value = "";
  try {
    let payload = { template_version: tpl.value.template_version };
    if (convo.value.length) {
      payload.messages = convo.value;                 // 降级路径：消息直传
      payload.session_id = undefined;
    } else if (pickedSession.value) {
      payload.session_id = pickedSession.value;       // A 域就绪后走会话读取
    } else {
      errMsg.value = "请先读取会话素材";
      return;
    }
    let r;
    try {
      r = await autofill(payload, pickedSession.value || undefined);
    } catch (e) {
      if (e.status === 409) {                          // 版本漂移：重取契约重试一次
        await loadTemplate();
        payload.template_version = tpl.value.template_version;
        r = await autofill(payload, pickedSession.value || undefined);
      } else throw e;
    }
    resetPhotoContext();
    fields.value = r.fields;
    evidence.value = r.evidence_map || {};
    gaps.value = r.gaps || [];
    draftVer.value = tpl.value.template_version;
    optFields.value = null; plan.value = null; results.value = {};
    saveLocalDraft();
  } catch (e) {
    errMsg.value = e.status === 501
      ? "会话存储未就绪：请先点「读取会话」用直传模式"
      : `填写失败：${e.message}`;
  } finally {
    busy.value = false;
  }
}

const filled = computed(() => Object.keys(fields.value).length > 0);

async function doOptimize() {
  if (!tpl.value || !filled.value || locked.value) return;
  optBusy.value = true; errMsg.value = "";
  plan.value = null; results.value = {}; optFields.value = null;
  try {
    const ver = tpl.value.template_version;
    plan.value = await optimizePlan({ template_version: ver, fields: fields.value });
    optFields.value = JSON.parse(JSON.stringify(fields.value));
    for (const s of plan.value.sections) {
      if (!(s.optimizable && s.status === "pending")) continue;
      s.status = "running";
      try {
        const r = await optimizeSection({
          template_version: ver, section: s.key, fields: fields.value,
        });
        results.value = { ...results.value, [s.key]: r };
        if (r.status === "done" && r.optimized != null) {
          optFields.value = { ...optFields.value, [s.key]: r.optimized };
        }
        s.status = r.status === "error" ? "error" : "done";
      } catch (e) {
        results.value = {
          ...results.value,
          [s.key]: { status: "error", steps: [], change_log: [], placeholders: [],
                     pending_questions: [], error: e.message },
        };
        s.status = "error";                              // 单段失败不阻塞其他段
      }
    }
  } catch (e) {
    errMsg.value = `优化失败：${e.message}`;
  } finally {
    optBusy.value = false;
  }
}

function adoptAll() {
  if (!optFields.value || locked.value) return;
  fields.value = JSON.parse(JSON.stringify(optFields.value));
  saveDraft(OPT_DRAFT_KEY, {
    template_version: tpl.value.template_version,
    fields: optFields.value, evidence_map: evidence.value, gaps: gaps.value,
  });
  saveLocalDraft();
  optFields.value = null;
  errMsg.value = "";
}

function discardOptimized() {
  if (locked.value) return;
  // 原稿及本机保存状态保持不变；不写 DRAFT_KEY / OPT_DRAFT_KEY。
  optFields.value = null; plan.value = null; results.value = {};
  errMsg.value = "";
  exportProblems.value = [];
  step.value = 2;
  mobilePanel.value = "edit";
}

async function exportPrint() {
  if (!tpl.value || !filled.value || locked.value) return;
  exporting.value = true;
  errMsg.value = "";
  exportProblems.value = [];
  try {
    // 只发送点击时纸面对应的明确快照；选中的素材会话不隐式绑定到导出请求。
    const snapshot = JSON.parse(JSON.stringify(step.value === 3 && optFields.value ? optFields.value : fields.value));
    exportProblems.value = exportIssues(tpl.value, snapshot);
    if (exportProblems.value.length) {
      errMsg.value = "请先补全或修正以下内容，再导出 PDF。";
      return;
    }
    const blob = await exportDraftPdf({
      template_version: draftVer.value || tpl.value.template_version,
      fields: snapshot, layout: layout.value,
    });
    downloadResumePdf(blob, "简历");
  } catch (e) {
    errMsg.value = e.message || "PDF 导出失败，请重试";
    exportProblems.value = e.issues || e.data?.detail?.issues || [];
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <section class="pane wizard">
    <nav class="wiz-steps" aria-label="简历制作步骤">
      <button v-for="n in [1, 2, 3]" :key="n" :class="{ on: step === n }"
              :aria-current="step === n ? 'step' : undefined"
              :disabled="locked || (n > 1 && !filled)" @click="step = n">{{ STEP_CN[n] }}</button>
      <button class="wiz-lib" :disabled="locked" @click="emit('goto-library')">简历库 →</button>
    </nav>
    <div v-if="filled" class="wiz-toolbar">
      <label class="layout-control">版式
        <select v-model="layout" :disabled="locked">
          <option value="classic">中文经典</option>
          <option value="modern">中文现代</option>
        </select>
      </label>
      <span class="save-state" role="status" aria-live="polite">{{ displaySaveState }}</span>
      <button class="ghost" :disabled="locked" @click="saveLocalDraft">保存草稿</button>
      <button class="primary" :disabled="locked || !tpl" @click="exportPrint">
        {{ exporting ? "导出中…" : step === 3 && optFields ? "导出优化稿" : "导出 PDF" }}
      </button>
      <p class="snapshot-hint">导出当前预览快照，无需开启长期记忆；草稿仅保存在本机。</p>
    </div>
    <div v-if="errMsg || exportProblems.length" class="wiz-feedback" role="alert">
      <p v-if="errMsg" class="warn">{{ errMsg }}</p>
      <ul v-if="exportProblems.length">
        <li v-for="(issue, i) in exportProblems" :key="i">{{ issue.label || issue.field_path }}：{{ issue.reason }}</li>
      </ul>
    </div>
    <div v-if="step > 1" class="mobile-switch" aria-label="工作区显示">
      <button :aria-pressed="mobilePanel === 'edit'" @click="mobilePanel = 'edit'">编辑</button>
      <button :aria-pressed="mobilePanel === 'preview'" @click="mobilePanel = 'preview'">预览</button>
    </div>

    <!-- ================= ① 一键填写 ================= -->
    <div v-if="step === 1">
      <h2>第一步：选择素材来源，一键填写</h2>
      <div class="wiz-source">
        <label>
          访谈会话
          <select v-model="pickedSession" :disabled="locked">
            <option value="">— 选择会话 —</option>
            <option v-for="s in sessions" :key="s.id" :value="s.id">
              {{ s.title }}（{{ s.stage }}）
            </option>
          </select>
        </label>
        <button class="ghost" :disabled="locked || !pickedSession" @click="readConvo">读取会话</button>
        <button class="ghost" :disabled="locked" @click="useDemoConvo">演示素材</button>
        <button :disabled="locked || (!convo.length && !pickedSession)" @click="doAutofill">
          {{ busy ? "填写中…" : "一键填写" }}
        </button>
      </div>
      <p v-if="convo.length" class="wiz-hint">已载入 {{ convo.length }} 条对话素材</p>
      <p v-if="tpl" class="wiz-hint">模板：{{ tpl.name }} · v{{ tpl.template_version }} · {{ tpl.page_rule }}</p>

      <fieldset v-if="filled && tpl" class="wiz-fields" :disabled="formDisabled">
        <legend class="sr-only">简历内容</legend>
        <ResumeFieldsForm :model-value="fields" @update:model-value="onFieldsUpdate" :template="tpl" :gaps="gaps"
                          :disabled="formDisabled" :context="photoContext" @photo-busy="onPhotoBusy" />
      </fieldset>
      <div v-if="filled" class="row">
        <span />
        <button :disabled="locked" @click="step = 2">下一步：预览导出 →</button>
      </div>
    </div>

    <!-- 内容与正式预览双栏；区块导航收进编辑侧栏。 -->
    <div v-else-if="step === 2" class="wiz-work" :data-panel="mobilePanel">
      <aside class="wz-panel" ref="panelEl">
        <h2 class="wz-panel-title">人工修改 · 即改即存本机</h2>
        <details v-if="evidenceRows.length" class="evidence-panel">
          <summary>字段出处（仅供核对，不会导出）</summary>
          <div v-for="row in evidenceRows" :key="row.path" class="evidence-field">
            <b>{{ row.path }}</b>
            <ul>
              <li v-for="(source, i) in row.sources" :key="i">
                <p>{{ source.quote || "未提供引用原文" }}</p>
                <template v-for="key in ['session_id', 'msg_id', 'fact_id', 'fact_version', 'fact_status']" :key="key">
                  <span v-if="source[key] != null && source[key] !== ''">{{ key }}：{{ source[key] }}</span>
                </template>
              </li>
            </ul>
          </div>
        </details>
        <details class="section-nav">
          <summary>区块导航</summary>
          <nav class="wz-nav" aria-label="简历内容区块">
        <button v-for="s in navSections" :key="s.key" class="wz-nav-item"
                :class="{ on: focusSec === s.key, gap: s.gapCount > 0, ok: s.hasData && !s.gapCount }"
                @click="gotoSec(s.key)">
          <span class="wz-nav-title">{{ s.title }}</span>
          <span v-if="s.gapCount" class="wz-nav-badge warn">{{ s.gapCount }} 待补</span>
          <span v-else-if="s.hasData" class="wz-nav-badge">✓</span>
          <span v-else class="wz-nav-badge dim">空</span>
        </button>
          </nav>
        </details>
        <fieldset class="wiz-fields" :disabled="formDisabled" :aria-busy="locked">
          <legend class="sr-only">编辑简历字段</legend>
          <ResumeFieldsForm v-if="tpl" :model-value="fields" @update:model-value="onFieldsUpdate" :template="tpl" :gaps="gaps"
                          :disabled="formDisabled" :context="photoContext" @photo-busy="onPhotoBusy" />
        </fieldset>
        <div class="row wz-actions">
          <button class="ghost" :disabled="locked" @click="step = 1">← 上一步</button>
          <button :disabled="locked" @click="step = 3">一键优化 →</button>
        </div>
      </aside>
      <div class="wz-canvas">
        <p class="preview-caption">投递预览 · {{ layout === "classic" ? "中文经典" : "中文现代" }}</p>
        <ResumePreview v-if="tpl" class="print-area" :template="tpl" :fields="fields" :layout="layout" />
      </div>
    </div>

    <!-- ================= ③ 逐段优化 + 对比 + 导出 ================= -->
    <div v-else class="wiz-work" :data-panel="mobilePanel">
      <aside class="wz-panel">
        <h2 class="wz-panel-title">优化建议与改动</h2>
        <details v-if="evidenceRows.length" class="evidence-panel">
          <summary>字段出处（仅供核对，不会导出）</summary>
          <div v-for="row in evidenceRows" :key="row.path" class="evidence-field">
            <b>{{ row.path }}</b>
            <ul>
              <li v-for="(source, i) in row.sources" :key="i">
                <p>{{ source.quote || "未提供引用原文" }}</p>
                <template v-for="key in ['session_id', 'msg_id', 'fact_id', 'fact_version', 'fact_status']" :key="key">
                  <span v-if="source[key] != null && source[key] !== ''">{{ key }}：{{ source[key] }}</span>
                </template>
              </li>
            </ul>
          </div>
        </details>
        <div class="opt-panel">
        <button class="opt-run" :disabled="locked || !filled" @click="doOptimize">
          {{ optBusy ? "优化中…" : "一键优化" }}
        </button>
        <ul v-if="plan" class="opt-list">
          <li v-for="s in plan.sections" :key="s.key" :data-st="s.status"
              :class="{ on: focusSec === s.key }">
            <b>{{ s.title }}</b>
            <span class="st">
              {{ { pending: "待优化", running: "优化中…", done: "完成", error: "失败", skipped: "跳过", ok: "无需改", check: "人工确认" }[s.status] || s.status }}
            </span>
            <span v-if="s.reason" class="rs">{{ s.reason }}</span>
          </li>
        </ul>
        <button class="ghost back" :disabled="locked" @click="step = 2">← 返回修改</button>
        </div>
        <p v-if="!plan && !optBusy" class="wiz-hint">点击「一键优化」，只改表达，不改事实。原文、改动与待确认事项在这里查看，投递预览不附带批注。</p>
        <p v-if="optFields" class="wiz-hint">当前预览为优化稿；可导出此快照、采纳，或放弃优化稿返回原稿。</p>

        <template v-for="(r, key) in results" :key="key">
          <div v-if="r.change_log?.length" class="rewrite">
            <h3>改动明细 · {{ (plan?.sections || []).find(s => s.key === key)?.title || key }}</h3>
            <div v-for="(c, i) in r.change_log" :key="i" class="pair">
              <div><b>原文</b><p>{{ c.before }}</p></div>
              <div><b>优化</b><p>{{ c.after }}</p><p class="follow">{{ c.reason }}</p></div>
            </div>
          </div>
          <div v-if="r.error" class="warn">{{ r.error }}</div>
        </template>

        <div v-if="placeholders.length" class="gaps">
          <h3>待补充占位（{{ placeholders.length }} 处）</h3>
          <p v-for="(p, i) in placeholders" :key="i">· {{ p.field_path }}：{{ p.placeholder }}</p>
        </div>
        <div v-if="pendingQuestions.length" class="questions">
          <b>建议你确认的事实：</b>
          <p v-for="(q, i) in pendingQuestions" :key="i">· {{ q }}</p>
        </div>

        <div v-if="optFields" class="row wz-actions">
          <button :disabled="locked" @click="adoptAll">采纳优化稿</button>
          <button class="ghost" :disabled="locked" @click="discardOptimized">放弃优化稿</button>
        </div>
      </aside>
      <div class="wz-canvas">
        <p class="preview-caption">{{ optFields ? "优化稿预览" : "投递预览" }} · {{ layout === "classic" ? "中文经典" : "中文现代" }}</p>
        <ResumePreview v-if="tpl" class="print-area" :template="tpl" :fields="optFields || fields" :layout="layout" />
      </div>
    </div>
  </section>
</template>


<style scoped>
.wizard { background: #f4f5f7; border-radius: 14px; padding: 20px 24px 40px; }
.wizard :deep(:focus-visible) { outline: 3px solid var(--teal); outline-offset: 3px; }
.wiz-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 14px 0; margin-bottom: 14px; border-bottom: 1px solid var(--line); }
.layout-control { display: flex; align-items: center; gap: 8px; font-size: .82rem; }
.layout-control select { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--ink); font: inherit; }
.save-state { margin-right: auto; font-size: .78rem; color: var(--ink-soft); }
.snapshot-hint { flex-basis: 100%; margin: 0; font-size: .75rem; color: var(--ink-soft); }
.wiz-feedback { background: #fff; border: 1px solid var(--warn-line); border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; color: var(--err); font-size: .82rem; }
.wiz-fields { min-width: 0; border: 0; padding: 0; margin: 0; }
.section-nav { margin-bottom: 16px; border-bottom: 1px solid var(--line); padding-bottom: 10px; }
.section-nav summary { font-size: .82rem; padding: 6px 0; cursor: pointer; }
.evidence-panel { margin-bottom: 14px; border-bottom: 1px solid var(--line); padding-bottom: 10px; font-size: .82rem; overflow-wrap: anywhere; }
.evidence-panel summary { cursor: pointer; padding: 6px 0; }
.evidence-field { margin-top: 10px; }
.evidence-field ul { margin: 6px 0; padding-left: 20px; }
.evidence-field p { white-space: pre-wrap; margin: 6px 0; }
.evidence-field span { display: block; color: var(--ink-soft); font-size: .75rem; }
.preview-caption { margin: 0 0 12px; font-size: .75rem; color: var(--ink-soft); }
.mobile-switch { display: none; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.wizard .wiz-steps .wiz-lib { margin-left: auto; color: var(--teal); }
.wiz-steps { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.wizard .wiz-steps button {
  border: 1px solid var(--line); background: #fff; color: var(--ink-soft);
  border-radius: 999px; padding: 6px 14px; font-size: .82rem; cursor: pointer;
}
.wiz-steps button.on { border-color: var(--teal); background: var(--teal); color: #fff; }
.wiz-source { display: flex; align-items: flex-end; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.wiz-source label { display: flex; flex-direction: column; gap: 4px; font-size: .8rem; color: var(--ink-soft); }
.wiz-source select { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font: inherit; background: #fff; min-width: 220px; }
.wiz-hint { font-size: .78rem; color: var(--mute); margin: 4px 0 12px; }

/* 双栏工作台：导航和建议留在内容侧栏，纸面只展示投递内容。 */
.wiz-work { display: grid; grid-template-columns: minmax(300px, .85fr) minmax(0, 1.4fr); gap: 24px; align-items: start; }
.wz-nav { display: grid; gap: 6px; padding-top: 8px; }
.wz-nav-item {
  display: flex; align-items: center; justify-content: space-between; gap: 6px;
  padding: 9px 12px; border: 1px solid var(--line); border-radius: 10px;
  background: #fff; font-size: .85rem; color: var(--ink); cursor: pointer; text-align: left;
}
.wz-nav-item:hover { border-color: var(--teal); }
.wz-nav-item.on { border-color: var(--teal); background: var(--hover-teal); }
.wz-nav-item.gap { border-left: 3px solid var(--warn-line); }
.wz-nav-badge { flex: none; font-size: .68rem; color: var(--good); }
.wz-nav-badge.warn { color: var(--warn); }
.wz-nav-badge.dim { color: var(--mute); }
.wz-canvas { min-width: 0; overflow: auto; }
.wz-panel {
  min-width: 0; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 18px;
}
.wz-panel-title { margin: 0 0 10px; font-size: .9rem; color: var(--teal); }
.row-right { display: flex; gap: 8px; flex-wrap: wrap; }
.wz-actions { margin-top: 14px; }
.primary { border-color: var(--accent) !important; background: var(--accent) !important; color: #fff !important; }

/* 优化导航列 */
.opt-run { margin-bottom: 10px; }
.opt-list { list-style: none; padding: 0; margin: 0 0 10px; }
.opt-list li {
  padding: 8px 10px; border: 1px solid var(--line); border-radius: 10px;
  margin-bottom: 6px; background: #fff; font-size: .85rem; cursor: pointer;
}
.opt-list li.on { border-color: var(--teal); background: var(--hover-teal); }
.opt-list li[data-st="done"] { border-left: 3px solid var(--good); }
.opt-list li[data-st="error"] { border-left: 3px solid var(--err); }
.opt-list li[data-st="running"] { border-left: 3px solid var(--accent); }
.opt-list .st { margin-left: 8px; font-size: .75rem; color: var(--mute); }
.opt-list .rs { display: block; font-size: .72rem; color: var(--mute); }
.wz-nav .back { margin-top: 4px; }

@media (max-width: 760px) {
  .wizard { padding: 16px 12px 32px; }
  .wiz-work { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .mobile-switch { display: flex; gap: 8px; margin-bottom: 14px; }
  .mobile-switch [aria-pressed="true"] { background: var(--teal); color: #fff; }
  .wiz-work[data-panel="edit"] .wz-canvas, .wiz-work[data-panel="preview"] .wz-panel { display: none; }
  .wz-panel { padding: 14px; }
  .save-state { width: 100%; order: 1; }
  .snapshot-hint { order: 2; }
}
@media print {
  .wiz-steps, .wiz-toolbar, .wiz-feedback, .mobile-switch, .wz-panel, .preview-caption, .evidence-panel { display: none !important; }
  .wiz-work, .wz-canvas { display: block !important; }
}
</style>
