<script setup>
// v3 #resume/library：岗位简历库列表 + 新建 + 文档编辑（事实库驱动）
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
import { downloadResumePdf } from "../utils/resumeDownload";
import { exportIssues } from "../../../backend/resume_assets/render.mjs";
import { getTemplate, optimizePlan, optimizeSection } from "../api/resume";
import {
  createResume, deleteResume, duplicateResume, getResume,
  listResumes, exportResumePdf,
  listVersions, patchResume, regenerateResume, restoreVersion,
} from "../api/v3";

const props = defineProps({ docId: { type: String, default: "" } });
const emit = defineEmits(["goto-memory", "nav"]);

const tpl = ref(null);
const docs = ref([]);
const doc = ref(null);              // 当前打开文档（详情态）
const fields = ref({});
const errMsg = ref("");
const busy = ref(false);
const photoBusy = ref(false);
const photoContext = ref(0);
let photoRequest = null;
function onPhotoBusy(event) {
  if (event.context !== photoContext.value) return;
  if (event.busy) { photoRequest = event.request; photoBusy.value = true; }
  else if (event.request === photoRequest) { photoRequest = null; photoBusy.value = false; }
}
// v5：Bearer 归属下凭据天然存在；仅当服务端返回 404（记忆未开/已停）时落为 false。
const hasToken = ref(true);
const creating = ref(false);
const newDoc = ref({ title: "", target_role: "", jd_text: "" });
const editorTab = ref("edit");       // 左侧控制台：edit | coach | evidence（v-show 切换，SSR 保活 DOM）
const editorEl = ref(null);          // .lib-editor 容器引用（模块锚点跳转）
const libSections = computed(() =>
  (tpl.value?.sections || []).slice().sort((a, b) => a.order - b.order));
function scrollToSection(key) {
  editorEl.value?.querySelector(`.rff-sec[data-key="${key}"]`)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}
const regenRole = ref("");
const regenJd = ref("");
const keepManual = ref(true);
const staleList = computed(() => doc.value?.stale_sources || []);
const savedFields = ref("{}");
const dirty = computed(() => !!doc.value && JSON.stringify(fields.value) !== savedFields.value);
const saving = ref(false);
const exporting = ref(false);
const layout = ref("classic");
const mobilePanel = ref("edit");
const exportProblems = ref([]);
const saveState = computed(() => photoBusy.value ? "照片处理中…" : saving.value ? "保存中…" : optFields.value ? "优化稿待采纳" : dirty.value ? "有未保存修改" : "已保存");
const cloneFields = (value) => JSON.parse(JSON.stringify(value || {}));
const evidenceRows = computed(() => Object.entries(doc.value?.evidence_map || {})
  .map(([path, sources]) => ({ path, sources: Array.isArray(sources) ? sources.filter((s) => s && typeof s === "object") : [] }))
  .filter((row) => row.sources.length));

// docId 相同也可能是重新进入；每次加载/离开都换代，不能仅比较 id。
const loading = ref(false);
let docGeneration = 0;
const captureContext = () => ({ id: props.docId, generation: docGeneration });
const isCurrent = (context) => context.generation === docGeneration && context.id === props.docId;
const isCurrentDocument = (context) => isCurrent(context) && !loading.value && !!doc.value && doc.value.id === context.id;
onBeforeUnmount(() => { photoContext.value = ++docGeneration; });

function clearOptimization() {
  optFields.value = null; optPlan.value = null; optResults.value = {};
  errMsg.value = "";
  exportProblems.value = [];
}

function resetDocument() {
  photoBusy.value = false; photoRequest = null;
  doc.value = null; fields.value = {}; savedFields.value = "{}";
  versions.value = [];
  regenRole.value = ""; regenJd.value = ""; keepManual.value = true;
  mobilePanel.value = "edit";
  editorTab.value = "edit";
  busy.value = false; saving.value = false; exporting.value = false; optBusy.value = false;
  clearOptimization();
}

function acceptDocument(document) {
  doc.value = document;
  fields.value = cloneFields(document.fields);
  savedFields.value = JSON.stringify(fields.value);
  clearOptimization();
}

// —— 优化（复用 v2 黑箱逐段优化 + diff 视图）——
const optBusy = ref(false);
const optPlan = ref(null);
const optResults = ref({});
const optFields = ref(null);
const placeholders = computed(() =>
  Object.values(optResults.value).flatMap((r) => r.placeholders || []));

