<script setup>
// 契约驱动的简历字段编辑器（#19）：不为字段写死 markup，
// 按 sections/type/fields 生成表单；gaps 字段黄标提示。
import { computed } from "vue";

const props = defineProps({
  template: { type: Object, required: true },
  modelValue: { type: Object, default: () => ({}) }, // fields
  gaps: { type: Array, default: () => [] },
});
const emit = defineEmits(["update:modelValue"]);

const gapMap = computed(() => {
  const m = {};
  for (const g of props.gaps) m[g.field_path] = g.reason || "待补充";
  return m;
});
const gapAt = (p) => gapMap.value[p];

const sections = computed(() =>
  (props.template.sections || []).slice().sort((a, b) => a.order - b.order));

// —— 读写 modelValue 的不可变工具 ——
function setKv(secKey, fKey, v) {
  emit("update:modelValue", {
    ...props.modelValue,
    [secKey]: { ...(props.modelValue[secKey] || {}), [fKey]: v },
  });
}
function setEntry(secKey, i, fKey, v) {
  const arr = (props.modelValue[secKey] || []).slice();
  arr[i] = { ...(arr[i] || {}), [fKey]: v };
  emit("update:modelValue", { ...props.modelValue, [secKey]: arr });
}
function setNested(secKey, nKey, v) {
  emit("update:modelValue", {
    ...props.modelValue,
    [secKey]: { ...(props.modelValue[secKey] || {}), [nKey]: v },
  });
}
function addEntry(sec) {
  const blank = {};
  for (const f of sec.entry_fields || []) {
    blank[f.key] = ["bullets", "tags"].includes(f.input) ? [] : "";
  }
  emit("update:modelValue", {
    ...props.modelValue,
    [sec.key]: [...(props.modelValue[sec.key] || []), blank],
  });
}
function removeEntry(secKey, i) {
  const arr = (props.modelValue[secKey] || []).slice();
  arr.splice(i, 1);
  emit("update:modelValue", { ...props.modelValue, [secKey]: arr });
}
const join = (v) => (Array.isArray(v) ? v.join("、") : v || "");
const split = (v) => v.split(/[、,，\n]/).map((s) => s.trim()).filter(Boolean);
const splitLines = (v) => v.split("\n").map((s) => s.trim()).filter(Boolean);

// 区块是否显示"可留空"提示
const isOptSec = (sec) => sec.required === false;
</script>

