<script setup>
// 简历向导两步（v5 §9.6）：
// ① 选素材（多会话多选 + 事实库开关）+ 生成（autofill→STAR扩写→内部优化，聚合进度条）
// ② 人工修改 + 实时预览 + 导出。草稿全部存服务端。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
import { downloadResumePdf } from "../utils/resumeDownload";
import { exportIssues } from "../../../backend/resume_assets/render.mjs";
import { getTemplate, autofill, expand, optimizePlan, optimizeSection, exportDraftPdf } from "../api/resume";
import { listSessions } from "../api/sessions";
import { getDraft, putDraft } from "../api/auth";

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
const formDisabled = computed(() => busy.value || pipeBusy.value || exporting.value);
const locked = computed(() => formDisabled.value || photoBusy.value);

// —— 草稿数据 ——
const fields = ref({});
const evidence = ref({});
const evidenceRows = computed(() => Object.entries(evidence.value || {})
  .map(([path, sources]) => ({ path, sources: Array.isArray(sources) ? sources.filter((s) => s && typeof s === "object") : [] }))
  .filter((row) => row.sources.length));
const gaps = ref([]);
const draftVer = ref("");
const draftId = ref("current");
const draftTitle = ref("简历草稿");

// —— 素材来源（v5：多选会话 + 事实库） ——
const sessions = ref([]);
const pickedSessions = ref(new Set());
const includeFacts = ref(true);

function toggleSession(sid) {
  const s = new Set(pickedSessions.value);
  if (s.has(sid)) s.delete(sid); else s.add(sid);
  pickedSessions.value = s;
}

// —— 生成管线：autofill → expand(STAR) → optimize(内部) ——
const pipeBusy = ref(false);
const pipeStages = ref([]);   // [{key,label,status:'pending'|'running'|'done'|'error'|'skip'}]
const pipeSummary = ref("");

function onFieldsUpdate(nextFields) {
  if (formDisabled.value) return;
  errMsg.value = "";
  exportProblems.value = [];
  fields.value = nextFields;
}

const emit = defineEmits(["goto-library"]);
const STEP_CN = { 1: "① 选素材 · 生成", 2: "② 预览微调 · 导出" };

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
// 反向通路：点预览区块 → 定位到左侧对应编辑区
function onPreviewClick(e) {
  const sec = e.target.closest(".resume-section[data-key]");
  if (sec) gotoSec(sec.dataset.key);
}

onMounted(() => { loadTemplate(); loadSessions(); });

// —— 服务端草稿：防抖写入 PUT /api/v4/drafts ——
let saveTimer = null;
function saveDraft() {
  if (photoBusy.value || !draftVer.value || !Object.keys(fields.value).length) return;
  clearTimeout(saveTimer);
  saveState.value = "保存中…";
  saveTimer = setTimeout(async () => {
    try {
      const snapshot = JSON.parse(JSON.stringify({
        draft_id: draftId.value, title: draftTitle.value,
        template_version: draftVer.value, fields: fields.value,
        evidence_map: evidence.value, gaps: gaps.value,
        photo: fields.value?._meta?.photo || null,
      }));
      await putDraft(snapshot);
      saveState.value = "已保存到云端";
    } catch {
      saveState.value = "云端保存失败，请重试；仍可导出当前快照";
    }
  }, 3000);
}
watch([fields, draftVer, photoBusy], () => { if (step.value === 2) saveDraft(); }, { deep: true });

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
  // 恢复服务端草稿：template_version 匹配才用
  try {
    const { draft } = await getDraft(draftId.value);
    if (draft && draft.template_version === tpl.value?.template_version) {
      resetPhotoContext();
      fields.value = draft.fields || {};
      evidence.value = draft.evidence_map || {};
      gaps.value = draft.gaps || [];
      draftVer.value = draft.template_version;
      draftTitle.value = draft.title || "简历草稿";
      step.value = 2;
      saveState.value = "已从云端恢复草稿";
    }
  } catch { /* 无草稿从空白开始 */ }
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

function setStage(key, status) {
  const s = pipeStages.value.find((x) => x.key === key);
  if (s) s.status = status;
}