async function onOptimize() {
  const context = captureContext();
  if (!tpl.value || !isCurrentDocument(context) || busy.value || optBusy.value || photoBusy.value) return;
  const snapshot = cloneFields(fields.value);
  const ver = doc.value.template_version || tpl.value.template_version;
  optBusy.value = true;
  editorTab.value = "coach";   // 点智能优化即切到 AI 优化教练 Tab 展示进度与结果
  clearOptimization();
  try {
    const plan = await optimizePlan({ template_version: ver, fields: snapshot });
    if (!isCurrentDocument(context)) return;
    optPlan.value = plan;
    optFields.value = cloneFields(snapshot);
    for (const s of optPlan.value.sections || []) {
      if (!isCurrentDocument(context)) return;
      if (!(s.optimizable && s.status === "pending")) continue;
      s.status = "running";
      try {
        const r = await optimizeSection({
          template_version: ver, section: s.key, fields: snapshot });
        if (!isCurrentDocument(context)) return;
        optResults.value = { ...optResults.value, [s.key]: r };
        if (r.status === "done" && r.optimized != null) {
          optFields.value = { ...optFields.value, [s.key]: r.optimized };
        }
        s.status = r.status === "error" ? "error" : "done";
      } catch (e) {
        if (!isCurrentDocument(context)) return;
        optResults.value = { ...optResults.value, [s.key]: {
          status: "error", change_log: [], placeholders: [], error: e.message } };
        s.status = "error";
      }
    }
  } catch (e) {
    if (isCurrentDocument(context)) errMsg.value = `优化失败：${e.message}`;
  } finally {
    if (isCurrent(context)) optBusy.value = false;
  }
}

function adoptOptimized() {
  if (!isCurrentDocument(captureContext()) || !optFields.value || busy.value || optBusy.value || photoBusy.value) return;
  fields.value = cloneFields(optFields.value);   // → dirty → PATCH 保存
  clearOptimization();
}

function discardOptimized() {
  if (!isCurrentDocument(captureContext()) || busy.value || optBusy.value || photoBusy.value) return;
  // 不触碰 fields/savedFields，也不提交服务端：保留优化前的原稿与脏状态。
  clearOptimization();
}

async function loadTpl() {
  try { tpl.value = await getTemplate(); }
  catch {
    if (import.meta.env.DEV) {
      tpl.value = await fetch("/demo/v2/template.json").then((r) => r.json());
    }
  }
}

async function loadList() {
  const context = captureContext();
  if (!context.id) errMsg.value = "";
  try {
    const res = await listResumes();
    if (isCurrent(context)) docs.value = res.documents || [];
  } catch (e) {
    if (!isCurrent(context) || context.id) return;
    if (e.status === 404) { hasToken.value = false; return; }
    if (import.meta.env.DEV) {
      try {
        const demo = await fetch("/demo/v3/resume-list.json").then((r) => r.json());
        if (isCurrent(context)) docs.value = demo.documents || [];
      } catch {
        if (isCurrent(context)) errMsg.value = "简历库暂时不可用";
      }
      return;
    }
    errMsg.value = "简历库暂时不可用";
  }
}

async function openDoc(id) {
  emit("nav", `#/resume/library/${id}`);
}

async function loadDoc(id) {
  if (id !== props.docId) return;
  photoContext.value = ++docGeneration;
  const context = captureContext();
  resetDocument();
  loading.value = !!id;
  if (!id) return;
  try {
    const document = await getResume(id);
    if (!isCurrent(context)) return;
    acceptDocument(document);
    loadVersions(context);
  } catch (e) {
    if (!isCurrent(context)) return;
    if (e.status === 404) errMsg.value = "文档不存在或已删除";
    else if (import.meta.env.DEV) {
      try {
        const demo = await fetch("/demo/v3/resume-detail.json").then((r) => r.json());
        if (isCurrent(context)) acceptDocument({ ...demo, id });
      } catch {
        if (isCurrent(context)) errMsg.value = "文档加载失败";
      }
    } else errMsg.value = "文档加载失败";
  } finally {
    if (isCurrent(context)) loading.value = false;
  }
}

async function onCreate() {
  const context = captureContext();
  if (context.id || busy.value) return;
  if (!newDoc.value.title.trim()) { errMsg.value = "请填写简历标题"; return; }
  busy.value = true;
  try {
    const res = await createResume({
      title: newDoc.value.title.trim(),
      target_role: newDoc.value.target_role.trim() || undefined,
      jd_text: newDoc.value.jd_text.trim() || undefined,
      template_version: tpl.value?.template_version,
    });
    if (!isCurrent(context)) return;
    creating.value = false;
    newDoc.value = { title: "", target_role: "", jd_text: "" };
    openDoc(res.document.id);
  } catch (e) {
    if (isCurrent(context)) errMsg.value = e.status === 503 ? "简历生成服务暂时不可用" : "创建失败";
  } finally {
    if (isCurrent(context)) busy.value = false;
  }
}

// 调用者持有 busy 锁；提交独立快照，晚到的响应不覆盖请求期间的新编辑。
async function persistFields(context) {
  if (!isCurrentDocument(context)) return false;
  const current = doc.value;
  const snapshot = JSON.stringify(fields.value);
  saving.value = true;
  try {
    const res = await patchResume(current.id, { fields: JSON.parse(snapshot) }, current.version);
    if (!isCurrentDocument(context)) return false;
    doc.value = res.document;
    savedFields.value = JSON.stringify(res.document.fields || {});
    if (JSON.stringify(fields.value) === snapshot) fields.value = cloneFields(res.document.fields);
    return true;
  } catch (e) {
    if (isCurrentDocument(context)) errMsg.value = e.status === 409 ? "版本冲突，请刷新后重试" : `保存失败：${e.message || "请重试"}`;
    return false;
  } finally {
    if (isCurrent(context)) saving.value = false;
  }
}

