<script setup>
// v3 #resume/library：岗位简历库列表 + 新建 + 文档编辑（事实库驱动）
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
import { downloadResumePdf } from "../utils/resumeDownload";
import { exportIssues } from "../../../backend/resume_assets/render.mjs";
import { getTemplate, optimizePlan, optimizeSection } from "../api/resume";
import {
  createResume, deleteResume, duplicateResume, getOwnerToken, getResume,
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
const hasToken = ref(!!getOwnerToken());
const creating = ref(false);
const newDoc = ref({ title: "", target_role: "", jd_text: "" });
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
    <h2 class="lib-title">岗位简历库</h2>

    <div v-if="!hasToken" class="lib-empty">
      <p>简历库需要长期记忆提供事实来源。</p>
      <button class="lib-btn primary" @click="emit('goto-memory')">
        先去开启记忆
      </button>
    </div>

    <div v-else-if="props.docId && !doc" class="lib-loading" role="status" aria-live="polite">
      <button class="lib-btn" @click="emit('nav', '#/resume/library')">← 列表</button>
      <p>{{ loading ? "正在加载简历…" : errMsg }}</p>
    </div>

    <template v-else-if="!doc">
      <div class="lib-bar">
        <button class="lib-btn primary" @click="creating = !creating">
          {{ creating ? "收起" : "＋ 新建简历" }}
        </button>
      </div>

      <div v-if="creating" class="lib-new">
        <label for="new-title">简历标题</label>
        <input id="new-title" v-model="newDoc.title" :disabled="busy" placeholder="如：后端开发-暑期实习" maxlength="80" />
        <label for="new-role">目标岗位（可选）</label>
        <input id="new-role" v-model="newDoc.target_role" :disabled="busy" maxlength="80" />
        <label for="new-jd">职位描述（可选）</label>
        <textarea id="new-jd" v-model="newDoc.jd_text" :disabled="busy" rows="3" placeholder="用于相关性排序" />
        <button class="lib-btn primary" :disabled="busy" @click="onCreate">
          从事实库生成
        </button>
      </div>

      <ul class="lib-list">
        <li v-for="d in docs" :key="d.id" class="lib-doc" role="button" tabindex="0"
            @click="openDoc(d.id)" @keydown.enter="openDoc(d.id)" @keydown.space.prevent="openDoc(d.id)">
          <div class="ld-main">
            <b>{{ d.title }}</b>
            <span class="ld-role">{{ d.target_role || "未指定岗位" }}</span>
          </div>
          <div class="ld-meta">
            <span class="ld-status" :data-s="d.status">{{ d.status }}</span>
            <span>完成度 {{ Math.round((Number.isFinite(d.completeness) ? d.completeness : 0) * 100) }}%</span>
            <span>{{ (d.updated_at || "").slice(0, 10) }}</span>
          </div>
        </li>
      </ul>
      <p v-if="!docs.length" class="lib-hint">还没有简历，点「新建简历」从事实库生成一份。</p>
      <p v-if="errMsg" class="lib-err">{{ errMsg }}</p>
    </template>

    <template v-else>
      <div class="lib-detail-head">
        <button class="lib-btn" @click="emit('nav', '#/resume/library')">← 列表</button>
        <b>{{ doc.title }}</b>
        <span class="ld-role">{{ doc.target_role || "未指定岗位" }}</span>
        <span class="ld-status" :data-s="doc.status">{{ doc.status }}</span>
      </div>

      <div v-if="staleList.length" class="lib-stale">
        {{ staleList.length }} 处来源已更新/删除：
        <span v-for="s in staleList" :key="s.field_path + s.fact_id" class="ls-item">
          {{ s.field_path }}（{{ s.change === "deleted" ? "来源已删" : "来源已变" }}）
        </span>
        —— 建议重新生成
      </div>

      <div class="lib-detail-ops">
        <label class="layout-control">版式
          <select v-model="layout" :disabled="busy || optBusy || photoBusy">
            <option value="classic">中文经典</option>
            <option value="modern">中文现代</option>
          </select>
        </label>
        <span class="save-state" role="status" aria-live="polite">{{ saveState }}</span>
        <button class="lib-btn" :disabled="busy || optBusy || photoBusy || !dirty" @click="onSave">
          {{ saving ? "保存中…" : "保存修改" }}
        </button>
        <button class="lib-btn primary" :disabled="busy || optBusy || photoBusy || !tpl" @click="onExport">
          {{ exporting ? "导出中…" : "导出 PDF" }}
        </button>
        <button class="lib-btn" :disabled="busy || optBusy || photoBusy" @click="onDuplicate">复制为新稿</button>
        <button class="lib-btn danger" :disabled="busy || optBusy || photoBusy" @click="onDelete">删除</button>
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

      <div v-if="doc.gaps?.length" class="lib-gaps">
        {{ doc.gaps.length }} 处待补充
      </div>

      <div v-if="versions.length" class="lib-versions">
        <span class="lv-title">历史版本：</span>
        <span v-for="v in versions" :key="v.id" class="lv-item">
          v{{ v.version }}
          <button class="lv-restore" :disabled="busy || optBusy || photoBusy"
                  @click="onRestore(v)">恢复</button>
        </span>
        <span class="lv-hint">仅保留最近 10 版</span>
      </div>

      <div class="lib-cols" :data-panel="mobilePanel">
        <aside class="lib-editor">
          <h3 class="editor-title">简历内容</h3>
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
              <textarea id="regen-jd" v-model="regenJd" rows="3" />
              <label><input type="checkbox" v-model="keepManual" /> 保留手工修改</label>
              <button class="lib-btn" @click="onRegenerate">重新生成</button>
            </fieldset>
          </details>
          <button class="lib-btn" :disabled="busy || optBusy || photoBusy || !tpl" @click="onOptimize">
            {{ optBusy ? "优化中…" : "智能优化" }}
          </button>
          <div v-if="optFields" class="lib-adopt" role="status">
            <p>预览为待采纳优化稿。可采纳后保存并导出，或放弃优化稿返回原稿。</p>
            <button class="lib-btn primary" :disabled="busy || optBusy || photoBusy" @click="adoptOptimized">采纳优化稿</button>
            <button class="lib-btn" :disabled="busy || optBusy || photoBusy" @click="discardOptimized">放弃优化稿</button>
          </div>
          <fieldset class="lib-fields" :disabled="busy || optBusy || !!optFields" :aria-busy="busy || optBusy || photoBusy">
            <legend class="sr-only">编辑简历字段</legend>
            <ResumeFieldsForm v-if="tpl" v-model="fields" :template="tpl" :gaps="doc.gaps || []"
                              :disabled="busy || optBusy || !!optFields" :context="photoContext" @photo-busy="onPhotoBusy" />
          </fieldset>
          <div v-if="Object.keys(optResults).length" class="lib-opt">
        <template v-for="(r, key) in optResults" :key="key">
          <div v-if="r.change_log?.length" class="lo-sec">
            <h3>改动 · {{ key }}</h3>
            <div v-for="(c, i) in r.change_log" :key="i" class="lo-pair">
              <p class="lo-before">{{ c.before }}</p>
              <p class="lo-after">{{ c.after }}</p>
            </div>
          </div>
          <p v-if="r.error" class="lib-err">{{ key }}：{{ r.error }}</p>
        </template>
        <div v-if="placeholders.length" class="lo-ph">
          {{ placeholders.length }} 处待补充占位：
          {{ placeholders.map((p) => p.field_path).join("、") }}
        </div>
          </div>
        </aside>
        <div class="lib-preview">
          <p class="preview-caption">{{ optFields ? "待采纳优化稿" : "投递预览" }} · {{ layout === "classic" ? "中文经典" : "中文现代" }}</p>
          <ResumePreview v-if="tpl" class="print-area" :template="tpl" :fields="optFields || fields" :layout="layout" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lib-page { max-width: 1440px; margin: 0 auto; padding: 20px 24px 60px; background: #f4f5f7; border-radius: 14px; color: var(--ink, #263238); }
.lib-page :deep(:focus-visible) { outline: 3px solid var(--teal, #2563eb); outline-offset: 3px; }
.lib-page fieldset { min-width: 0; border: 0; margin: 0; padding: 0; }
.lib-editor { min-width: 0; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 18px; }
.editor-title { margin: 0 0 14px; font-size: 15px; }
.lib-fields { margin-top: 18px !important; }
.lib-generation { border-bottom: 1px solid #e5e7eb; padding-bottom: 14px; margin-bottom: 14px; font-size: 13px; }
.lib-generation summary { cursor: pointer; padding: 6px 0; }
.evidence-panel { margin-bottom: 14px; border-bottom: 1px solid #e5e7eb; padding-bottom: 10px; font-size: 13px; overflow-wrap: anywhere; }
.evidence-panel summary { cursor: pointer; padding: 6px 0; }
.evidence-field { margin-top: 10px; }
.evidence-field ul { margin: 6px 0; padding-left: 20px; }
.evidence-field p { white-space: pre-wrap; margin: 6px 0; }
.evidence-field span { display: block; color: #4b5563; font-size: 12px; }
.lib-adopt .lib-btn { margin: 0 8px 6px 0; }
.lib-generation fieldset { display: grid; gap: 8px; padding-top: 10px; }
.lib-generation input:not([type]), .lib-generation textarea, .layout-control select { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 10px; background: #fff; font: inherit; }
.layout-control { display: flex; gap: 8px; align-items: center; white-space: nowrap; }
.layout-control select { width: auto; }
.save-state { margin-right: auto; font-size: 12px; color: #4b5563; }
.lib-adopt, .lib-feedback { padding: 10px 14px; border: 1px solid #e5e7eb; border-radius: 8px; margin: 12px 0; background: #fff; font-size: 13px; }
.lib-feedback { color: #b91c1c; }
.lib-preview { min-width: 0; overflow: auto; }
.preview-caption { margin: 0 0 12px; font-size: 12px; color: #4b5563; }
.mobile-switch { display: none; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.lib-title { font-size: 20px; margin: 0 0 16px; }
.lib-empty { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
  padding: 24px; text-align: center; }
.lib-bar { margin-bottom: 12px; }
.lib-new { display: flex; flex-direction: column; gap: 8px; background: #fff;
  border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.lib-new input, .lib-new textarea { border: 1px solid #d1d5db; border-radius: 8px;
  padding: 8px 10px; font-size: 13px; font-family: inherit; }
.lib-list { list-style: none; margin: 0; padding: 0; }
.lib-doc { display: flex; justify-content: space-between; align-items: center;
  background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
  padding: 14px 16px; margin-bottom: 10px; cursor: pointer; }
.lib-doc:hover { border-color: #93c5fd; }
.ld-main { display: flex; flex-direction: column; gap: 2px; }
.ld-role { color: #6b7280; font-size: 12px; }
.ld-meta { display: flex; gap: 12px; font-size: 12px; color: #6b7280; }
.ld-status[data-s="draft"] { color: #b45309; }
.ld-status[data-s="ready"] { color: #047857; }
.lib-detail-head { display: flex; align-items: center; gap: 10px;
  margin-bottom: 12px; }
.lib-versions { font-size: 12px; color: #6b7280; margin: 8px 0;
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.lv-title { font-weight: 600; }
.lv-item { background: #f3f4f6; border-radius: 999px; padding: 2px 8px; }
.lv-restore { border: 0; background: none; color: #2563eb; cursor: pointer;
  font-size: 12px; padding: 0 0 0 4px; }
.lv-hint { color: #9ca3af; }
.lib-stale { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px;
  padding: 10px 12px; font-size: 13px; color: #92400e; margin-bottom: 12px; }
.ls-item { font-family: monospace; }
.lib-detail-ops { display: flex; gap: 10px; align-items: center;
  margin-bottom: 12px; flex-wrap: wrap; font-size: 13px; }
.lib-detail-ops input[type="text"], .lib-detail-ops input:not([type]) {
  border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 10px; }
.lib-gaps { font-size: 12px; color: #b45309; margin-bottom: 8px; }
.lib-cols { display: grid; grid-template-columns: minmax(300px, 0.85fr) minmax(0, 1.4fr); gap: 24px; align-items: start; }
.lib-btn { border: 1px solid #d1d5db; background: #fff; border-radius: 8px;
  padding: 7px 14px; font-size: 13px; cursor: pointer; }
.lib-btn.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.lib-btn.danger { color: #b91c1c; border-color: #fca5a5; }
.lib-btn:disabled { opacity: 0.5; cursor: default; }
.lib-hint { color: #9ca3af; font-size: 13px; }
.lib-err { color: #b91c1c; font-size: 13px; }
.lib-opt { margin-top: 14px; border-top: 1px solid #e5e7eb; padding-top: 10px; }
.lo-sec h3 { font-size: 13px; margin: 10px 0 6px; }
.lo-pair { border-left: 3px solid #d1d5db; padding-left: 10px; margin-bottom: 8px; }
.lo-before { color: #9ca3af; font-size: 12px; margin: 0;
  text-decoration: line-through; }
.lo-after { color: #047857; font-size: 13px; margin: 2px 0 0; }
.lo-ph { font-size: 12px; color: #b45309; margin: 8px 0; }
@media (max-width: 760px) {
  .lib-page { padding: 16px 12px 40px; }
  .lib-detail-head, .ld-meta { flex-wrap: wrap; }
  .lib-cols { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .mobile-switch { display: flex; gap: 8px; margin: 14px 0; }
  .mobile-switch [aria-pressed="true"] { background: var(--teal, #2563eb); color: #fff; }
  .lib-cols[data-panel="edit"] .lib-preview, .lib-cols[data-panel="preview"] .lib-editor { display: none; }
  .lib-editor { padding: 14px; }
  .save-state { width: 100%; order: 1; }
}
/* 库详情导出：只出预览稿，隐藏列表/表单/操作区 */
@media print {
  .lib-title, .lib-detail-head, .lib-list, .lib-bar, .lib-new, .lib-loading, .lib-stale, .lib-gaps,
  .lib-versions, .lib-detail-ops, .lib-feedback, .mobile-switch, .preview-caption,
  .lib-editor, .evidence-panel { display: none !important; }
  .lib-cols, .lib-preview { display: block !important; }
}
@media (max-width: 420px) { .lib-page { padding: 14px 10px 40px; } }
</style>
