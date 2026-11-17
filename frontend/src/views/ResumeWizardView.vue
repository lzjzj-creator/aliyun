<script setup>
// 简历向导三步（B 域 #19/#20/#22）：
// ① 一键填写（会话素材→字段+出处）② 人工修改+实时预览+打印导出 ③ 逐段黑箱优化+对比+导出
import { computed, onMounted, ref, watch } from "vue";
import ResumeFieldsForm from "../components/ResumeFieldsForm.vue";
import ResumePreview from "../components/ResumePreview.vue";
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

const STEP_CN = { 1: "① 一键填写", 2: "② 修改预览", 3: "③ 一键优化" };
const isDev = import.meta.env.DEV;

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

function exportPrint() {
  if (placeholders.value.length && optFields.value && step.value === 3) {
    const list = placeholders.value.map((p) => `· ${p.field_path}`).join("\n");
    if (!window.confirm(`还有 ${placeholders.value.length} 处【待补充】占位：\n${list}\n\n仍要导出吗？`)) return;
  }
  window.print();
}
</script>

<template>
  <section class="pane wizard">
    <nav class="wiz-steps">
      <button v-for="n in [1, 2, 3]" :key="n" :class="{ on: step === n }"
              :disabled="n > 1 && !filled" @click="step = n">{{ STEP_CN[n] }}</button>
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
        <button v-if="isDev" class="ghost" @click="useDemoConvo">演示素材</button>
        <button :disabled="busy || (!convo.length && !pickedSession)" @click="doAutofill">
          {{ busy ? "填写中…" : "✨ 一键填写" }}
        </button>
      </div>
      <p v-if="convo.length" class="wiz-hint">已载入 {{ convo.length }} 条对话素材</p>
      <p v-if="tpl" class="wiz-hint">模板：{{ tpl.name }} · v{{ tpl.template_version }} · {{ tpl.page_rule }}</p>

      <ResumeFieldsForm v-if="filled" v-model="fields" :template="tpl" :gaps="gaps" />
      <div v-if="filled" class="row">
        <span />
        <button @click="step = 2">下一步：预览导出 →</button>
      </div>
    </div>

    <!-- ================= ② 修改 + 预览 + 导出 ================= -->
    <div v-else-if="step === 2" class="wiz-grid">
      <div class="wz-left">
        <h2>第二步：人工修改（即改即存本机）</h2>
        <ResumeFieldsForm v-model="fields" :template="tpl" :gaps="gaps" />
        <div class="row">
          <button class="ghost" @click="step = 1">← 上一步</button>
          <div class="row-right">
            <button class="ghost" @click="saveDraft(DRAFT_KEY, { template_version: draftVer, fields, evidence_map: evidence, gaps })">保存草稿</button>
            <button @click="exportPrint">导出 PDF</button>
            <button @click="step = 3">下一步：一键优化 →</button>
          </div>
        </div>
      </div>
      <div class="wz-right print-area">
        <ResumePreview v-if="tpl" :template="tpl" :fields="fields"
                       :evidence="evidence" :gaps="gaps" />
      </div>
    </div>

    <!-- ================= ③ 逐段优化 + 对比 + 导出 ================= -->
    <div v-else class="wiz-grid">
      <div class="wz-left opt-panel">
        <h2>第三步：黑箱优化（逐段跑，不改事实）</h2>
        <button :disabled="optBusy || !filled" @click="doOptimize">
          {{ optBusy ? "优化中…" : "⚡ 一键优化" }}
        </button>

        <ul v-if="plan" class="opt-list">
          <li v-for="s in plan.sections" :key="s.key" :data-st="s.status">
            <b>{{ s.title }}</b>
            <span class="st">
              {{ { pending: "待优化", running: "优化中…", done: "完成", error: "失败", skipped: "跳过", ok: "无需改", check: "人工确认" }[s.status] || s.status }}
            </span>
            <span v-if="s.reason" class="rs">{{ s.reason }}</span>
            <div v-if="results[s.key]?.steps?.length" class="opt-steps">
              <span v-for="(st, i) in results[s.key].steps" :key="i">
                {{ st.title }}：{{ st.detail }}
              </span>
            </div>
            <div v-if="results[s.key]?.error" class="warn">{{ results[s.key].error }}</div>
          </li>
        </ul>

        <template v-for="(r, key) in results" :key="key">
          <div v-if="r.change_log?.length" class="rewrite">
            <h3>改动明细 · {{ key }}</h3>
            <div v-for="(c, i) in r.change_log" :key="i" class="pair">
              <div><b>原文</b><p>{{ c.before }}</p></div>
              <div><b>优化</b><p>{{ c.after }}</p><p class="follow">{{ c.reason }}</p></div>
            </div>
          </div>
        </template>

        <div v-if="placeholders.length" class="gaps">
          <h3>待补充占位（{{ placeholders.length }} 处）</h3>
          <p v-for="(p, i) in placeholders" :key="i">· {{ p.field_path }}：{{ p.placeholder }}</p>
        </div>
        <div v-if="pendingQuestions.length" class="questions">
          <b>建议你确认的事实：</b>
          <p v-for="(q, i) in pendingQuestions" :key="i">· {{ q }}</p>
        </div>

        <div v-if="optFields" class="row">
          <button class="ghost" @click="step = 2">← 返回原稿</button>
          <div class="row-right">
            <button @click="adoptAll">采纳优化稿</button>
            <button class="ghost" @click="exportPrint">导出优化稿</button>
          </div>
        </div>
      </div>

      <div class="wz-right print-area">
        <ResumePreview v-if="tpl && optFields" :template="tpl" :fields="optFields"
                       :evidence="evidence" :gaps="gaps" :compare="compareMap" />
        <ResumePreview v-else-if="tpl" :template="tpl" :fields="fields"
                       :evidence="evidence" :gaps="gaps" />
      </div>
    </div>
  </section>
