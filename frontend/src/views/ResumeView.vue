<script setup>
import { ref } from "vue";
import { analyzeResume, rewriteBullets } from "../api";

const MAX = 12000;
const step = ref(1); // 1输入 2核对 3结果
const resumeText = ref("");
const jobText = ref("");
const fileMsg = ref("");
const busy = ref(false);
const report = ref(null);
const rewrite = ref(null);
const errMsg = ref("");

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
  busy.value = true; errMsg.value = ""; report.value = null; rewrite.value = null;
  try {
    const r = await analyzeResume(resumeText.value, jobText.value);
    if (!r.ok) { errMsg.value = r.error?.message || "分析失败"; return; }
    report.value = r;
    step.value = 3;
  } catch (e) { errMsg.value = `请求失败：${e.message}`; }
  finally { busy.value = false; }
}

const REWRITE_SEED = /[。；;\n]/;
async function runRewrite() {
  busy.value = true; errMsg.value = "";
  try {
    const bullets = resumeText.value.split(REWRITE_SEED)
      .map((s) => s.trim()).filter((s) => s.length >= 8).slice(0, 12);
    const r = await rewriteBullets(bullets);
    if (!r.ok) { errMsg.value = r.error?.message || "改写失败"; return; }
    rewrite.value = r.data;
  } catch (e) { errMsg.value = `请求失败：${e.message}`; }
  finally { busy.value = false; }
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
      <textarea v-model="resumeText" :maxlength="MAX"
        placeholder="或直接粘贴简历文本…" rows="10" />
      <p class="count">{{ resumeText.length }} / {{ MAX }}</p>
      <button :disabled="!resumeText.trim()" @click="step = 2">下一步：核对文本 →</button>
    </div>

    <div v-else-if="step === 2" class="pane">
      <h2>第二步：核对提取结果（可修改）</h2>
      <textarea v-model="resumeText" rows="9" :maxlength="MAX" />
      <h3>目标岗位 JD（可选）</h3>
      <textarea v-model="jobText" rows="4" :maxlength="MAX"
        placeholder="粘贴招聘 JD 进入岗位定向分析；留空则通用体检" />
      <div class="row">
        <button class="ghost" @click="step = 1">← 返回</button>
        <button :disabled="busy" @click="runAnalyze">{{ busy ? "分析中…" : "开始分析 →" }}</button>
      </div>
    </div>

    <div v-else class="pane">
      <h2>诊断结果</h2>
      <p v-if="errMsg" class="warn">{{ errMsg }}</p>

      <template v-if="report">
        <p class="summary">{{ report.summary || report.rule_analysis?.summary }}</p>
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
      </template>

      <div class="row">
        <button class="ghost" @click="step = 2">← 回去改文本</button>
        <button :disabled="busy || !report" @click="runRewrite">
          {{ busy ? "生成中…" : "生成安全改写建议 →" }}
        </button>
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
