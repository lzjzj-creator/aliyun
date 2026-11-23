<script setup>
// 素材工作室（NotebookLM Studio 式右栏）：常驻——画像环 + 已收集素材卡 +
// 待补充缺口（点击回填）+ 常驻「去生成简历」CTA。消费 §4 profile 事件。
import { computed, ref } from "vue";
import UiIcon from "./UiIcon.vue";

const emit = defineEmits(["ask", "generate"]);
const props = defineProps({
  coverage: { type: Number, default: null },   // 0..1
  gaps: { type: Array, default: () => [] },    // 缺口 slot_id
  updates: { type: Array, default: () => [] }, // 累计 {slot, value}
  collapsed: { type: Boolean, default: false },
  labels: { type: Object, default: () => ({}) }, // slot→label，契约唯一来源（resume_template）
});

const EXTRA_CN = { experiences: "经历" };   // experiences 是事件名非模板槽位
const slotCn = (id) => props.labels[id] || EXTRA_CN[id] || id;

const pct = computed(() =>
  props.coverage == null ? null : Math.round(props.coverage * 100));

// 素材卡：最新的在前面；每条取槽位名 + 截断值
const materials = computed(() =>
  props.updates.slice(-8).reverse().map((u) => ({
    slot: u.slot,
    label: slotCn(u.slot),
    value: String(u.value ?? "").replace(/^（|）$/g, ""),
  })).filter((m) => m.value));
const moreMaterials = computed(() => Math.max(0, props.updates.length - 8));

// 缺口 chip 默认只露 4 个，避免决策点超 4 选项
const showAllGaps = ref(false);
const GAP_CAP = 4;
const visGaps = computed(() =>
  showAllGaps.value ? props.gaps : props.gaps.slice(0, GAP_CAP));
const moreGaps = computed(() => Math.max(0, props.gaps.length - GAP_CAP));

const canGenerate = computed(() => (pct.value ?? 0) > 0 || props.updates.length > 0);
const ctaTitle = computed(() =>
  (pct.value ?? 0) >= 80 ? "素材够写一份简历了" : "生成简历草稿");
const ctaSub = computed(() => {
  if (pct.value == null) return "先聊聊，聊出的素材会进简历";
  if (pct.value >= 80) return "去简历向导，一键生成";
  return `素材 ${pct.value}% · 也可以先生成再补`;
});

// SVG 圆环：r=15.5，周长 ≈ 97.4
const RING_LEN = 2 * Math.PI * 15.5;
const ringOffset = computed(() => RING_LEN * (1 - (pct.value || 0) / 100));
</script>

<template>
  <aside class="profile-panel studio" aria-label="素材工作室">
    <header class="studio-head">
      <b>素材工作室</b>
      <span v-if="pct !== null" class="studio-pct">{{ pct }}%</span>
    </header>

    <div class="studio-progress">
      <svg class="st-ring" viewBox="0 0 40 40" role="progressbar"
           :aria-valuenow="pct ?? 0" aria-valuemin="0" aria-valuemax="100"
           aria-label="素材覆盖率">
        <circle class="st-ring-track" cx="20" cy="20" r="15.5" />
        <circle class="st-ring-arc" cx="20" cy="20" r="15.5"
                :stroke-dasharray="RING_LEN" :stroke-dashoffset="ringOffset"
                transform="rotate(-90 20 20)" />
      </svg>
      <div class="studio-progress-txt">
        <b>{{ updates.length ? `已收集 ${updates.length} 项素材` : "聊出的素材会出现在这里" }}</b>
        <span>{{ pct === null ? "访谈开始后才统计覆盖率" : `覆盖率 ${pct}%` }}</span>
      </div>
    </div>

    <button class="studio-cta" :disabled="!canGenerate" @click="emit('generate')">
      <span class="cta-txt">
        {{ ctaTitle }}
        <span class="cta-sub">{{ ctaSub }}</span>
      </span>
      <UiIcon name="arrow-right" :size="16" class="cta-arrow" />
    </button>

    <section v-if="materials.length" class="studio-sec" aria-label="已收集素材">
      <h3 class="studio-sec-title">已收集<span class="pf-count">{{ updates.length }} 项</span></h3>
      <div v-for="m in materials" :key="m.slot" class="studio-source">
        <b>{{ m.label }}</b>
        <span class="ss-preview">{{ m.value }}</span>
      </div>
      <p v-if="moreMaterials" class="studio-empty">还有 {{ moreMaterials }} 项，已计入画像。</p>
    </section>

    <section v-if="gaps.length" class="studio-sec" aria-label="待补充">
      <h3 class="studio-sec-title">待补充 · 点击直接聊<span class="pf-count">{{ gaps.length }}</span></h3>
      <div class="pp-chips">
        <button v-for="g in visGaps" :key="g" class="chip miss gap-chip"
                @click="emit('ask', slotCn(g))">{{ slotCn(g) }}</button>
        <button v-if="moreGaps && !showAllGaps" class="chip miss gap-more"
                @click="showAllGaps = true">还有 {{ moreGaps }} 项</button>
      </div>
    </section>

    <p v-if="!updates.length && !gaps.length" class="studio-empty">
      实习、项目、比赛、社团——随便聊，我会把能写进简历的素材一条条存下来。
    </p>
  </aside>
