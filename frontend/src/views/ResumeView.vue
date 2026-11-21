<script setup>
import { ref, computed, onUnmounted } from "vue";
import { analyzeResume, rewriteBullets } from "../api";
import { downloadElementAsPdf } from "../utils/exportPdf";

const MAX = 12000;
const step = ref(1); // 1输入 2核对 3结果
const resumeText = ref("");
const jobText = ref("");
const fileMsg = ref("");
const busy = ref(false);
const slow = ref(false);
const report = ref(null);
const rewrite = ref(null);
const errMsg = ref("");
let slowTimer = null;

function startSlowHint() {
  clearTimeout(slowTimer);
  slow.value = false;
  slowTimer = setTimeout(() => { slow.value = true; }, 1500);
}
function stopSlowHint() {
  clearTimeout(slowTimer);
  slow.value = false;
}
onUnmounted(stopSlowHint);

// 浏览器本地提取 TXT / DOCX / 文字型 PDF（文件不出浏览器，点分析才发文本）
async function onFile(e) {
  const f = e.target.files?.[0];
  if (!f) return;
  fileMsg.value = "";
  try {
    const ext = f.name.split(".").pop().toLowerCase();
    let text = "";
    if (ext === "txt") {
      text = await f.text();
    } else if (ext === "docx") {
      const mammoth = await import("mammoth");
      text = (await mammoth.extractRawText({ arrayBuffer: await f.arrayBuffer() })).value;
    } else if (ext === "pdf") {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc =
        new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).href;
      const pdf = await pdfjs.getDocument({ data: await f.arrayBuffer() }).promise;
      if (pdf.numPages > 5) { fileMsg.value = "PDF 超过 5 页，请删减后再试。"; return; }
      const pages = [];
      for (let i = 1; i <= pdf.numPages; i++) {
        const c = await (await pdf.getPage(i)).getTextContent();
        pages.push(c.items.map((it) => it.str).join(" "));
      }
      text = pages.join("\n\n");
      if (!text.trim()) { fileMsg.value = "这个 PDF 提取不到文字，可能是扫描件（暂不支持 OCR）。"; return; }
    } else {
      fileMsg.value = "只支持 TXT / DOCX / 文字型 PDF。"; return;
    }
    if (text.length > MAX) { fileMsg.value = `超出 ${MAX} 字上限（当前 ${text.length} 字）`; return; }
    resumeText.value = text.trim();
    step.value = 2;
  } catch (err) {
    fileMsg.value = `解析失败：${err.message}`;
  }
}

async function runAnalyze() {
  busy.value = true; startSlowHint(); errMsg.value = ""; report.value = null; rewrite.value = null;
  demoMode.value = false;
  try {
    const r = await analyzeResume(resumeText.value, jobText.value);
    if (!r.ok) { errMsg.value = r.error?.message || "分析失败"; return; }
    report.value = r;
    step.value = 3;
  } catch (e) { errMsg.value = `请求失败：${e.message}`; }
  finally { stopSlowHint(); busy.value = false; }
}

// 评委演示模式：加载预存的真实接口响应，不消耗 API 配额
const demoMode = ref(false);
async function loadDemo() {
  busy.value = true; errMsg.value = "";
  try {
    const [analyze, rewriteR, text] = await Promise.all([
      fetch("/demo/analyze.json").then((r) => r.json()),
      fetch("/demo/rewrite.json").then((r) => r.json()),
      fetch("/demo/resume.txt").then((r) => r.text()),
    ]);
    resumeText.value = text;
    report.value = analyze;
    rewrite.value = rewriteR?.ok ? rewriteR.data : null;
    demoMode.value = true;
    step.value = 3;
  } catch (e) { errMsg.value = `示例加载失败：${e.message}`; }
  finally { busy.value = false; }
}

const REWRITE_SEED = /[。；;\n]/;