async function onSave() {
  const context = captureContext();
  if (!isCurrentDocument(context) || busy.value || optBusy.value || photoBusy.value || !dirty.value) return false;
  busy.value = true;
  errMsg.value = "";
  try {
    return await persistFields(context);
  } finally {
    if (isCurrent(context)) busy.value = false;
  }
}

async function onRegenerate() {
  const context = captureContext();
  if (!isCurrentDocument(context) || busy.value || optBusy.value || photoBusy.value) return;
  errMsg.value = "";
  exportProblems.value = [];
  if (optFields.value) {
    errMsg.value = "请先采纳或放弃优化稿，再重新生成。";
    mobilePanel.value = "edit";
    return;
  }
  if (staleList.value.length &&
      !confirm(`有 ${staleList.value.length} 处来源已变更，重新生成会以最新事实重建草稿。继续？`)) return;
  busy.value = true;
  try {
    if (dirty.value && !await persistFields(context)) return;
    if (!isCurrentDocument(context)) return;
    if (dirty.value) {
      errMsg.value = "仍有未保存修改，请保存后重新生成。";
      return;
    }
    const res = await regenerateResume(context.id, {
      target_role: regenRole.value.trim() || undefined,
      jd_text: regenJd.value.trim() || undefined,
      keep_manual: keepManual.value,
    });
    if (!isCurrentDocument(context)) return;
    acceptDocument(res.document);
    loadVersions(context);
  } catch (e) {
    if (isCurrentDocument(context)) errMsg.value = e.status === 503 ? "生成服务暂时不可用" : "重新生成失败";
  } finally {
    if (isCurrent(context)) busy.value = false;
  }
}

const versions = ref([]);
let versionsRequest = 0;

async function loadVersions(context) {
  if (!isCurrent(context)) return;
  const request = ++versionsRequest;
  try {
    const res = await listVersions(context.id);
    if (isCurrent(context) && request === versionsRequest) versions.value = res.versions || [];
  } catch {
    if (isCurrent(context) && request === versionsRequest) versions.value = [];
  }
}

async function onRestore(v) {
  const context = captureContext();
  if (!isCurrentDocument(context) || busy.value || optBusy.value || photoBusy.value) return;
  errMsg.value = "";
  exportProblems.value = [];
  if (optFields.value) {
    errMsg.value = "请先采纳或放弃优化稿，再恢复历史版本。";
    mobilePanel.value = "edit";
    return;
  }
  // Saving here could prune the selected target from the ten-version history.
  if (dirty.value) {
    errMsg.value = "有未保存修改，暂不能恢复历史版本。请先处理当前修改；历史仅保留最近 10 版，保存后请刷新版本列表再选择。";
    mobilePanel.value = "edit";
    return;
  }
  if (!confirm(`恢复到 v${v.version} 的快照？当前状态会先存入历史，可再回退。`)) return;
  busy.value = true;
  try {
    const res = await restoreVersion(context.id, v.version);
    if (!isCurrentDocument(context)) return;
    acceptDocument(res.document);
    loadVersions(context);
  } catch (e) {
    if (isCurrentDocument(context)) errMsg.value = e.status === 404 ? "该历史版本不存在" : "恢复失败，请重试";
  } finally {
    if (isCurrent(context)) busy.value = false;
  }
}

async function onExport() {
  const context = captureContext();
  if (!isCurrentDocument(context) || !tpl.value || busy.value || optBusy.value || photoBusy.value) return;
  errMsg.value = "";
  exportProblems.value = [];
  if (optFields.value) {
    errMsg.value = "请先采纳优化稿，或放弃优化稿后导出原稿；不会静默导出旧稿。";
    mobilePanel.value = "edit";
    return;
  }
  busy.value = true;
  exporting.value = true;
  const selectedLayout = layout.value;
  try {
    exportProblems.value = exportIssues(tpl.value, fields.value);
    if (exportProblems.value.length) {
      errMsg.value = "请先补全或修正以下内容，再导出 PDF。";
      return;
    }
    if (dirty.value && !await persistFields(context)) return;
    if (!isCurrentDocument(context)) return;
    if (dirty.value) {
      errMsg.value = "仍有未保存修改，请保存后重新导出。";
      return;
    }
    exportProblems.value = exportIssues(tpl.value, fields.value);
    if (exportProblems.value.length) {
      errMsg.value = "请先补全或修正以下内容，再导出 PDF。";
      return;
    }
    const current = doc.value;
    const blob = await exportResumePdf(current.id, { version: current.version, layout: selectedLayout });
    if (!isCurrentDocument(context)) return;
    downloadResumePdf(blob, current.title || "简历");
  } catch (e) {
    if (!isCurrentDocument(context)) return;
    errMsg.value = e.message || "PDF 导出失败，请重试";
    exportProblems.value = e.issues || e.data?.detail?.issues || [];
  } finally {
    if (isCurrent(context)) {
      busy.value = false;
      exporting.value = false;
    }
  }
}