</template>

<style scoped>
.profile-panel {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 14px; font-size: .82rem;
  box-shadow: var(--shadow-xs); display: flex; flex-direction: column;
  min-height: 0; overflow-y: auto;
}
.studio-head { display: flex; align-items: center; margin-bottom: 10px; }
.studio-head b { color: var(--ink); font-size: .85rem; }
.studio-pct { margin-left: auto; color: var(--agent); font-weight: 700; font-variant-numeric: tabular-nums; }
.studio-progress { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.st-ring { flex: none; width: 40px; height: 40px; }
.st-ring-track { fill: none; stroke: var(--bg-subtle); stroke-width: 4; }
.st-ring-arc {
  fill: none; stroke: var(--agent); stroke-width: 4; stroke-linecap: round;
  transition: stroke-dashoffset var(--dur-base) var(--ease);
}
.studio-progress-txt { min-width: 0; }
.studio-progress-txt b { display: block; font-size: .82rem; color: var(--ink); }
.studio-progress-txt span { font-size: .72rem; color: var(--ink-muted); }

.studio-cta {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 10px 12px; margin-bottom: 6px; text-align: left;
  border: 1.5px solid var(--brand); border-radius: var(--radius-md);
  background: var(--brand-soft); color: var(--brand);
  font: inherit; font-size: .85rem; font-weight: 600; cursor: pointer;
  transition: background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease);
}
.studio-cta:hover:not(:disabled) { background: #e2ebf6; border-color: var(--brand-hover); }
.studio-cta:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.studio-cta:disabled { opacity: .55; cursor: not-allowed; }
.studio-cta .cta-txt { flex: 1; min-width: 0; }
.studio-cta .cta-sub { display: block; font-size: .7rem; font-weight: 400; color: var(--ink-muted); margin-top: 1px; }
.studio-cta .cta-arrow { flex: none; }

.studio-sec { margin-top: 12px; }
.studio-sec-title {
  display: flex; align-items: center; gap: 8px; margin: 0 0 8px;
  font-size: .78rem; font-weight: 600; color: var(--ink);
}
.studio-sec-title .pf-count { margin-left: auto; font-weight: 400; color: var(--ink-muted); font-size: .72rem; }
.studio-source {
  padding: 9px 11px; margin-bottom: 6px;
  border: 1px solid var(--border); border-radius: 10px; background: var(--bg-canvas);
}
.studio-source b { display: block; font-size: .76rem; color: var(--agent); margin-bottom: 2px; }
.studio-source .ss-preview {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  font-size: .78rem; color: var(--ink-secondary); line-height: 1.5;
}
.pp-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.pp-chips .chip { font-size: .72rem; }
button.gap-chip { cursor: pointer; font: inherit; transition: transform .15s; }
button.gap-chip:hover { transform: translateY(-1px); }
button.gap-more { cursor: pointer; font: inherit; border-style: solid; color: var(--ink-muted); }
.studio-empty { font-size: .76rem; color: var(--ink-muted); line-height: 1.6; margin: 10px 0 0; }
</style>