<template>
  <div class="rff">
    <section v-for="sec in sections" :key="sec.key" class="rff-sec" :data-key="sec.key">
      <h3 class="rff-title">
        {{ sec.title }}
        <span v-if="isOptSec(sec)" class="rff-opt">可选</span>
        <span v-if="sec.rules" class="rff-rule">{{ sec.rules }}</span>
      </h3>

      <!-- kv：基本信息 -->
      <div v-if="sec.type === 'kv'" class="rff-kv">
        <label v-for="f in sec.fields" :key="f.key" class="rff-field" :class="{ gap: gapAt(`${sec.key}.${f.key}`) }">
          <span>{{ f.label }}<i v-if="f.required" class="req">*</i></span>
          <input :value="modelValue[sec.key]?.[f.key] || ''"
                 @input="setKv(sec.key, f.key, $event.target.value)"
                 :placeholder="gapAt(`${sec.key}.${f.key}`) || f.rules || ''" />
        </label>
      </div>

      <!-- entries：教育/实习/项目 -->
      <template v-if="sec.type === 'entries'">
        <div v-for="(e, i) in (modelValue[sec.key] || [])" :key="i" class="rff-entry">
          <div class="rff-entry-head">
            <span>#{{ i + 1 }}</span>
            <button type="button" class="mini" @click="removeEntry(sec.key, i)">删除</button>
          </div>
          <label v-for="f in sec.entry_fields" :key="f.key"
                 class="rff-field" :class="{ gap: gapAt(`${sec.key}.${i}.${f.key}`) }">
            <span>{{ f.label }}<i v-if="f.required" class="req">*</i></span>
            <textarea v-if="f.input === 'bullets'" rows="3"
                      :value="(e[f.key] || []).join('\n')"
                      @input="setEntry(sec.key, i, f.key, splitLines($event.target.value))"
                      :placeholder="'每行一条：' + (f.rubric || '')" />
            <input v-else-if="f.input === 'tags'" :value="join(e[f.key])"
                   @input="setEntry(sec.key, i, f.key, split($event.target.value))"
                   :placeholder="(f.rules || '顿号/逗号分隔')" />
            <input v-else :value="e[f.key] || ''"
                   @input="setEntry(sec.key, i, f.key, $event.target.value)"
                   :placeholder="gapAt(`${sec.key}.${i}.${f.key}`) || f.rules || ''" />
          </label>
        </div>
        <p v-if="!(modelValue[sec.key] || []).length && gapAt(sec.key)" class="rff-gap-hint">
          【待补充】{{ gapAt(sec.key) }}
        </p>
        <button v-if="!sec.max || (modelValue[sec.key] || []).length < sec.max"
                type="button" class="rff-add" @click="addEntry(sec)">
          ＋ 添加{{ sec.title.replace("经历", "").replace("背景", "") }}
        </button>
      </template>

      <!-- skillgroups -->
      <template v-if="sec.type === 'skillgroups'">
        <div v-for="(g, gi) in (modelValue[sec.key]?.groups || [])" :key="gi" class="rff-entry">
          <div class="rff-entry-head">
            <input class="grp-name" :value="g.name" placeholder="分组名（语言/框架/工具）"
                   @input="setNested(sec.key, 'groups', modelValue[sec.key].groups.map((x, xi) => xi === gi ? { ...x, name: $event.target.value } : x))" />
            <button type="button" class="mini"
                    @click="setNested(sec.key, 'groups', modelValue[sec.key].groups.filter((_, xi) => xi !== gi))">删除</button>
          </div>
          <label class="rff-field" :class="{ gap: gapAt(`${sec.key}.groups.${gi}`) }">
            <span>技能（顿号分隔）</span>
            <input :value="join(g.items)"
                   @input="setNested(sec.key, 'groups', modelValue[sec.key].groups.map((x, xi) => xi === gi ? { ...x, items: split($event.target.value) } : x))" />
          </label>
        </div>
        <p v-if="!(modelValue[sec.key]?.groups || []).length && gapAt(sec.key)" class="rff-gap-hint">
          【待补充】{{ gapAt(sec.key) }}
        </p>
        <button type="button" class="rff-add"
                @click="setNested(sec.key, 'groups', [...(modelValue[sec.key]?.groups || []), { name: '', items: [] }])">
          ＋ 添加分组
        </button>
      </template>

      <!-- lines：荣誉奖项 -->
      <template v-if="sec.type === 'lines'">
        <div v-for="(it, i) in (modelValue[sec.key]?.items || [])" :key="i" class="rff-line">
          <input class="grow" :value="it.text" placeholder="奖项全称（含级别）"
                 @input="setNested(sec.key, 'items', modelValue[sec.key].items.map((x, xi) => xi === i ? { ...x, text: $event.target.value } : x))" />
          <input class="per" :value="it.period" placeholder="时间"
                 @input="setNested(sec.key, 'items', modelValue[sec.key].items.map((x, xi) => xi === i ? { ...x, period: $event.target.value } : x))" />
          <button type="button" class="mini"
                  @click="setNested(sec.key, 'items', modelValue[sec.key].items.filter((_, xi) => xi !== i))">删</button>
        </div>
        <button type="button" class="rff-add"
                @click="setNested(sec.key, 'items', [...(modelValue[sec.key]?.items || []), { text: '', period: '' }])">
          ＋ 添加奖项
        </button>
      </template>

      <!-- text：自我评价 -->
      <template v-if="sec.type === 'text'">
        <label class="rff-field" :class="{ gap: gapAt(`${sec.key}.text`) }">
          <textarea rows="2" :value="modelValue[sec.key]?.text || ''"
                    @input="setNested(sec.key, 'text', $event.target.value)"
                    :placeholder="sec.entry_fields?.[0]?.rules || ''" />
        </label>
      </template>
    </section>
  </div>
</template>

<style scoped>
.rff-sec { margin-bottom: 18px; }
.rff-title { color: var(--teal); font-size: .95rem; margin: 0 0 8px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.rff-opt { font-size: .68rem; color: var(--mute); border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; }
.rff-rule { font-size: .68rem; color: var(--mute); font-weight: normal; }
.rff-kv { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.rff-field { display: flex; flex-direction: column; gap: 3px; margin-bottom: 6px; }
.rff-field > span { font-size: .78rem; color: var(--ink-soft); }
.req { color: var(--err); font-style: normal; }
.rff-field input, .rff-field textarea {
  border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px;
  font: inherit; font-size: .88rem; background: #fff; width: 100%;
}
.rff-field.gap input, .rff-field.gap textarea { border-color: var(--warn); background: var(--warn-bg); }
.rff-entry { border: 1px dashed var(--line); border-radius: 10px; padding: 10px; margin-bottom: 8px; background: #fff; }
.rff-entry-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: .75rem; color: var(--mute); }
.rff-entry-head .grp-name { flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: 5px 9px; font-size: .85rem; margin-right: 8px; }
.mini { border: 1px solid var(--line); background: #fff; color: var(--mute); border-radius: 6px; padding: 2px 8px; font-size: .72rem; cursor: pointer; }
.mini:hover { color: var(--err); border-color: var(--err); }
.rff-add {
  border: 1px dashed var(--teal); background: #fff; color: var(--teal);
  border-radius: 999px; padding: 5px 14px; font-size: .8rem; cursor: pointer;
}
.rff-line { display: flex; gap: 6px; margin-bottom: 6px; }
.rff-line input { border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; font-size: .85rem; }
.rff-line .grow { flex: 1; }
.rff-line .per { width: 90px; }
.rff-gap-hint { font-size: .78rem; color: var(--warn); background: var(--warn-bg); border-radius: 8px; padding: 6px 10px; margin: 4px 0 8px; }
@media (max-width: 640px) {
  .rff-kv { grid-template-columns: 1fr; }
}
</style>
