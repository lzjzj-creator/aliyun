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
const ringStyle = computed(() => ({
  background: `conic-gradient(var(--teal) ${(pct.value || 0) * 3.6}deg, var(--surface-warm) 0deg)`,
}));
</script>

<template>
  <aside v-if="pct !== null || updates.length || gaps.length" class="profile-panel">
    <details :open="!collapsed" class="pp-body">
      <summary class="pp-summary">
        素材收集
        <b v-if="pct !== null">{{ pct }}%</b>
      </summary>
      <div v-if="pct !== null" class="pp-coverage">
        <div class="pp-ring" :style="ringStyle" role="progressbar"
             :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
          <span>{{ pct }}<small>%</small></span>
        </div>
        <div class="pp-coverage-side">
          <span class="pp-label">已收集</span>
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
      <p v-else-if="pct === 100 || (pct !== null && pct >= 80)" class="pp-hint">
        素材够写一份简历了，可以点下方卡片去一键填写。
      </p>
    </details>
  </aside>
</template>

<style scoped>
.profile-panel {
  background: #fff; border: 1px solid var(--line);
  border-radius: 12px; padding: 12px 14px; font-size: .82rem;
}
.pp-summary { cursor: pointer; color: var(--teal); font-weight: 600; list-style: none; }
.pp-summary::before { content: "▸ "; font-weight: 400; }
.pp-body[open] .pp-summary::before { content: "▾ "; }
.pp-summary b { margin-left: 6px; }
.pp-coverage { display: flex; align-items: center; gap: 14px; margin: 10px 0 4px; }
.pp-ring {
  flex: none; width: 64px; height: 64px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
}
.pp-ring > span {
  width: 46px; height: 46px; border-radius: 50%; background: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: .95rem; font-weight: 700; color: var(--teal);
}
.pp-ring small { font-size: .6rem; margin-left: 1px; }
.pp-coverage-side { flex: 1; min-width: 0; }
.pp-section { margin-top: 8px; }
.pp-label { display: block; font-size: .72rem; color: var(--mute); margin-bottom: 4px; }
.pp-chips { display: flex; flex-wrap: wrap; }
.pp-chips .chip { font-size: .72rem; }
.pp-none { font-size: .75rem; color: var(--mute); }
button.gap-chip { cursor: pointer; font: inherit; transition: transform .15s; }
button.gap-chip:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }
.pp-hint { margin: 8px 0 0; color: var(--teal); font-size: .78rem; }
</style>