</template>


<style scoped>
.wiz-steps { display: flex; gap: 8px; margin-bottom: 14px; }
.wiz-steps button {
  border: 1px solid var(--line); background: #fff; color: #7a6f63;
  border-radius: 999px; padding: 6px 14px; font-size: .82rem; cursor: pointer;
}
.wiz-steps button.on { border-color: var(--teal); background: var(--teal); color: #fff; }
.wiz-source { display: flex; align-items: flex-end; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.wiz-source label { display: flex; flex-direction: column; gap: 4px; font-size: .8rem; color: #7a6f63; }
.wiz-source select { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font: inherit; background: #fff; min-width: 220px; }
.wiz-hint { font-size: .78rem; color: #9a9186; margin: 4px 0 12px; }
.wiz-grid { display: grid; grid-template-columns: 1fr 1.1fr; gap: 18px; align-items: start; }
.wz-right { position: sticky; top: 10px; }
.row-right { display: flex; gap: 8px; }
.opt-list { list-style: none; padding: 0; margin: 12px 0; }
.opt-list li { padding: 8px 10px; border: 1px solid var(--line); border-radius: 10px; margin-bottom: 6px; background: #fff; font-size: .85rem; }
.opt-list li[data-st="done"] { border-left: 3px solid #0e8a16; }
.opt-list li[data-st="error"] { border-left: 3px solid #b3402a; }
.opt-list li[data-st="running"] { border-left: 3px solid var(--accent); }
.opt-list .st { margin-left: 8px; font-size: .75rem; color: #9a9186; }
.opt-list .rs { display: block; font-size: .72rem; color: #9a9186; }
.opt-steps { display: flex; flex-direction: column; font-size: .72rem; color: #7a6f63; margin-top: 4px; }
@media (max-width: 900px) {
  .wiz-grid { grid-template-columns: 1fr; }
  .wz-right { position: static; }
}
</style>
