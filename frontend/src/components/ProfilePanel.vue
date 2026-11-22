<script setup>
// #15 画像面板：覆盖率环 + 缺口提醒 + 本轮/累计已收集事实（消费 §4 profile 事件）
import { computed } from "vue";

const emit = defineEmits(["ask"]);   // 点「待补充」chip → 回填引导句
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
const recent = computed(() => props.updates.slice(-6));

// SVG 圆环：r=26，周长 2π·26 ≈ 163.36
const RING_LEN = 2 * Math.PI * 26;
const ringOffset = computed(() => RING_LEN * (1 - (pct.value || 0) / 100));
</script>

<template>
  <aside v-if="pct !== null || updates.length || gaps.length" class="profile-panel">
    <details :open="!collapsed" class="pp-body">
      <summary class="pp-summary">
        素材收集进度
        <b v-if="pct !== null">{{ pct }}%</b>
      </summary>

      <div v-if="pct !== null" class="pp-coverage">
        <svg class="pp-ring" viewBox="0 0 64 64" role="progressbar"
             :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
          <circle class="pp-ring-track" cx="32" cy="32" r="26" />
          <circle class="pp-ring-arc" cx="32" cy="32" r="26"
                  :stroke-dasharray="RING_LEN" :stroke-dashoffset="ringOffset"
                  transform="rotate(-90 32 32)" />
          <text class="pp-ring-num" x="32" y="37" text-anchor="middle">{{ pct }}%</text>
        </svg>
        <div class="pp-coverage-side">
          <span class="pp-label">已收集 {{ updates.length ? `· ${updates.length} 项` : "" }}</span>
          <div class="pp-chips">
            <span v-for="(u, i) in recent" :key="i" class="chip hit">
              {{ slotCn(u.slot) }}<template v-if="u.value && !String(u.value).startsWith('（')">·{{ u.value }}</template>
            </span>
            <span v-if="!recent.length" class="pp-none">暂无</span>
          </div>
        </div>
      </div>

      <div v-if="gaps.length" class="pp-section">
        <span class="pp-label">待补充 · 点击直接聊</span>
        <div class="pp-chips">
          <button v-for="g in gaps" :key="g" class="chip miss gap-chip"
                  @click="emit('ask', slotCn(g))">{{ slotCn(g) }}</button>
        </div>
      </div>

      <p v-if="pct === 0" class="pp-hint">
        还没收集到素材，先聊聊你的经历吧——实习、项目、比赛都算。
      </p>
      <a v-else-if="pct !== null && pct >= 80" class="pp-cta" href="#/resume">
        <span class="pp-cta-title">素材够写一份简历了</span>
        <span class="pp-cta-sub">去简历向导，一键生成 →</span>
      </a>
      <p v-else-if="pct !== null" class="pp-hint">
        继续聊，把缺口补上就能生成更准的简历。
      </p>
    </details>
  </aside>
</template>

<style scoped>
.profile-panel {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 14px; font-size: .82rem;
  box-shadow: var(--shadow-xs);
}
.pp-summary { cursor: pointer; color: var(--ink); font-weight: 600; font-size: .85rem; list-style: none; display: flex; align-items: center; gap: 5px; }
.pp-summary::before { content: "▸"; font-weight: 400; color: var(--agent); }
.pp-body[open] .pp-summary::before { content: "▾"; }
.pp-summary b { margin-left: auto; color: var(--agent); font-variant-numeric: tabular-nums; }
.pp-coverage { display: flex; align-items: center; gap: 14px; margin: 12px 0 6px; }
.pp-ring { flex: none; width: 64px; height: 64px; }
.pp-ring-track { fill: none; stroke: var(--bg-subtle); stroke-width: 7; }
.pp-ring-arc {
  fill: none; stroke: var(--agent); stroke-width: 7; stroke-linecap: round;
  transition: stroke-dashoffset var(--dur-base) var(--ease);
}
.pp-ring-num { font-size: 13px; font-weight: 700; fill: var(--agent); font-variant-numeric: tabular-nums; }
.pp-coverage-side { flex: 1; min-width: 0; }
.pp-section { margin-top: 10px; }
.pp-label { display: block; font-size: .72rem; color: var(--ink-muted); margin-bottom: 4px; }
.pp-chips { display: flex; flex-wrap: wrap; }
.pp-chips .chip { font-size: .72rem; }
.pp-none { font-size: .75rem; color: var(--ink-muted); }
button.gap-chip { cursor: pointer; font: inherit; transition: transform .15s; }
button.gap-chip:hover { transform: translateY(-1px); }
.pp-hint { margin: 10px 0 0; color: var(--ink-secondary); font-size: .78rem; }
.pp-cta {
  display: block; margin-top: 12px; padding: 11px 13px; text-decoration: none;
  background: var(--agent); border-radius: 10px; color: #fff;
  transition: background var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
}
.pp-cta:hover { background: var(--agent-hover); transform: translateY(-1px); }
.pp-cta-title { display: block; font-size: .85rem; font-weight: 650; }
.pp-cta-sub { display: block; font-size: .72rem; opacity: .85; margin-top: 2px; }
</style>