// 定向模式没有数值匹配分，覆盖率直接使用服务端结构化结果，不解析散文
const jdResult = computed(() => {
  const r = report.value;
  const coverage = r?.rule_analysis?.job_coverage || r?.job_coverage;
  if (!r || r.mode !== "targeted" || !coverage) return null;
  return {
    total: coverage.total || 0,
    covered: coverage.covered || [],
    missing: coverage.missing || [],
    pct: Number.isFinite(coverage.pct) ? coverage.pct : null,
    readiness: r.model_analysis?.rewrite_readiness || "",
    questions: r.model_analysis?.follow_up_questions || [],
    safety: r.model_analysis?.safety_note || "",
  };
});

const READINESS_CN = {
  ready_for_limited_rewrite: "可进入有限改写",
  needs_facts: "需先补充事实",
};
const readinessText = (code) => READINESS_CN[code] || code;

const KNOWN_MODEL_KEYS = new Set([
  "provider", "model", "is_real_ai", "summary", "evidence_found",
  "evidence_gaps", "follow_up_questions", "rewrite_readiness", "safety_note",
]);
const extraModelFields = computed(() =>
  Object.entries(report.value?.model_analysis || {}).filter(([k]) => !KNOWN_MODEL_KEYS.has(k)));

async function exportPdf() {
  try {
    const el = document.querySelector(".pane");
    await downloadElementAsPdf(el, "简历诊断报告.pdf");
  } catch (e) {
    errMsg.value = e.message || "PDF 导出失败，请重试";
  }
}

async function runRewrite() {
  busy.value = true; startSlowHint(); errMsg.value = "";
  try {
    const bullets = resumeText.value.split(REWRITE_SEED)
      .map((s) => s.trim()).filter((s) => s.length >= 8).slice(0, 12);
    const r = await rewriteBullets(bullets);
    if (!r.ok) { errMsg.value = r.error?.message || "改写失败"; return; }
    rewrite.value = r.data;
  } catch (e) { errMsg.value = `请求失败：${e.message}`; }
  finally { stopSlowHint(); busy.value = false; }
}
</script>