async function onDuplicate() {
  const context = captureContext();
  if (!isCurrentDocument(context) || busy.value || optBusy.value || photoBusy.value) return;
  errMsg.value = "";
  exportProblems.value = [];
  if (optFields.value) {
    errMsg.value = "请先采纳或放弃优化稿，再复制为新稿；不会静默复制旧稿。";
    mobilePanel.value = "edit";
    return;
  }
  busy.value = true;
  try {
    if (dirty.value && !await persistFields(context)) return;
    if (!isCurrentDocument(context)) return;
    if (dirty.value) {
      errMsg.value = "仍有未保存修改，请保存后重新复制。";
      return;
    }
    const res = await duplicateResume(context.id);
    if (isCurrentDocument(context)) openDoc(res.document.id);
  } catch {
    if (isCurrentDocument(context)) errMsg.value = "复制失败，请重试";
  } finally {
    if (isCurrent(context)) busy.value = false;
  }
}

async function onDelete() {
  const context = captureContext();
  if (!isCurrentDocument(context) || busy.value || optBusy.value || photoBusy.value || !confirm(`删除「${doc.value.title}」？此操作不可恢复。`)) return;
  busy.value = true;
  try {
    await deleteResume(context.id);
    if (isCurrentDocument(context)) emit("nav", "#/resume/library");
  } catch {
    if (isCurrentDocument(context)) errMsg.value = "删除失败";
  } finally {
    if (isCurrent(context)) busy.value = false;
  }
}

// 同步换代：即使 A→列表→A 在同一轮发生，旧请求也不能复活。
watch(() => props.docId, (id) => {
  loadDoc(id);
  if (!id && hasToken.value) loadList();
}, { flush: "sync" });

onMounted(() => {
  // 不等模板/列表请求结束后再加载，避免启动流程对新路由重复加载。
  loadDoc(props.docId);
  loadTpl();
  if (hasToken.value) loadList();
});
</script>

