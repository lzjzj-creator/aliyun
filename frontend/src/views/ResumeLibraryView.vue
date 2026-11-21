<script setup>
// v3 #resume/library：岗位简历库列表 + 新建 + 文档编辑（事实库驱动）
import { computed, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
import { downloadElementAsPdf } from "../utils/exportPdf";
import { getTemplate, optimizePlan, optimizeSection } from "../api/resume";
import {
  createResume, deleteResume, duplicateResume, getOwnerToken, getResume,
  listResumes,
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
const hasToken = ref(!!getOwnerToken());
const creating = ref(false);
const newDoc = ref({ title: "", target_role: "", jd_text: "" });
const regenRole = ref("");
const regenJd = ref("");
const keepManual = ref(true);
const staleList = computed(() => doc.value?.stale_sources || []);
const dirty = ref(false);

// —— 优化（复用 v2 黑箱逐段优化 + diff 视图）——
const optBusy = ref(false);
const optPlan = ref(null);
const optResults = ref({});
const optFields = ref(null);
const compareMap = computed(() => {
  const m = {};
  for (const r of Object.values(optResults.value)) {
    for (const c of r.change_log || []) m[c.field_path] = c.before;
  }
  return m;
});
const placeholders = computed(() =>
  Object.values(optResults.value).flatMap((r) => r.placeholders || []));

async function onOptimize() {
  if (!tpl.value || !doc.value) return;
  optBusy.value = true;
  errMsg.value = "";
  optPlan.value = null; optResults.value = {}; optFields.value = null;
  try {
    const ver = doc.value.template_version || tpl.value.template_version;
    optPlan.value = await optimizePlan({
      template_version: ver, fields: fields.value });
    optFields.value = JSON.parse(JSON.stringify(fields.value));
    for (const s of optPlan.value.sections || []) {
      if (!(s.optimizable && s.status === "pending")) continue;
      s.status = "running";
      try {
        const r = await optimizeSection({
          template_version: ver, section: s.key, fields: fields.value });
        optResults.value = { ...optResults.value, [s.key]: r };
        if (r.status === "done" && r.optimized != null) {
          optFields.value = { ...optFields.value, [s.key]: r.optimized };
        }
        s.status = r.status === "error" ? "error" : "done";
      } catch (e) {
        optResults.value = { ...optResults.value, [s.key]: {
          status: "error", change_log: [], placeholders: [], error: e.message } };
        s.status = "error";
      }
    }
  } catch (e) {
    errMsg.value = `优化失败：${e.message}`;
  } finally {
    optBusy.value = false;
  }
}

function adoptOptimized() {
  if (!optFields.value) return;
  fields.value = JSON.parse(JSON.stringify(optFields.value));   // → dirty → PATCH 保存
  optResults.value = {}; optFields.value = null; optPlan.value = null;
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
  errMsg.value = "";
  try {
    const res = await listResumes();
    docs.value = res.documents || [];
  } catch (e) {
    if (e.status === 404) { hasToken.value = false; return; }
    if (import.meta.env.DEV) {
      const demo = await fetch("/demo/v3/resume-list.json").then((r) => r.json());
      docs.value = demo.documents || [];
      return;
    }
    errMsg.value = "简历库暂时不可用";
  }
}

async function openDoc(id) {
  emit("nav", `#/resume/library/${id}`);
}

async function loadDoc(id) {
  errMsg.value = "";
  try {
    doc.value = await getResume(id);
    fields.value = doc.value.fields || {};
    loadVersions(id);
  } catch (e) {
    if (e.status === 404) { doc.value = null; errMsg.value = "文档不存在或已删除"; }
    else if (import.meta.env.DEV) {
      doc.value = await fetch("/demo/v3/resume-detail.json").then((r) => r.json());
      fields.value = doc.value.fields || {};
    } else errMsg.value = "文档加载失败";
  }
  dirty.value = false;
}

async function onCreate() {
  if (!newDoc.value.title.trim()) { errMsg.value = "请填写简历标题"; return; }
  busy.value = true;
  try {
    const res = await createResume({
      title: newDoc.value.title.trim(),
      target_role: newDoc.value.target_role.trim() || undefined,
      jd_text: newDoc.value.jd_text.trim() || undefined,
      template_version: tpl.value?.template_version,
    });
    creating.value = false;
    newDoc.value = { title: "", target_role: "", jd_text: "" };
    openDoc(res.document.id);
  } catch (e) {
    errMsg.value = e.status === 503 ? "简历生成服务暂时不可用" : "创建失败";
  } finally {
    busy.value = false;
  }
}

async function onSave() {
  if (!doc.value) return;
  busy.value = true;
  try {
    const res = await patchResume(doc.value.id, { fields: fields.value },
                                  doc.value.version);
    doc.value = res.document;
    fields.value = res.document.fields || {};
    dirty.value = false;
  } catch (e) {
    errMsg.value = e.status === 409 ? "版本冲突，请刷新后重试" : "保存失败";
  } finally {
    busy.value = false;
  }
}

async function onRegenerate() {
  if (!doc.value) return;
  if (staleList.value.length &&
      !confirm(`有 ${staleList.value.length} 处来源已变更，重新生成会以最新事实重建草稿。继续？`)) return;
  busy.value = true;
  try {
    const res = await regenerateResume(doc.value.id, {
      target_role: regenRole.value.trim() || undefined,
      jd_text: regenJd.value.trim() || undefined,
      keep_manual: keepManual.value,
    });
    doc.value = res.document;
    fields.value = res.document.fields || {};
    dirty.value = false;
  } catch (e) {
    errMsg.value = e.status === 503 ? "生成服务暂时不可用" : "重新生成失败";
  } finally {
    busy.value = false;
  }
}

const versions = ref([]);

async function loadVersions(id) {
  try {
    const res = await listVersions(id);
    versions.value = res.versions || [];
  } catch {
    versions.value = [];
  }
}

async function onRestore(v) {
  if (!doc.value) return;
  if (!confirm(`恢复到 v${v.version} 的快照？当前状态会先存入历史，可再回退。`)) return;
  busy.value = true;
  try {
    const res = await restoreVersion(doc.value.id, v.version);
    doc.value = res.document;
    fields.value = res.document.fields || {};
    dirty.value = false;
    loadVersions(doc.value.id);
  } catch (e) {
    errMsg.value = e.status === 404 ? "该历史版本不存在" : "恢复失败，请重试";
  } finally {
    busy.value = false;
  }
}

async function onExport() {
  // 占位符红线：导出前必须列出待补充清单二次确认（与 v2 向导同口径）
  const gaps = doc.value?.gaps || [];
  if (gaps.length) {
    const list = gaps.map((g) => `· ${g.field_path}（${g.reason || "待补充"}）`)
      .join("\n");
    if (!window.confirm(`还有 ${gaps.length} 处待补充：\n${list}\n\n仍要导出吗？`))
      return;
  }
  busy.value = true;
  errMsg.value = "";
  try {
    const el = document.querySelector(".lib-page .print-area");
    await downloadElementAsPdf(el, `${doc.value?.title || "简历"}.pdf`);
  } catch (e) {
    errMsg.value = e.message || "PDF 导出失败，请重试";
  } finally {
    busy.value = false;
  }
}

async function onDuplicate() {
  if (!doc.value) return;
  busy.value = true;
  try {
    const res = await duplicateResume(doc.value.id);
    openDoc(res.document.id);
  } catch {
    errMsg.value = "复制失败，请重试";
  } finally {
    busy.value = false;
  }
}

async function onDelete() {
  if (!doc.value || !confirm(`删除「${doc.value.title}」？此操作不可恢复。`)) return;
  busy.value = true;
  try {
    await deleteResume(doc.value.id);
    emit("nav", "#/resume/library");
  } catch {
    errMsg.value = "删除失败";
  } finally {
    busy.value = false;
  }
}

watch(fields, () => { if (doc.value) dirty.value = true; }, { deep: true });
watch(() => props.docId, (id) => { if (id) loadDoc(id); else doc.value = null; });

onMounted(async () => {
  await loadTpl();
  if (hasToken.value) await loadList();
  if (props.docId) await loadDoc(props.docId);
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

    <template v-else-if="!doc">
      <div class="lib-bar">
        <button class="lib-btn primary" @click="creating = !creating">
          {{ creating ? "收起" : "＋ 新建简历" }}
        </button>
      </div>

      <div v-if="creating" class="lib-new">
        <input v-model="newDoc.title" placeholder="标题（如：后端开发-暑期实习）"
               maxlength="80" />
        <input v-model="newDoc.target_role" placeholder="目标岗位（可选）"
               maxlength="80" />
        <textarea v-model="newDoc.jd_text" rows="3"
                  placeholder="JD 原文（可选，用于相关性排序）" />
        <button class="lib-btn primary" :disabled="busy" @click="onCreate">
          从事实库生成
        </button>
      </div>

      <ul class="lib-list">
        <li v-for="d in docs" :key="d.id" class="lib-doc"
            @click="openDoc(d.id)">
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
        <input v-model="regenRole" placeholder="目标岗位（可选改）" maxlength="80" />
        <label><input type="checkbox" v-model="keepManual" /> 保留手工修改</label>
        <button class="lib-btn" :disabled="busy" @click="onRegenerate">重新生成</button>
        <button class="lib-btn" :disabled="busy || optBusy" @click="onOptimize">
          {{ optBusy ? "优化中…" : "智能优化" }}
        </button>
        <button class="lib-btn primary" :disabled="busy || !dirty" @click="onSave">
          {{ dirty ? "保存修改" : "已保存" }}
        </button>
        <button class="lib-btn" :disabled="busy" @click="onDuplicate">复制为新稿</button>
        <button class="lib-btn" :disabled="busy" @click="onExport">导出 PDF</button>
        <button class="lib-btn danger" :disabled="busy" @click="onDelete">删除</button>
      </div>

      <div v-if="doc.gaps?.length" class="lib-gaps">
        {{ doc.gaps.length }} 处待补充
      </div>

      <div v-if="versions.length" class="lib-versions">
        <span class="lv-title">历史版本：</span>
        <span v-for="v in versions" :key="v.id" class="lv-item">
          v{{ v.version }}
          <button class="lv-restore" :disabled="busy"
                  @click="onRestore(v)">恢复</button>
        </span>
        <span class="lv-hint">仅保留最近 10 版</span>
      </div>

      <div class="lib-cols">
        <ResumeFieldsForm v-model="fields" :template="tpl" :gaps="doc.gaps || []" />
        <ResumePreview class="print-area" :template="tpl"
                       :fields="optFields || fields"
                       :evidence="doc.evidence_map || {}" :gaps="doc.gaps || []"
                       :compare="optFields ? compareMap : null" />
      </div>

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
        <button v-if="optFields" class="lib-btn primary" @click="adoptOptimized">
          采纳优化稿（再点保存生效）
        </button>
      </div>
      <p v-if="errMsg" class="lib-err">{{ errMsg }}</p>
    </template>
  </div>
</template>

<style scoped>
.lib-page { max-width: 1080px; margin: 0 auto; padding: 20px 16px 60px; }
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
.lib-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
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
@media (max-width: 720px) { .lib-cols { grid-template-columns: 1fr; } }
/* 库详情导出：只出预览稿，隐藏列表/表单/操作区 */
@media print {
  .lib-list, .lib-bar, .lib-new, .ld-head, .lib-stale, .lib-gaps,
  .lib-versions, .lib-detail-ops, .lib-opt, .rff { display: none !important; }
  .lib-cols { display: block !important; }
}
@media (max-width: 420px) { .lib-page { padding: 14px 10px 40px; } }
</style>