// 一键生成：autofill（多源）→ expand（STAR 扩写）→ optimize（内部黑箱，自动采纳）
async function generate() {
  if (!tpl.value || locked.value) return;
  const ids = [...pickedSessions.value];
  if (!ids.length && !includeFacts.value) {
    errMsg.value = "请至少勾选一个素材来源（会话或经历证据库）";
    return;
  }
  pipeBusy.value = true;
  errMsg.value = "";
  pipeSummary.value = "";
  pipeStages.value = [
    { key: "autofill", label: "提取素材，填写字段", status: "running" },
    { key: "expand", label: "STAR 法则扩写经历", status: "pending" },
    { key: "optimize", label: "全面优化表达", status: "pending" },
  ];
  try {
    // ① autofill：多会话 + 事实库合并提取
    let payload = {
      template_version: tpl.value.template_version,
      session_ids: ids, include_facts: includeFacts.value,
    };
    let r;
    try {
      r = await autofill(payload);
    } catch (e) {
      if (e.status === 409) {                    // 版本漂移：重取契约重试一次
        await loadTemplate();
        payload.template_version = tpl.value.template_version;
        r = await autofill(payload);
      } else throw e;
    }
    resetPhotoContext();
    fields.value = r.fields || {};
    evidence.value = r.evidence_map || {};
    gaps.value = r.gaps || [];
    draftVer.value = tpl.value.template_version;
    setStage("autofill", "done");

    // ② expand：每条经历按 STAR 扩写为一段话（数字守卫不成立则保留原样）
    setStage("expand", "running");
    try {
      const ex = await expand({
        template_version: tpl.value.template_version,
        fields: fields.value,
      });
      if (ex.fields) fields.value = ex.fields;
      pipeSummary.value = `扩写 ${ex.expanded || 0} 条经历` +
        (ex.kept ? `，${ex.kept} 条素材不足保持原样` : "");
      setStage("expand", "done");
    } catch {
      setStage("expand", "skip");                // 扩写失败不阻塞出稿
    }

    // ③ optimize：逐段优化，结果自动采纳（黑箱，不展示 diff）
    setStage("optimize", "running");
    try {
      const ver = tpl.value.template_version;
      const planRes = await optimizePlan({ template_version: ver, fields: fields.value });
      const merged = JSON.parse(JSON.stringify(fields.value));
      let optCount = 0;
      for (const s of planRes.sections || []) {
        if (!(s.optimizable && s.status === "pending")) continue;
        try {
          const sec = await optimizeSection({
            template_version: ver, section: s.key, fields: fields.value,
          });
          if (sec.status === "done" && sec.optimized != null) {
            merged[s.key] = sec.optimized;
            optCount++;
          }
        } catch { /* 单段失败不阻塞其他段 */ }
      }
      fields.value = merged;
      pipeSummary.value = (pipeSummary.value ? pipeSummary.value + "；" : "") +
        (optCount ? `优化了 ${optCount} 个区块` : "表达已就绪");
      setStage("optimize", "done");
    } catch {
      setStage("optimize", "skip");
    }

    saveState.value = "未保存";
    step.value = 2;
    mobilePanel.value = "edit";
  } catch (e) {
    errMsg.value = `生成失败：${e.message}`;
  } finally {
    pipeBusy.value = false;
  }
}

const filled = computed(() => Object.keys(fields.value).length > 0);