<template>
  <div class="lib-page">
    <div v-if="!hasToken" class="lib-empty">
      <b>简历库需要长期记忆提供事实来源</b>
      <p>完成一次访谈即可自动建档，或先去记忆页手动开启。</p>
      <button class="btn-primary" @click="emit('goto-memory')">先去开启记忆</button>
    </div>

    <div v-else-if="props.docId && !doc" class="lib-loading" role="status" aria-live="polite">
      <button class="btn-secondary sm" @click="emit('nav', '#/resume/library')">← 列表</button>
      <p>{{ loading ? "正在加载简历…" : errMsg }}</p>
    </div>

    <template v-else-if="!doc">
      <div class="lib-head">
        <div class="lib-head-main">
          <h2 class="lib-title">岗位简历库</h2>
          <span class="lib-count">{{ docs.length }} 份</span>
        </div>
        <button class="btn-primary" :disabled="busy" @click="creating = true">＋ 新建简历</button>
      </div>

      <div v-if="creating" class="lib-modal-mask" @click.self="creating = false">
        <div class="lib-modal" role="dialog" aria-modal="true" aria-label="新建简历">
          <div class="lib-modal-head">
            <b>新建简历</b>
            <button class="lib-modal-x" aria-label="关闭" @click="creating = false">×</button>
          </div>
          <label for="new-title">简历标题</label>
          <input id="new-title" v-model="newDoc.title" :disabled="busy" placeholder="如：后端开发-暑期实习" maxlength="80" />
          <label for="new-role">目标岗位（可选）</label>
          <input id="new-role" v-model="newDoc.target_role" :disabled="busy" maxlength="80" />
          <label for="new-jd">职位描述（可选）</label>
          <textarea id="new-jd" v-model="newDoc.jd_text" :disabled="busy" rows="3" placeholder="用于相关性排序"></textarea>
          <div class="lib-modal-ops">
            <button class="btn-secondary" @click="creating = false">取消</button>
            <button class="btn-primary" :disabled="busy" @click="onCreate">从事实库生成</button>
          </div>
        </div>
      </div>

      <div v-if="docs.length" class="lib-grid">
        <article v-for="d in docs" :key="d.id" class="lib-card" role="button" tabindex="0"
                 @click="openDoc(d.id)" @keydown.enter="openDoc(d.id)" @keydown.space.prevent="openDoc(d.id)">
          <div class="lc-top">
            <span class="lc-role">{{ d.target_role || "未指定岗位" }}</span>
            <span class="ld-status" :data-s="d.status">
              {{ { draft: "草稿", ready: "就绪", stale: "需更新" }[d.status] || d.status }}
            </span>
          </div>
          <b class="lc-title">{{ d.title }}</b>
          <div class="lc-foot">
            <span class="lc-bar" aria-hidden="true">
              <i :style="{ width: Math.round((Number.isFinite(d.completeness) ? d.completeness : 0) * 100) + '%' }"></i>
            </span>
            <span class="lc-pct">{{ Math.round((Number.isFinite(d.completeness) ? d.completeness : 0) * 100) }}%</span>
            <span class="lc-date">{{ (d.updated_at || "").slice(0, 10) }}</span>
          </div>
        </article>
      </div>
      <p v-else class="lib-hint">还没有简历，点「新建简历」从事实库生成一份。</p>
      <p v-if="errMsg" class="lib-err">{{ errMsg }}</p>
    </template>

    <template v-else>
      <div class="lib-topbar">
        <button class="btn-ghost sm" @click="emit('nav', '#/resume/library')">← 列表</button>
        <b class="lt-title">{{ doc.title }}</b>
        <span class="ld-status" :data-s="doc.status">
          {{ { draft: "草稿", ready: "就绪", stale: "需更新" }[doc.status] || doc.status }}
        </span>
        <label class="layout-control">版式
          <select v-model="layout" :disabled="busy || optBusy || photoBusy">
            <option value="classic">中文经典</option>
            <option value="modern">中文现代</option>
          </select>
        </label>
        <span class="save-state" role="status" aria-live="polite">{{ saveState }}</span>
        <span class="lt-spacer"></span>
        <details v-if="versions.length" class="lv-dropdown">
          <summary>历史版本 · {{ versions.length }} <i class="lv-caret" aria-hidden="true">▾</i></summary>
          <div class="lv-menu">
            <div v-for="v in versions" :key="v.id" class="lv-row">
              <span>v{{ v.version }}</span>
              <button class="lv-restore" :disabled="busy || optBusy || photoBusy" @click="onRestore(v)">恢复</button>
            </div>
            <p class="lv-hint">仅保留最近 10 版</p>
          </div>
        </details>
        <button class="btn-secondary sm" :disabled="busy || optBusy || photoBusy || !dirty" @click="onSave">{{ saving ? "保存中…" : "保存修改" }}</button>
        <button class="btn-secondary sm" :disabled="busy || optBusy || photoBusy" @click="onDuplicate">复制为新稿</button>
        <button class="btn-danger sm" :disabled="busy || optBusy || photoBusy" @click="onDelete">删除</button>
        <button class="btn-primary sm" :disabled="busy || optBusy || photoBusy || !tpl" @click="onExport">{{ exporting ? "导出中…" : "导出 PDF" }}</button>
      </div>

      <div v-if="staleList.length" class="lib-stale">
        {{ staleList.length }} 处来源已更新/删除：
        <span v-for="s in staleList" :key="s.field_path + s.fact_id" class="ls-item">
          {{ s.field_path }}（{{ s.change === "deleted" ? "来源已删" : "来源已变" }}）
        </span>
        —— 建议重新生成
        <button class="stale-link" @click="editorTab = 'evidence'">去证据与定制</button>
      </div>

      <div v-if="errMsg || exportProblems.length" class="lib-feedback" role="alert">
        <p v-if="errMsg" class="lib-err">{{ errMsg }}</p>
        <ul v-if="exportProblems.length">
          <li v-for="(issue, i) in exportProblems" :key="i">{{ issue.label || issue.field_path }}：{{ issue.reason }}</li>
        </ul>
      </div>
      <div class="mobile-switch" aria-label="工作区显示">
        <button class="lib-btn" :aria-pressed="mobilePanel === 'edit'" @click="mobilePanel = 'edit'">编辑</button>
        <button class="lib-btn" :aria-pressed="mobilePanel === 'preview'" @click="mobilePanel = 'preview'">预览</button>
      </div>

      <div class="lib-cols" :data-panel="mobilePanel">
        <aside ref="editorEl" class="lib-editor">
          <nav class="editor-tabs" aria-label="简历编辑">
            <button :aria-pressed="editorTab === 'edit'" :class="{ on: editorTab === 'edit' }" @click="editorTab = 'edit'">内容编辑</button>
            <button :aria-pressed="editorTab === 'coach'" :class="{ on: editorTab === 'coach' }" @click="editorTab = 'coach'">AI 优化教练</button>
            <button :aria-pressed="editorTab === 'evidence'" :class="{ on: editorTab === 'evidence' }" @click="editorTab = 'evidence'">证据与定制</button>
          </nav>

          <div v-show="editorTab === 'edit'" class="etab">
            <nav v-if="libSections?.length" class="sec-pills" aria-label="模块跳转">
              <button v-for="s in libSections" :key="s.key" class="sec-pill" @click="scrollToSection(s.key)">{{ s.title }}</button>
            </nav>
            <div v-if="doc.gaps?.length" class="lib-gaps">{{ doc.gaps.length }} 处待补充</div>
            <fieldset class="lib-fields" :disabled="busy || optBusy || !!optFields" :aria-busy="busy || optBusy || photoBusy">
              <legend class="sr-only">编辑简历字段</legend>
              <ResumeFieldsForm v-if="tpl" v-model="fields" :template="tpl" :gaps="doc.gaps || []"
                                :disabled="busy || optBusy || !!optFields" :context="photoContext" @photo-busy="onPhotoBusy" />
            </fieldset>
          </div>

          <div v-show="editorTab === 'coach'" class="etab">
            <button class="btn-agent coach-run" :disabled="busy || optBusy || photoBusy || !tpl" @click="onOptimize">
              {{ optBusy ? "优化中…" : "✦ 智能优化" }}
            </button>
            <ul v-if="optPlan" class="opt-list">
              <li v-for="s in optPlan.sections" :key="s.key" :data-st="s.status">
                <b>{{ s.title }}</b>
                <span class="st">{{ { pending: "待优化", running: "优化中…", done: "完成", error: "失败", skipped: "跳过", ok: "无需改", check: "人工确认" }[s.status] || s.status }}</span>
                <span v-if="s.reason" class="rs">{{ s.reason }}</span>
              </li>
            </ul>
            <template v-for="(r, key) in optResults" :key="key">
              <div v-if="r.change_log?.length" class="coach-sec">
                <h3 class="coach-sec-title">改动 · {{ (optPlan?.sections || []).find(x => x.key === key)?.title || key }}</h3>
                <div v-for="(c, i) in r.change_log" :key="i" class="coach-diff-card">
                  <p class="diff-before">{{ c.before }}</p>
                  <p class="diff-after">{{ c.after }}</p>
                  <p v-if="c.reason" class="diff-reason">{{ c.reason }}</p>
                </div>
              </div>
              <p v-if="r.error" class="lib-err">{{ (optPlan?.sections || []).find(x => x.key === key)?.title || key }}：{{ r.error }}</p>
            </template>
            <div v-if="optFields" class="lib-adopt" role="status">
              <p>预览为待采纳优化稿。可采纳后保存并导出，或放弃优化稿返回原稿。</p>
              <button class="btn-agent sm" :disabled="busy || optBusy || photoBusy" @click="adoptOptimized">采纳优化稿</button>
              <button class="btn-secondary sm" :disabled="busy || optBusy || photoBusy" @click="discardOptimized">放弃优化稿</button>
            </div>
            <div v-if="placeholders.length" class="coach-ph">
              {{ placeholders.length }} 处待补充占位：{{ placeholders.map((p) => p.field_path).join("、") }}
            </div>
            <p v-if="!optPlan && !Object.keys(optResults).length" class="lib-hint">点「智能优化」，我会逐段给出改动和理由，像教练一样教你怎么改。</p>
          </div>

          <div v-show="editorTab === 'evidence'" class="etab">
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
            <details class="lib-generation">
              <summary>岗位与重新生成</summary>
              <fieldset :disabled="busy || optBusy || photoBusy">
                <label for="regen-role">目标岗位（可选）</label>
                <input id="regen-role" v-model="regenRole" maxlength="80" />
                <label for="regen-jd">职位描述（可选）</label>
                <textarea id="regen-jd" v-model="regenJd" rows="3"></textarea>
                <label><input type="checkbox" v-model="keepManual" /> 保留手工修改</label>
                <button class="btn-primary" :disabled="busy || optBusy || photoBusy" @click="onRegenerate">重新生成</button>
              </fieldset>
            </details>
          </div>
        </aside>
        <div class="lib-preview">
          <p class="preview-caption">{{ optFields ? "待采纳优化稿" : "投递预览" }} · {{ layout === "classic" ? "中文经典" : "中文现代" }}</p>
          <div class="paper-stage">
            <ResumePreview v-if="tpl" class="print-area" :template="tpl" :fields="optFields || fields" :layout="layout" />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lib-page { max-width: 1440px; margin: 0 auto; padding: 0 0 60px; color: var(--ink); }
