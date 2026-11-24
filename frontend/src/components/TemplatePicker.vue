<script setup>
import { RESUME_TEMPLATES } from "./resumeTemplates";

const props = defineProps({
  modelValue: { type: String, default: "classic" },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue"]);

function pick(key) {
  if (!props.disabled && key !== props.modelValue) emit("update:modelValue", key);
}
</script>

<template>
  <div class="tpl-picker" role="radiogroup" aria-label="简历模板">
    <button
      v-for="t in RESUME_TEMPLATES" :key="t.key" type="button"
      class="tpl-card" :class="{ on: modelValue === t.key }"
      :aria-checked="modelValue === t.key" role="radio"
      :disabled="disabled" @click="pick(t.key)">
      <span class="tpl-thumb" :data-layout="t.key" aria-hidden="true">
        <i v-if="t.key === 'modern'" class="tpl-side"></i>
        <span class="tpl-lines">
          <i class="tpl-line w60"></i><i class="tpl-line w90"></i>
          <i class="tpl-line w75"></i><i class="tpl-line w50"></i>
        </span>
      </span>
      <span class="tpl-meta">
        <span class="tpl-name">{{ t.name }}<em class="tpl-badge">{{ t.badge }}</em></span>
        <span class="tpl-desc">{{ t.desc }}</span>
      </span>
    </button>
  </div>
</template>

<style scoped>
.tpl-picker { display: flex; gap: 8px; }
.tpl-card {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px 6px 6px;
  border: 1px solid var(--border); border-radius: 10px; background: var(--bg-surface);
  cursor: pointer; font: inherit; text-align: left; color: var(--ink);
}
.tpl-card.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand-border, var(--brand)); }
.tpl-card:disabled { opacity: .5; cursor: default; }
.tpl-thumb {
  width: 34px; height: 46px; border: 1px solid var(--border); border-radius: 3px;
  background: #fff; display: flex; overflow: hidden; flex: 0 0 auto;
}
.tpl-side { width: 34%; background: #2b3a4a; flex: 0 0 auto; }
.tpl-lines { flex: 1; padding: 4px 3px; display: flex; flex-direction: column; gap: 3px; }
.tpl-line { height: 2.5px; background: #d4d9df; border-radius: 1px; }
.tpl-thumb[data-layout="modern"] .tpl-line { background: #c4cdd6; }
.tpl-line.w60 { width: 60%; } .tpl-line.w90 { width: 90%; }
.tpl-line.w75 { width: 75%; } .tpl-line.w50 { width: 50%; }
.tpl-meta { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.tpl-name { font-size: .8rem; font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 5px; }
.tpl-badge {
  font-style: normal; font-size: .62rem; font-weight: 500; color: var(--brand);
  border: 1px solid var(--brand-border, var(--brand)); border-radius: 99px; padding: 0 5px;
}
.tpl-desc { font-size: .68rem; color: var(--ink-muted); }
@media (max-width: 640px) {
  .tpl-card { flex: 1; }
  .tpl-desc { display: none; }
}
</style>
