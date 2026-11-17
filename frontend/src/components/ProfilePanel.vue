<script setup>
// #15 画像面板：覆盖度进度 + 缺口提醒 + 本轮/累计已收集事实（消费 §4 profile 事件）
import { computed } from "vue";

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
</script>

<template>
  <aside v-if="pct !== null || updates.length || gaps.length" class="profile-panel">
    <details :open="!collapsed" class="pp-body">
      <summary class="pp-summary">
        素材收集
        <b v-if="pct !== null">{{ pct }}%</b>
      </summary>
      <div v-if="pct !== null" class="pp-bar" role="progressbar"
           :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
        <i :style="{ width: pct + '%' }"></i>
      </div>
      <div v-if="recent.length" class="pp-section">
        <span class="pp-label">已收集</span>
        <div class="pp-chips">
          <span v-for="(u, i) in recent" :key="i" class="chip hit">
            {{ slotCn(u.slot) }}<template v-if="u.value && !String(u.value).startsWith('（')">·{{ u.value }}</template>
          </span>
        </div>
      </div>
      <div v-if="gaps.length" class="pp-section">
        <span class="pp-label">待补充</span>
        <div class="pp-chips">
          <span v-for="g in gaps" :key="g" class="chip miss">{{ slotCn(g) }}</span>
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
  margin: 0 0 10px; background: #fff; border: 1px solid var(--line);
  border-radius: 12px; padding: 10px 14px; font-size: .82rem;
}
.pp-summary { cursor: pointer; color: var(--teal); font-weight: 600; list-style: none; }
.pp-summary::before { content: "▸ "; font-weight: 400; }
.pp-body[open] .pp-summary::before { content: "▾ "; }
.pp-summary b { margin-left: 6px; }
.pp-bar { height: 6px; border-radius: 999px; background: #f3eee6; overflow: hidden; margin: 8px 0; }
.pp-bar i { display: block; height: 100%; background: var(--teal); border-radius: 999px; transition: width .5s; }
.pp-section { margin-top: 6px; }
.pp-label { display: block; font-size: .72rem; color: #9a9186; margin-bottom: 4px; }
.pp-chips { display: flex; flex-wrap: wrap; }
.pp-chips .chip { font-size: .72rem; }
.pp-hint { margin: 8px 0 0; color: var(--teal); font-size: .78rem; }
</style>