.lib-page :deep(:focus-visible) { outline: 3px solid var(--brand); outline-offset: 3px; }
.lib-page fieldset { min-width: 0; border: 0; margin: 0; padding: 0; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }

/* —— 列表态：工具条 + 卡片网格 + 居中 Modal —— */
.lib-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 8px 0 18px; }
.lib-head-main { display: flex; align-items: baseline; gap: 10px; }
.lib-title { font-size: 20px; margin: 0; font-weight: 650; }
.lib-count { font-size: 12px; padding: 1px 9px; border-radius: var(--radius-pill); background: var(--bg-subtle); color: var(--ink-secondary); }
.lib-modal-mask {
  position: fixed; inset: 0; z-index: 50; background: rgba(15, 23, 42, .42);
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.lib-modal {
  width: min(420px, 92vw); background: var(--bg-surface); border-radius: var(--radius-md);
  padding: 18px; display: flex; flex-direction: column; gap: 8px; box-shadow: var(--shadow-md);
}
.lib-modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.lib-modal-head b { font-size: 15px; }
.lib-modal-x { border: none; background: none; font-size: 18px; color: var(--ink-muted); cursor: pointer; padding: 2px 6px; border-radius: var(--radius-xs); }
.lib-modal-x:hover { background: var(--bg-hover); color: var(--ink); }
.lib-modal label { font-size: .78rem; color: var(--ink-secondary); }
.lib-modal input, .lib-modal textarea { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 13px; font-family: inherit; background: var(--bg-surface); color: var(--ink); }
.lib-modal input:focus, .lib-modal textarea:focus { outline: none; border-color: var(--brand); }
.lib-modal-ops { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.lib-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.lib-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md);
  padding: 16px; cursor: pointer; display: flex; flex-direction: column; gap: 8px;
  transition: border-color var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease);
}
.lib-card:hover { border-color: var(--brand-border); transform: translateY(-2px); box-shadow: var(--shadow-sm); }
.lc-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.lc-role { font-size: .75rem; color: var(--ink-secondary); background: var(--bg-subtle); padding: 1px 9px; border-radius: var(--radius-pill); }
.lc-title { font-size: .95rem; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lc-foot { display: flex; align-items: center; gap: 8px; font-size: .72rem; color: var(--ink-muted); }
.lc-bar { flex: 1; height: 5px; border-radius: 3px; background: var(--bg-subtle); overflow: hidden; }
.lc-bar i { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--agent), var(--brand)); transition: width var(--dur-base) var(--ease); }
.lc-pct { font-variant-numeric: tabular-nums; }
.lc-date { margin-left: auto; }
.ld-status { font-size: .72rem; font-style: normal; padding: 1px 9px; border-radius: var(--radius-pill); }
.ld-status[data-s="draft"] { background: var(--coach-soft); color: var(--coach); border: 1px solid var(--coach-border); }
.ld-status[data-s="ready"] { background: var(--agent-soft); color: var(--agent); border: 1px solid var(--agent-border); }
.ld-status[data-s="stale"] { background: #ffedd5; color: #c2410c; border: 1px solid #fdba74; }
.lib-empty { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 32px 24px; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.lib-empty p { color: var(--ink-secondary); font-size: .88rem; margin: 0; }
.lib-loading { color: var(--ink-secondary); font-size: .88rem; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.lib-hint { color: var(--ink-muted); font-size: 13px; }
.lib-err { color: var(--danger); font-size: 13px; }

/* —— 详情态：56px 吸顶操作栏 —— */
.lib-topbar {
  position: sticky; top: 0; z-index: 15; min-height: 56px;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 0; margin-bottom: 14px;
  background: var(--bg-canvas); border-bottom: 1px solid var(--border);
}
.lt-title { font-size: .95rem; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
.lt-spacer { flex: 1; }
.layout-control { display: flex; gap: 6px; align-items: center; white-space: nowrap; font-size: .8rem; color: var(--ink-secondary); }
.layout-control select { border: 1px solid var(--border); border-radius: 8px; padding: 5px 8px; background: var(--bg-surface); font: inherit; font-size: .82rem; color: var(--ink); }
.save-state { font-size: .75rem; color: var(--ink-muted); }
.lv-dropdown { position: relative; }
.lv-dropdown summary {
  list-style: none; cursor: pointer; font-size: .8rem; color: var(--ink-secondary);
  padding: 6px 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--bg-surface);
  display: inline-flex; align-items: center; gap: 4px; user-select: none;
}
.lv-dropdown summary::-webkit-details-marker { display: none; }
.lv-dropdown summary:hover { background: var(--bg-subtle); }
.lv-dropdown[open] summary { border-color: var(--brand-border); color: var(--brand); }
.lv-caret { font-style: normal; font-size: .68rem; }
.lv-menu {
  position: absolute; right: 0; top: calc(100% + 4px); z-index: 20; min-width: 180px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
  box-shadow: var(--shadow-md); padding: 6px;
}
.lv-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 5px 8px; font-size: .82rem; border-radius: var(--radius-xs); }
.lv-row:hover { background: var(--bg-subtle); }
.lv-restore { border: none; background: none; color: var(--brand); cursor: pointer; font-size: .78rem; padding: 0 0 0 6px; }
.lv-restore:hover { text-decoration: underline; }
.lv-hint { margin: 4px 8px 2px; font-size: .68rem; color: var(--ink-muted); }
.lib-stale {
  background: var(--coach-soft); border: 1px solid var(--coach-border); border-radius: 8px;
  padding: 10px 12px; font-size: 13px; color: var(--coach); margin-bottom: 12px;
}
.ls-item { font-family: monospace; font-size: .82rem; }
.stale-link { border: none; background: none; color: var(--brand); cursor: pointer; font-size: .8rem; text-decoration: underline; padding: 0 0 0 6px; }
.lib-feedback { padding: 10px 14px; border: 1px solid var(--danger-border); border-radius: 8px; margin: 12px 0; background: var(--danger-soft); font-size: 13px; color: var(--danger); }
.lib-gaps { font-size: .78rem; color: var(--coach); margin-bottom: 10px; }
.mobile-switch { display: none; }

/* —— 左侧控制台：三 Tab（v-show 保活）—— */
.lib-cols { display: grid; grid-template-columns: minmax(320px, 0.9fr) minmax(0, 1.35fr); gap: 24px; align-items: start; }
.lib-editor { min-width: 0; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 14px 16px 18px; box-shadow: var(--shadow-xs); }
.editor-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin: 0 -6px 14px; padding: 0 6px; }
.editor-tabs button {
  border: none; background: none; cursor: pointer; font: inherit; font-size: .84rem;
  color: var(--ink-secondary); padding: 8px 10px; position: relative;
}
.editor-tabs button:hover { color: var(--ink); }
.editor-tabs button.on { color: var(--brand); font-weight: 600; }
.editor-tabs button.on::after { content: ""; position: absolute; left: 8px; right: 8px; bottom: -1px; height: 2px; border-radius: 1px; background: var(--brand); }
.etab { min-width: 0; }
.sec-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.sec-pill {
  border: 1px solid var(--border); background: var(--bg-surface); color: var(--ink-secondary);
  border-radius: var(--radius-pill); padding: 3px 10px; font-size: .75rem; cursor: pointer; font-family: inherit;
}
.sec-pill:hover { border-color: var(--brand-border); color: var(--brand); background: var(--brand-soft); }
.lib-fields { margin-top: 4px !important; }

/* —— AI 优化教练：diff 教学卡 —— */
.coach-run { width: 100%; justify-content: center; margin-bottom: 10px; }
.opt-list { list-style: none; margin: 0 0 10px; padding: 0; font-size: .8rem; }
.opt-list li { display: flex; align-items: baseline; gap: 8px; padding: 5px 0; border-bottom: 1px dashed var(--border); flex-wrap: wrap; }
.opt-list li:last-child { border-bottom: none; }
.opt-list .st { font-size: .72rem; color: var(--ink-muted); }
.opt-list li[data-st="done"] .st, .opt-list li[data-st="ok"] .st { color: var(--agent); }
.opt-list li[data-st="running"] .st { color: var(--brand); }
.opt-list li[data-st="error"] .st { color: var(--danger); }
.opt-list .rs { width: 100%; font-size: .72rem; color: var(--ink-muted); }
.coach-sec { margin-top: 10px; }
.coach-sec-title { font-size: .8rem; font-weight: 650; margin: 0 0 8px; color: var(--ink-secondary); }
.coach-diff-card {
  border: 1px solid var(--coach-border); background: var(--coach-soft);
  border-radius: 8px; padding: 9px 12px; margin-bottom: 8px;
}
.coach-diff-card p { margin: 0; }
.diff-before { color: var(--ink-muted); font-size: .78rem; text-decoration: line-through; }
.diff-after { color: var(--agent); font-size: .85rem; margin-top: 3px !important; }
.diff-reason { color: var(--ink-secondary); font-size: .75rem; margin-top: 5px !important; border-top: 1px dashed var(--coach-border); padding-top: 5px; }
.coach-ph { font-size: .78rem; color: var(--coach); margin: 8px 0; }
.lib-adopt {
  padding: 10px 14px; border: 1px solid var(--agent-border); border-radius: 8px;
  margin: 12px 0; background: var(--agent-soft); font-size: .82rem; color: var(--ink-secondary);
}
.lib-adopt .btn-agent, .lib-adopt .btn-secondary { margin: 6px 8px 0 0; }

/* —— 证据与定制 —— */
.evidence-panel { margin-bottom: 14px; border-bottom: 1px solid var(--border); padding-bottom: 10px; font-size: 13px; overflow-wrap: anywhere; }
.evidence-panel summary { cursor: pointer; padding: 6px 0; color: var(--ink-secondary); }
.evidence-field { margin-top: 10px; }
.evidence-field ul { margin: 6px 0; padding-left: 20px; }
.evidence-field p { white-space: pre-wrap; margin: 6px 0; }
.evidence-field span { display: block; color: var(--ink-secondary); font-size: 12px; }
.lib-generation { border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 14px; font-size: 13px; }
.lib-generation summary { cursor: pointer; padding: 6px 0; color: var(--ink-secondary); }
.lib-generation fieldset { display: grid; gap: 8px; padding-top: 10px; }
.lib-generation input:not([type]), .lib-generation textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; background: var(--bg-surface); font: inherit; color: var(--ink); }