async function exportPrint() {
  if (!tpl.value || !filled.value || locked.value) return;
  exporting.value = true;
  errMsg.value = "";
  exportProblems.value = [];
  try {
    const snapshot = JSON.parse(JSON.stringify(fields.value));
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
      <button v-for="n in [1, 2]" :key="n" :class="{ on: step === n }"
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
      <span class="save-state" role="status" aria-live="polite">{{ photoBusy ? "照片处理中…" : saveState }}</span>
      <button class="ghost" :disabled="locked" @click="saveDraft">保存草稿</button>
      <button class="primary" :disabled="locked || !tpl" @click="exportPrint">
        {{ exporting ? "导出中…" : "导出 PDF" }}
      </button>
      <p class="snapshot-hint">导出当前预览快照；草稿自动保存在云端账号。</p>
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

    <!-- ================= ① 选素材 · 生成 ================= -->
    <div v-if="step === 1">
      <h2>第一步：选择素材来源，生成简历</h2>
      <p class="wiz-hint">勾选聊过的访谈和经历库素材，多选会合并提取、去重后填进简历。</p>

      <div class="src-grid">
        <div class="src-card">
          <div class="src-card-title">访谈会话</div>
          <p v-if="!sessions.length" class="wiz-hint">还没有会话，先去「访谈挖掘」聊聊你的经历。</p>
          <label v-for="s in sessions" :key="s.id" class="src-item">
            <input type="checkbox" :checked="pickedSessions.has(s.id)"
                   :disabled="locked" @change="toggleSession(s.id)" />
            <span class="src-item-name">{{ s.title || s.id.slice(0, 8) }}</span>
            <span class="src-item-meta">{{ s.stage }}</span>
          </label>
        </div>
        <div class="src-card">
          <div class="src-card-title">经历证据库</div>
          <label class="src-item">
            <input type="checkbox" v-model="includeFacts" :disabled="locked" />
            <span class="src-item-name">纳入已确认的事实（含随手记）</span>
          </label>
          <p class="wiz-hint">你在「经历证据库」确认过的事实会作为素材一并提取。</p>
        </div>
      </div>

      <div class="wiz-generate">
        <button class="primary gen-btn" :disabled="locked || (!pickedSessions.size && !includeFacts)"
                @click="generate">
          {{ pipeBusy ? "生成中…" : "生成简历" }}
        </button>
        <ul v-if="pipeStages.length" class="pipe-stages" aria-live="polite">
          <li v-for="s in pipeStages" :key="s.key" :data-st="s.status">
            <i class="pipe-dot" />{{ s.label }}
            <span class="pipe-st">{{ { running: "进行中…", done: "完成", skip: "已跳过", pending: "" }[s.status] }}</span>
          </li>
        </ul>
        <p v-if="pipeSummary" class="wiz-hint">{{ pipeSummary }}</p>
      </div>

      <p v-if="tpl" class="wiz-hint">模板：{{ tpl.name }} · v{{ tpl.template_version }} · {{ tpl.page_rule }}</p>
    </div>

    <!-- ================= ② 预览微调 · 导出 ================= -->
    <div v-else class="wiz-work" :data-panel="mobilePanel">
      <aside class="wz-panel" ref="panelEl">
        <h2 class="wz-panel-title">人工修改 · 自动存云端</h2>
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
          <button class="ghost" :disabled="locked" @click="step = 1">← 重选素材</button>
        </div>
      </aside>
      <div class="wz-canvas">
        <p class="preview-caption">投递预览 · {{ layout === "classic" ? "中文经典" : "中文现代" }} · 点区块可定位到左侧编辑</p>
        <div class="paper-stage" data-editable @click="onPreviewClick">
          <ResumePreview v-if="tpl" class="print-area" :template="tpl" :fields="fields" :layout="layout" />
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.wizard { padding: 0 0 40px; }
.wizard :deep(:focus-visible) { outline: 3px solid var(--brand); outline-offset: 3px; }
.wiz-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 14px 0; margin-bottom: 14px; border-bottom: 1px solid var(--border); }
.layout-control { display: flex; align-items: center; gap: 8px; font-size: .82rem; color: var(--ink-secondary); }
.layout-control select { padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-surface); color: var(--ink); font: inherit; }
.save-state { margin-right: auto; font-size: .78rem; color: var(--ink-secondary); }
.snapshot-hint { flex-basis: 100%; margin: 0; font-size: .75rem; color: var(--ink-muted); }
.wiz-feedback { background: var(--danger-soft); border: 1px solid var(--danger-border); border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; color: var(--danger); font-size: .82rem; }
.wiz-fields { min-width: 0; border: 0; padding: 0; margin: 0; }
.section-nav { margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
.section-nav summary { font-size: .82rem; padding: 6px 0; cursor: pointer; color: var(--ink-secondary); }
.evidence-panel { margin-bottom: 14px; border-bottom: 1px solid var(--border); padding-bottom: 10px; font-size: .82rem; overflow-wrap: anywhere; }
.evidence-panel summary { cursor: pointer; padding: 6px 0; color: var(--ink-secondary); }
.evidence-field { margin-top: 10px; }
.evidence-field ul { margin: 6px 0; padding-left: 20px; }
.evidence-field p { white-space: pre-wrap; margin: 6px 0; }
.evidence-field span { display: block; color: var(--ink-secondary); font-size: .75rem; }
.preview-caption { margin: 0 0 10px; font-size: .75rem; color: var(--ink-muted); }
.mobile-switch { display: none; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.wizard .wiz-steps .wiz-lib { margin-left: auto; color: var(--brand); }
.wiz-steps { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.wizard .wiz-steps button {
  border: 1px solid var(--border); background: var(--bg-surface); color: var(--ink-secondary);
  border-radius: var(--radius-pill); padding: 6px 14px; font-size: .82rem; cursor: pointer; font-family: inherit;
}
.wiz-steps button.on { border-color: var(--brand); background: var(--brand); color: #fff; }
.wiz-hint { font-size: .78rem; color: var(--ink-muted); margin: 4px 0 12px; }

/* ① 素材选择 */
.src-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 18px; }
.src-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md);
  padding: 14px 16px; box-shadow: var(--shadow-xs);
}
.src-card-title { font-size: .85rem; font-weight: 650; margin-bottom: 8px; }
.src-item {
  display: flex; align-items: center; gap: 8px; padding: 7px 0;
  font-size: .84rem; color: var(--ink); cursor: pointer;
}
.src-item + .src-item { border-top: 1px dashed var(--border-subtle); }
.src-item-name { flex: 1; min-width: 0; }
.src-item-meta { font-size: .7rem; color: var(--ink-muted); }
.wiz-generate { margin: 16px 0; }
.gen-btn {
  border: none; border-radius: 10px; padding: 12px 32px; cursor: pointer;
  background: var(--brand); color: #fff; font: inherit; font-weight: 650; font-size: .95rem;
}
.gen-btn:disabled { opacity: .55; cursor: default; }
.pipe-stages { list-style: none; margin: 14px 0 0; padding: 0; }
.pipe-stages li {
  display: flex; align-items: center; gap: 8px; padding: 7px 0;
  font-size: .84rem; color: var(--ink-muted);
}
.pipe-stages li[data-st="running"] { color: var(--brand); }
.pipe-stages li[data-st="done"] { color: var(--agent); }
.pipe-stages li[data-st="skip"] { color: var(--coach); }
.pipe-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--border); flex: none;
}
li[data-st="running"] .pipe-dot { background: var(--brand); animation: pulse 1s infinite alternate; }
li[data-st="done"] .pipe-dot { background: var(--agent); }
li[data-st="skip"] .pipe-dot { background: var(--coach); }
@keyframes pulse { from { opacity: .4; } to { opacity: 1; } }
.pipe-st { font-size: .75rem; }