<template>
  <section class="resume">
    <div v-if="step === 1" class="pane">
      <h2>第一步：放入简历</h2>
      <label class="upload">
        <input type="file" accept=".txt,.docx,.pdf" @change="onFile" hidden />
        选择 TXT / DOCX / 文字型 PDF（≤5MB·≤5页，浏览器本地提取）
      </label>
      <p v-if="fileMsg" class="warn">{{ fileMsg }}</p>
      <p v-if="errMsg" class="warn">{{ errMsg }}</p>
      <textarea v-model="resumeText" :maxlength="MAX"
        placeholder="或直接粘贴简历文本…" rows="10" />
      <p class="count">{{ resumeText.length }} / {{ MAX }}</p>
      <div class="row">
        <button :disabled="!resumeText.trim()" @click="step = 2">下一步：核对文本 →</button>
        <button class="ghost" :disabled="busy" @click="loadDemo">📋 示例演示（无需上传）</button>
      </div>
    </div>

    <div v-else-if="step === 2" class="pane">
      <h2>第二步：核对提取结果（可修改）</h2>
      <textarea v-model="resumeText" rows="9" :maxlength="MAX" />
      <h3>目标岗位 JD（可选）</h3>
      <textarea v-model="jobText" rows="4" :maxlength="MAX"
        placeholder="粘贴招聘 JD 进入岗位定向分析；留空则通用体检" />
      <p v-if="errMsg" class="warn">{{ errMsg }}</p>
      <p v-if="busy && slow" class="slow-hint">首次唤醒服务，约 3–8 秒，按钮已锁定，请稍候…</p>
      <div class="row">
        <button class="ghost" @click="step = 1">← 返回</button>
        <button :disabled="busy" @click="runAnalyze">{{ busy ? "分析中…" : "开始分析 →" }}</button>
      </div>
    </div>

    <div v-else class="pane">
      <h2>诊断结果 <span v-if="demoMode" class="demo-badge">示例快照</span></h2>
      <p v-if="errMsg" class="warn">{{ errMsg }}</p>

      <template v-if="report">
        <p class="summary">{{ report.model_analysis?.summary || report.summary || report.rule_analysis?.summary }}</p>

        <div v-if="jdResult" class="jd-panel">
          <h3>岗位定向分析</h3>
          <p v-if="jdResult.total === 0" class="jd-empty">
            JD 中暂未识别到可匹配的能力要求，可补充更具体的技术栈与职责描述后重试。
          </p>
          <template v-else>
            <p class="jd-score">
              JD 要求证据覆盖 <b>{{ jdResult.pct }}%</b>
              <span>命中 {{ jdResult.covered.length }} 项 · 缺失 {{ jdResult.missing.length }} 项</span>
            </p>
            <div class="jd-bar"><i :style="{ width: jdResult.pct + '%' }"></i></div>
          </template>
          <div v-if="jdResult.covered.length" class="jd-group">
            <b>命中关键词</b>
            <div><span v-for="k in jdResult.covered" :key="'c' + k" class="chip hit">{{ k }}</span></div>
          </div>
          <div v-if="jdResult.missing.length" class="jd-group">
            <b>缺失关键词（指简历中暂无证据，不代表你不具备）</b>
            <div><span v-for="k in jdResult.missing" :key="'m' + k" class="chip miss">{{ k }}</span></div>
          </div>
          <p v-if="jdResult.readiness" class="jd-readiness">
            改写就绪度：{{ readinessText(jdResult.readiness) }}
          </p>
          <div v-if="jdResult.questions.length" class="questions">
            <b>需要你补充的事实：</b>
            <p v-for="(q, i) in jdResult.questions" :key="i">· {{ q }}</p>
          </div>
          <p v-if="jdResult.safety" class="jd-safety">{{ jdResult.safety }}</p>
        </div>

        <ul class="issues">
          <li v-for="(it, i) in (report.issues || report.rule_analysis?.issues || [])" :key="i">
            <b>[P{{ it.priority }}] {{ it.title }}</b> — {{ it.description }}
            <p v-if="it.follow_up" class="follow">待确认：{{ it.follow_up }}</p>
          </li>
        </ul>
        <div v-if="report.model_analysis?.evidence_gaps?.length" class="gaps">
          <h3>证据缺口</h3>
          <p v-for="(g, i) in report.model_analysis.evidence_gaps" :key="i">
            · {{ g.claim }}：{{ g.explanation }}
          </p>
        </div>
        <div v-if="extraModelFields.length" class="gaps">
          <h3>模型其他输出</h3>
          <p v-for="([k, v]) in extraModelFields" :key="k">· {{ k }}：{{ typeof v === "string" ? v : JSON.stringify(v) }}</p>
        </div>
      </template>

      <p v-if="busy && slow" class="slow-hint">首次唤醒服务，约 3–8 秒，按钮已锁定，请稍候…</p>
      <div class="row">
        <button class="ghost" @click="step = 2">← 回去改文本</button>
        <button :disabled="busy || !report" @click="runRewrite">
          {{ busy ? "生成中…" : "生成安全改写建议 →" }}
        </button>
        <button class="ghost" :disabled="busy || !report" @click="exportPdf">导出 PDF</button>
      </div>

      <div v-if="rewrite" class="rewrite">
        <h3>改写建议（逐条核对，不编造）</h3>
        <div v-for="(r, i) in rewrite.rewritten" :key="i" class="pair">
          <div><b>原句</b><p>{{ r.original }}</p></div>
          <div><b>建议</b><p>{{ r.suggested }}</p></div>
        </div>
        <div v-if="rewrite.questions?.length" class="questions">
          <b>需要你补充的事实：</b>
          <p v-for="(q, i) in rewrite.questions" :key="i">· {{ q }}</p>
        </div>
      </div>
    </div>
  </section>
</template>
