<script setup>
// 简历向导三步（B 域 #19/#20/#22）：
// ① 一键填写（会话素材→字段+出处）② 人工修改+实时预览+打印导出 ③ 逐段黑箱优化+对比+导出
import { computed, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
import { downloadElementAsPdf } from "../utils/exportPdf";
import { getTemplate, autofill, optimizePlan, optimizeSection } from "../api/resume";
import { getSession, listSessions } from "../api/sessions";
import {
  DRAFT_KEY, OPT_DRAFT_KEY, saveDraft, loadDraft, getCurrentSessionId,
} from "../storage";

const step = ref(1);
const tpl = ref(null);
const errMsg = ref("");
const busy = ref(false);

// —— 草稿数据 ——
const fields = ref({});
const evidence = ref({});
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
const compareMap = computed(() => {
  const m = {};
  for (const r of Object.values(results.value)) {
    for (const c of r.change_log || []) m[c.field_path] = c.before;
  }
  return m;
});
const placeholders = computed(() =>
  Object.values(results.value).flatMap((r) => r.placeholders || []));
const pendingQuestions = computed(() =>
  Object.values(results.value).flatMap((r) => r.pending_questions || []));

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
// 点区块卡 → 右栏表单滚动定位 + 预览高亮
function gotoSec(key) {
  focusSec.value = key;
  panelEl.value?.querySelector(`.rff-sec[data-key="${key}"]`)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}

onMounted(() => { loadTemplate(); loadSessions(); });

// 即改即存本机（§11：简历不落服务端）
watch(fields, (v) => {
  if (draftVer.value && Object.keys(v).length) {
    saveDraft(DRAFT_KEY, {
      template_version: draftVer.value,
      fields: v, evidence_map: evidence.value, gaps: gaps.value,
    });
  }
}, { deep: true });

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
  if (!tpl.value) return;
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
    fields.value = r.fields;
    evidence.value = r.evidence_map || {};
    gaps.value = r.gaps || [];
    saveDraft(DRAFT_KEY, { template_version: tpl.value.template_version, ...r });
    draftVer.value = tpl.value.template_version;
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
  if (!tpl.value || !filled.value) return;
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
  if (!optFields.value) return;
  fields.value = JSON.parse(JSON.stringify(optFields.value));
  saveDraft(OPT_DRAFT_KEY, {
    template_version: tpl.value.template_version,
    fields: optFields.value, evidence_map: evidence.value, gaps: gaps.value,
  });
  errMsg.value = "";
}

async function exportPrint() {
  if (placeholders.value.length && optFields.value && step.value === 3) {
    const list = placeholders.value.map((p) => `· ${p.field_path}`).join("\n");
    if (!window.confirm(`还有 ${placeholders.value.length} 处【待补充】占位：\n${list}\n\n仍要导出吗？`)) return;
  }
  try {
    const el = document.querySelector(".wizard .print-area");
    await downloadElementAsPdf(el, `${targetRole.value || "简历"}.pdf`);
  } catch (e) {
    errMsg.value = e.message || "PDF 导出失败，请重试";
  }
}
</script>