/* 双栏工作台 */
.wiz-work { display: grid; grid-template-columns: minmax(300px, .85fr) minmax(0, 1.4fr); gap: 24px; align-items: start; }
.wz-nav { display: grid; gap: 6px; padding-top: 8px; }
.wz-nav-item {
  display: flex; align-items: center; justify-content: space-between; gap: 6px;
  padding: 9px 12px; border: 1px solid var(--border); border-radius: 10px;
  background: var(--bg-surface); font-size: .85rem; color: var(--ink); cursor: pointer; text-align: left;
  font-family: inherit;
}
.wz-nav-item:hover { border-color: var(--brand-border); }
.wz-nav-item.on { border-color: var(--brand); background: var(--brand-soft); }
.wz-nav-item.gap { border-color: var(--coach-border); }
.paper-stage[data-editable] :deep(.resume-section) { cursor: pointer; border-radius: 4px; transition: background var(--dur-fast) var(--ease); }
.paper-stage[data-editable] :deep(.resume-section:hover) { background: #f0f6ff; outline: 1.5px dashed var(--brand); outline-offset: 3px; }
.wz-nav-badge { flex: none; font-size: .68rem; color: var(--agent); }
.wz-nav-badge.warn { color: var(--coach); }
.wz-nav-badge.dim { color: var(--ink-muted); }
.wz-canvas { min-width: 0; position: sticky; top: 70px; max-height: calc(100vh - 96px); overflow: auto; }
.paper-stage {
  border: 1px dashed var(--border-strong); border-radius: 10px; padding: 26px;
  background: var(--bg-subtle);
}
.paper-stage :deep(.print-area) { box-shadow: var(--shadow-paper); }
.wz-panel {
  min-width: 0; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 18px;
  box-shadow: var(--shadow-xs);
}
.wz-panel-title { margin: 0 0 10px; font-size: .9rem; color: var(--brand); font-weight: 650; }
.row { display: flex; gap: 8px; flex-wrap: wrap; }
.row button, .ghost {
  border: 1px solid var(--border-strong); background: var(--bg-surface); border-radius: 8px;
  padding: 7px 14px; font-size: 13px; cursor: pointer; font-family: inherit; color: var(--ink);
}
.wz-actions { margin-top: 14px; }
.primary { border-color: var(--brand) !important; background: var(--brand) !important; color: #fff !important; }

@media (max-width: 760px) {
  .src-grid { grid-template-columns: 1fr; }
  .wiz-work { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .wz-canvas { position: static; max-height: none; }
  .mobile-switch { display: flex; gap: 8px; margin-bottom: 14px; }
  .mobile-switch [aria-pressed="true"] { background: var(--brand); color: #fff; }
  .wiz-work[data-panel="edit"] .wz-canvas, .wiz-work[data-panel="preview"] .wz-panel { display: none; }
  .wz-panel { padding: 14px; }
  .paper-stage { padding: 14px 8px; }
  .save-state { width: 100%; order: 1; }
  .snapshot-hint { order: 2; }
}
@media print {
  .wiz-steps, .wiz-toolbar, .wiz-feedback, .mobile-switch, .wz-panel, .preview-caption, .evidence-panel { display: none !important; }
  .wiz-work, .wz-canvas { display: block !important; }
  .paper-stage { border: none; padding: 0; background: none; }
}
</style>