/* —— 右侧 A4 纸张舞台 —— */
.lib-preview { min-width: 0; position: sticky; top: 70px; max-height: calc(100vh - 96px); overflow: auto; }
.preview-caption { margin: 0 0 10px; font-size: .75rem; color: var(--ink-muted); }
.paper-stage {
  border: 1px dashed var(--border-strong); border-radius: 10px; padding: 26px;
  background: var(--bg-subtle); box-shadow: inset 0 0 0 1px var(--bg-surface);
}
.paper-stage :deep(.print-area) { box-shadow: var(--shadow-paper); }

@media (max-width: 760px) {
  .lib-topbar { position: static; min-height: 0; }
  .lib-cols { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .lib-preview { position: static; max-height: none; }
  .mobile-switch { display: flex; gap: 8px; margin: 14px 0; }
  .mobile-switch .lib-btn {
    flex: 1; border: 1px solid var(--border); background: var(--bg-surface); color: var(--ink-secondary);
    border-radius: var(--radius-sm); padding: 8px; font-size: .84rem; cursor: pointer; font-family: inherit;
  }
  .mobile-switch [aria-pressed="true"] { background: var(--brand); border-color: var(--brand); color: #fff; }
  .lib-cols[data-panel="edit"] .lib-preview, .lib-cols[data-panel="preview"] .lib-editor { display: none; }
  .lib-editor { padding: 12px; }
  .paper-stage { padding: 14px 8px; }
  .lt-title { max-width: 140px; }
}
/* 库详情导出：只出预览稿，隐藏列表/表单/操作区 */
@media print {
  .lib-head, .lib-title, .lib-grid, .lib-modal-mask, .lib-empty, .lib-loading, .lib-stale, .lib-gaps,
  .lib-topbar, .lib-feedback, .mobile-switch, .preview-caption,
  .lib-editor, .evidence-panel { display: none !important; }
  .lib-cols, .lib-preview { display: block !important; }
  .paper-stage { border: none; padding: 0; background: none; box-shadow: none; }
}
</style>
