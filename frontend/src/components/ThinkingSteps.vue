<script setup>
// #14 思考步骤条：展示服务端步骤事件 + reasoning_summary（原始 CoT 不出服务端，前端只渲染摘要）
const props = defineProps({
  steps: { type: Array, default: () => [] },   // [{id,seq,kind,title,status,detail}]
  reasoning: { type: String, default: "" },
  live: { type: Boolean, default: false },     // 流式进行中展开，结束后默认收起
});

const KIND_CN = {
  recall: "回忆", plan: "编排", retrieve: "检索", grade: "校验",
  answer: "作答", extract: "抽取", guide: "引导", profile: "画像", wrap_up: "收尾",
};
const kindCn = (k) => KIND_CN[k] || k || "步骤";
</script>

<template>
  <div v-if="steps.length || reasoning" class="tsteps" :class="{ live }">
    <details :open="live" class="tsteps-body">
      <summary v-if="!live">
        <span class="tsteps-summary">思考过程 · {{ steps.length }} 步</span>
      </summary>
      <ol class="tsteps-list">
        <li v-for="s in steps" :key="s.id" :class="['tstep', s.status || 'running']">
          <span class="tstep-dot" aria-hidden="true"></span>
          <span class="tstep-kind">{{ kindCn(s.kind) }}</span>
          <span class="tstep-title">{{ s.title }}</span>
          <span v-if="s.detail" class="tstep-detail">{{ s.detail }}</span>
        </li>
      </ol>
      <p v-if="reasoning" class="tstep-reasoning">{{ reasoning }}</p>
    </details>
  </div>
</template>

<style scoped>
.tsteps {
  margin: 0 0 10px; font-size: .8rem;
  background: var(--bg-subtle); border: 1px solid var(--border-subtle);
  border-radius: 10px; padding: 8px 12px;
}
.tsteps-body summary {
  cursor: pointer; color: var(--ink-muted); font-size: .75rem; list-style: none;
  padding: 2px 0; display: flex; align-items: center; gap: 5px;
}
.tsteps-body summary::before {
  content: "▸"; color: var(--agent); transition: transform var(--dur-fast) var(--ease);
}
.tsteps-body[open] summary::before { content: "▾"; }
.tsteps-body summary:hover { color: var(--ink); }
.tsteps-list { list-style: none; margin: 8px 0 4px; padding: 0 0 0 4px; position: relative; }
/* 左侧竖向时间轴线 */
.tsteps-list::before {
  content: ""; position: absolute; left: 7px; top: 8px; bottom: 8px;
  width: 1.5px; background: var(--border-strong); border-radius: 1px;
}
.tstep {
  display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
  padding: 3px 0 3px 20px; position: relative; color: var(--ink-secondary); line-height: 1.45;
}
.tstep-dot {
  position: absolute; left: 0; top: .68em; width: 8px; height: 8px;
  border-radius: 50%; background: var(--step-idle);
}
.tstep.done .tstep-dot { background: var(--agent); }
.tstep.running .tstep-dot {
  background: var(--agent); animation: tpulse 1.1s ease-in-out infinite;
  box-shadow: 0 0 0 3px var(--agent-soft);
}
@keyframes tpulse { 50% { opacity: .35; } }
.tstep-kind {
  flex: none; font-size: .68rem; padding: 0 6px; border-radius: var(--radius-pill);
  background: var(--agent-soft); color: var(--agent); border: 1px solid var(--agent-border);
}
.tstep-title { font-weight: 600; color: var(--ink); }
.tstep-detail { color: var(--ink-muted); font-size: .75rem; overflow-wrap: anywhere; }
.tstep-reasoning {
  margin: 6px 0 0; padding: 8px 12px; border: 1px solid var(--agent-border);
  background: var(--agent-soft); border-radius: 8px;
  color: var(--ink-secondary); font-size: .78rem; line-height: 1.6;
}
</style>
