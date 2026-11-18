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
      <summary v-if="!live">思考过程 · {{ steps.length }} 步</summary>
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
.tsteps { margin: 0 0 8px; font-size: .8rem; }
.tsteps-body summary {
  cursor: pointer; color: var(--mute); font-size: .75rem; list-style: none;
  padding: 2px 0;
}
.tsteps-body summary::before { content: "▸ "; }
.tsteps-body[open] summary::before { content: "▾ "; }
.tsteps-body summary:hover { color: var(--teal); }
.tsteps-list { list-style: none; margin: 6px 0 4px; padding: 0; }
.tstep {
  display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
  padding: 2px 0 2px 14px; position: relative; color: var(--ink-soft); line-height: 1.45;
}
.tstep + .tstep { border-top: 1px dashed var(--line); }
.tstep-dot {
  position: absolute; left: 0; top: .62em; width: 7px; height: 7px;
  border-radius: 50%; background: var(--step-idle);
}
.tstep.done .tstep-dot { background: var(--teal); }
.tstep.running .tstep-dot {
  background: var(--accent); animation: tpulse 1s ease-in-out infinite;
}
@keyframes tpulse { 50% { opacity: .3; } }
.tstep-kind {
  flex: none; font-size: .68rem; padding: 0 6px; border-radius: 999px;
  background: rgba(20, 82, 90, .08); color: var(--teal);
  border: 1px solid rgba(20, 82, 90, .18);
}
.tstep-title { font-weight: 600; color: var(--ink); }
.tstep-detail { color: var(--mute); font-size: .75rem; overflow-wrap: anywhere; }
.tstep-reasoning {
  margin: 4px 0 0; padding: 6px 10px; border-left: 3px solid var(--accent);
  background: var(--accent-bg); border-radius: 0 8px 8px 0; color: var(--ink-soft); font-size: .78rem;
}
</style>