<template>
  <section class="pane wizard">
    <nav class="wiz-steps">
      <button v-for="n in [1, 2, 3]" :key="n" :class="{ on: step === n }"
              :disabled="n > 1 && !filled" @click="step = n">{{ STEP_CN[n] }}</button>
      <button class="wiz-lib" @click="emit('goto-library')">简历库 →</button>
    </nav>
    <p v-if="errMsg" class="warn">{{ errMsg }}</p>

    <!-- ================= ① 一键填写 ================= -->
    <div v-if="step === 1">
      <h2>第一步：选择素材来源，一键填写</h2>
      <div class="wiz-source">
        <label>
          访谈会话
          <select v-model="pickedSession">
            <option value="">— 选择会话 —</option>
            <option v-for="s in sessions" :key="s.id" :value="s.id">
              {{ s.title }}（{{ s.stage }}）
            </option>
          </select>
        </label>
        <button class="ghost" :disabled="busy || !pickedSession" @click="readConvo">读取会话</button>
        <button class="ghost" @click="useDemoConvo">演示素材</button>
        <button :disabled="busy || (!convo.length && !pickedSession)" @click="doAutofill">
          {{ busy ? "填写中…" : "✨ 一键填写" }}
        </button>
      </div>
      <p v-if="convo.length" class="wiz-hint">已载入 {{ convo.length }} 条对话素材</p>
      <p v-if="tpl" class="wiz-hint">模板：{{ tpl.name }} · v{{ tpl.template_version }} · {{ tpl.page_rule }}</p>

      <ResumeFieldsForm v-if="filled && tpl" v-model="fields" :template="tpl" :gaps="gaps" />
      <div v-if="filled" class="row">
        <span />
        <button @click="step = 2">下一步：预览导出 →</button>
      </div>
    </div>

    <!-- ================= ② 修改 + 预览 + 导出：区块导航 | A4 画布 | 表单 ================= -->
    <div v-else-if="step === 2" class="wiz-work">
      <nav class="wz-nav">
        <button v-for="s in navSections" :key="s.key" class="wz-nav-item"
                :class="{ on: focusSec === s.key, gap: s.gapCount > 0, ok: s.hasData && !s.gapCount }"
                @click="gotoSec(s.key)">
          <span class="wz-nav-title">{{ s.title }}</span>
          <span v-if="s.gapCount" class="wz-nav-badge warn">{{ s.gapCount }} 待补</span>
          <span v-else-if="s.hasData" class="wz-nav-badge">✓</span>
          <span v-else class="wz-nav-badge dim">空</span>
        </button>
      </nav>

      <div class="wz-canvas print-area">
        <ResumePreview v-if="tpl" :template="tpl" :fields="fields"
                       :evidence="evidence" :gaps="gaps" :focus="focusSec" />
      </div>

      <aside class="wz-panel" ref="panelEl">
        <h2 class="wz-panel-title">人工修改 · 即改即存本机</h2>
        <ResumeFieldsForm v-model="fields" :template="tpl" :gaps="gaps" />
        <div class="row wz-actions">
          <button class="ghost" @click="step = 1">← 上一步</button>
          <div class="row-right">
            <button class="ghost" @click="saveDraft(DRAFT_KEY, { template_version: draftVer, fields, evidence_map: evidence, gaps })">保存草稿</button>
            <button class="primary" @click="exportPrint">导出 PDF</button>
            <button @click="step = 3">一键优化 →</button>
          </div>
        </div>
      </aside>
    </div>

    <!-- ================= ③ 逐段优化 + 对比 + 导出 ================= -->
    <div v-else class="wiz-work">
      <nav class="wz-nav opt-panel">
        <button class="opt-run" :disabled="optBusy || !filled" @click="doOptimize">
          {{ optBusy ? "优化中…" : "⚡ 一键优化" }}
        </button>
        <ul v-if="plan" class="opt-list">
          <li v-for="s in plan.sections" :key="s.key" :data-st="s.status"
              :class="{ on: focusSec === s.key }" @click="gotoSec(s.key)">
            <b>{{ s.title }}</b>
            <span class="st">
              {{ { pending: "待优化", running: "优化中…", done: "完成", error: "失败", skipped: "跳过", ok: "无需改", check: "人工确认" }[s.status] || s.status }}
            </span>
            <span v-if="s.reason" class="rs">{{ s.reason }}</span>
          </li>
        </ul>
        <button class="ghost back" @click="step = 2">← 返回修改</button>
      </nav>

      <div class="wz-canvas print-area">
        <ResumePreview v-if="tpl && optFields" :template="tpl" :fields="optFields"
                       :evidence="evidence" :gaps="gaps" :compare="compareMap" :focus="focusSec" />
        <ResumePreview v-else-if="tpl" :template="tpl" :fields="fields"
                       :evidence="evidence" :gaps="gaps" :focus="focusSec" />
      </div>

      <aside class="wz-panel">
        <h2 class="wz-panel-title">优化上下文</h2>
        <template v-if="!plan && !optBusy">
          <p class="wiz-hint">点左侧「一键优化」，逐段跑黑箱优化；只改表达，不改事实。改动处会在预览里标绿，悬停可见原文。</p>
        </template>

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
          <button @click="adoptAll">采纳优化稿</button>
          <button class="primary" @click="exportPrint">导出优化稿</button>
        </div>
      </aside>
    </div>
  </section>
</template>


<style scoped>
.wiz-steps { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.wiz-steps button {
  border: 1px solid var(--line); background: #fff; color: var(--ink-soft);
  border-radius: 999px; padding: 6px 14px; font-size: .82rem; cursor: pointer;
}
.wiz-steps button.on { border-color: var(--teal); background: var(--teal); color: #fff; }
.wiz-source { display: flex; align-items: flex-end; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.wiz-source label { display: flex; flex-direction: column; gap: 4px; font-size: .8rem; color: var(--ink-soft); }
.wiz-source select { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font: inherit; background: #fff; min-width: 220px; }
.wiz-hint { font-size: .78rem; color: var(--mute); margin: 4px 0 12px; }

/* 工作台三柱：区块导航 | A4 画布 | 上下文面板 */
.wiz-work { display: flex; gap: 18px; align-items: flex-start; }
.wz-nav {
  flex: none; width: 200px; display: flex; flex-direction: column; gap: 6px;
  position: sticky; top: 14px;
}
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
.wz-canvas { flex: 1; min-width: 0; }
.wz-panel {
  flex: none; width: 340px; position: sticky; top: 14px;
  max-height: calc(100vh - 130px); overflow-y: auto;
  background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px;
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

@media (max-width: 1100px) {
  .wiz-work { flex-direction: column; }
  .wz-nav {
    width: 100%; flex-direction: row; flex-wrap: nowrap; overflow-x: auto;
    position: static; padding-bottom: 4px;
  }
  .wz-nav-item { flex: none; white-space: nowrap; }
  .wz-panel { width: 100%; position: static; max-height: none; }
}
</style>

.wiz-steps .wiz-lib { margin-left: auto; color: var(--teal); }
