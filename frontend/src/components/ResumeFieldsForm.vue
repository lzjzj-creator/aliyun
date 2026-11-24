<script setup>
// 契约驱动的简历字段编辑器（#19）：不为字段写死 markup，
// 按 sections/type/fields 生成表单；gaps 字段黄标提示。
import { computed, onBeforeUnmount, ref, useId, watch } from "vue";
import { prepareResumePhoto } from "../utils/resumePhoto.js";
import { isValidResumePhoto } from "../../../backend/resume_assets/render.mjs";

const props = defineProps({
  template: { type: Object, required: true },
  modelValue: { type: Object, default: () => ({}) }, // fields
  gaps: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  context: { type: [String, Number], default: 0 }, // parent generation, not just document id
});
const emit = defineEmits(["update:modelValue", "photo-busy"]);
const photoId = useId();
const photoError = ref("");
const photoBusy = ref(false);
const hasPhoto = computed(() => !!props.modelValue._meta && Object.hasOwn(props.modelValue._meta, "photo"));
const photoSrc = computed(() => isValidResumePhoto(props.modelValue._meta?.photo) ? props.modelValue._meta.photo : "");
let mounted = true;
let pendingPhoto = null;

function finishPhoto(request) {
  if (pendingPhoto !== request) return;
  pendingPhoto = null;
  photoBusy.value = false;
  emit("photo-busy", { ...request, busy: false });
}
function cancelPhoto() {
  if (pendingPhoto) finishPhoto(pendingPhoto);
}
watch(() => props.context, () => { cancelPhoto(); photoError.value = ""; }, { flush: "sync" });
watch(() => props.disabled, (disabled) => { if (disabled) cancelPhoto(); }, { flush: "sync" });
onBeforeUnmount(() => { mounted = false; cancelPhoto(); });

function mergePhoto(photo) {
  // Read at completion time: text/manual metadata may change during encoding.
  const meta = { ...(props.modelValue._meta || {}) };
  if (photo === undefined) delete meta.photo;
  else meta.photo = photo;
  emit("update:modelValue", { ...props.modelValue, _meta: meta });
}
async function selectPhoto(event) {
  const file = event.target.files?.[0];
  event.target.value = ""; // same file may be retried, including after an error
  if (!mounted || props.disabled || !file) return;
  const request = { context: props.context, request: Symbol("photo") };
  pendingPhoto = request; // older finally blocks must not release this request
  photoError.value = "";
  photoBusy.value = true;
  emit("photo-busy", { ...request, busy: true });
  const current = () => mounted && pendingPhoto === request && props.context === request.context && !props.disabled;
  try {
    const photo = await prepareResumePhoto(file);
    if (current()) mergePhoto(photo);
  } catch (error) {
    if (current()) photoError.value = error.message || "照片处理失败，请重新选择。";
  } finally {
    if (current()) finishPhoto(request);
  }
}
function removePhoto() {
  if (!mounted || props.disabled) return;
  cancelPhoto();
  photoError.value = "";
  mergePhoto(undefined);
}

const gapMap = computed(() => {
  const m = {};
  for (const g of props.gaps) m[g.field_path] = { reason: g.reason || "待补充", req: g.required !== false };
  return m;
});
const gapAt = (p) => gapMap.value[p]?.reason;
const gapReq = (p) => gapMap.value[p]?.req !== false;
// 必填缺口黄标警示；可选缺口中性提示（不强制填写）。
const gapClass = (p) => (gapAt(p) ? (gapReq(p) ? "gap" : "opt-gap") : "");

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
  <fieldset class="rff" :disabled="disabled">
    <section class="rff-sec rff-photo">
      <label :for="photoId" class="rff-title">照片（可选）</label>
      <p :id="`${photoId}-help`" class="rff-photo-help">照片随简历保存，不发送给 AI。支持 JPEG / PNG，最大 5 MB；自动处理为不超过 240×320 像素、30 KB 的 JPEG。</p>
      <img v-if="photoSrc" class="rff-photo-preview" :src="photoSrc" alt="当前简历照片" />
      <p v-else-if="hasPhoto" class="rff-photo-error" role="alert">已保存的照片无效，请重新上传或移除后导出。</p>
      <label :for="photoId" class="rff-photo-action">{{ hasPhoto ? "替换照片" : "上传照片" }}</label>
      <input :id="photoId" class="rff-photo-input" type="file" accept="image/jpeg,image/png"
             :disabled="disabled" :aria-describedby="`${photoId}-help`" @change="selectPhoto" />
      <button v-if="hasPhoto || photoBusy" type="button" class="rff-photo-action" :disabled="disabled" @click="removePhoto">移除照片</button>
      <p v-if="photoBusy" class="rff-photo-help" role="status" aria-live="polite">正在处理照片，请稍候再保存或导出；可重新选择照片。</p>
      <p v-if="photoError" class="rff-photo-error" role="alert">{{ photoError }}</p>
    </section>
    <section v-for="sec in sections" :key="sec.key" class="rff-sec" :data-key="sec.key">
      <h3 class="rff-title">
        {{ sec.title }}
        <span v-if="isOptSec(sec)" class="rff-opt">可选</span>
        <span v-if="sec.rules" class="rff-rule">{{ sec.rules }}</span>
      </h3>

      <!-- kv：基本信息 -->
      <div v-if="sec.type === 'kv'" class="rff-kv">
        <label v-for="f in sec.fields" :key="f.key" class="rff-field" :class="gapClass(`${sec.key}.${f.key}`)">
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
                 class="rff-field" :class="gapClass(`${sec.key}.${i}.${f.key}`)">
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
        <p v-if="!(modelValue[sec.key] || []).length && gapAt(sec.key)" class="rff-gap-hint" :class="{ opt: !gapReq(sec.key) }">
          {{ gapReq(sec.key) ? "【待补充】" : "" }}{{ gapAt(sec.key) }}
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
          <label class="rff-field" :class="gapClass(`${sec.key}.groups.${gi}`)">
            <span>技能（顿号分隔）</span>
            <input :value="join(g.items)"
                   @input="setNested(sec.key, 'groups', modelValue[sec.key].groups.map((x, xi) => xi === gi ? { ...x, items: split($event.target.value) } : x))" />
          </label>
        </div>
        <p v-if="!(modelValue[sec.key]?.groups || []).length && gapAt(sec.key)" class="rff-gap-hint" :class="{ opt: !gapReq(sec.key) }">
          {{ gapReq(sec.key) ? "【待补充】" : "" }}{{ gapAt(sec.key) }}
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
        <label class="rff-field" :class="gapClass(`${sec.key}.text`)">
          <textarea rows="2" :value="modelValue[sec.key]?.text || ''"
                    @input="setNested(sec.key, 'text', $event.target.value)"
                    :placeholder="sec.entry_fields?.[0]?.rules || ''" />
        </label>
      </template>
    </section>
  </fieldset>
</template>

<style scoped>
.rff { min-width: 0; border: 0; padding: 0; margin: 0; }
.rff-photo { border-bottom: 1px solid var(--border); padding-bottom: 14px; }
.rff-photo-help { font-size: .78rem; color: var(--ink-secondary); margin: 6px 0 10px; }
.rff-photo-preview { display: block; width: 72px; height: 96px; object-fit: contain; margin-bottom: 10px; }
.rff-photo-action { display: inline-flex; align-items: center; min-height: 44px; min-width: 44px; border: 1px solid var(--border-strong); border-radius: 8px; padding: 8px 12px; margin: 0 8px 8px 0; background: var(--bg-surface); color: var(--brand); font: inherit; font-size: .82rem; cursor: pointer; }
.rff-photo-input { display: block; min-height: 44px; max-width: 100%; font: inherit; font-size: .78rem; margin-bottom: 8px; }
.rff-photo-input::file-selector-button { min-height: 44px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--bg-surface); color: var(--brand); cursor: pointer; }
.rff-photo :focus-visible { outline: 3px solid var(--brand); outline-offset: 3px; }
.rff:disabled .rff-photo-action, .rff-photo-input:disabled { opacity: .5; cursor: default; }
.rff-photo-error { font-size: .78rem; color: var(--danger); margin: 8px 0; }
.rff-sec { margin-bottom: 18px; }
.rff-title { color: var(--brand); font-size: .95rem; margin: 0 0 8px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.rff-opt { font-size: .68rem; color: var(--ink-muted); border: 1px solid var(--border); border-radius: var(--radius-pill); padding: 0 6px; }
.rff-rule { font-size: .68rem; color: var(--ink-muted); font-weight: normal; }
.rff-kv { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.rff-field { display: flex; flex-direction: column; gap: 3px; margin-bottom: 6px; }
.rff-field > span { font-size: .78rem; color: var(--ink-secondary); }
.req { color: var(--danger); font-style: normal; }
.rff-field input, .rff-field textarea {
  border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px;
  font: inherit; font-size: .88rem; background: var(--bg-surface); width: 100%; color: var(--ink);
}
.rff-field.gap input, .rff-field.gap textarea { border-color: var(--coach); background: var(--coach-soft); }
.rff-field.opt-gap input, .rff-field.opt-gap textarea { border-color: var(--border-strong); }
.rff-field.opt-gap input::placeholder, .rff-field.opt-gap textarea::placeholder { color: var(--ink-muted); opacity: .8; }
.rff-entry { border: 1px dashed var(--border-strong); border-radius: 10px; padding: 10px; margin-bottom: 8px; background: var(--bg-surface); }
.rff-entry-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: .75rem; color: var(--ink-muted); }
.rff-entry-head .grp-name { flex: 1; border: 1px solid var(--border); border-radius: 8px; padding: 5px 9px; font-size: .85rem; margin-right: 8px; background: var(--bg-surface); color: var(--ink); }
.mini { border: 1px solid var(--border); background: var(--bg-surface); color: var(--ink-muted); border-radius: 6px; padding: 2px 8px; font-size: .72rem; cursor: pointer; }
.mini:hover { color: var(--danger); border-color: var(--danger); }
.rff-add {
  border: 1px dashed var(--brand-border); background: var(--bg-surface); color: var(--brand);
  border-radius: 999px; padding: 5px 14px; font-size: .8rem; cursor: pointer;
}
.rff-line { display: flex; gap: 6px; margin-bottom: 6px; }
.rff-line input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: .85rem; background: var(--bg-surface); color: var(--ink); }
.rff-line .grow { flex: 1; }
.rff-line .per { width: 90px; }
.rff-gap-hint { font-size: .78rem; color: var(--coach); background: var(--coach-soft); border-radius: 8px; padding: 6px 10px; margin: 4px 0 8px; }
.rff-gap-hint.opt { color: var(--ink-muted); background: var(--bg-subtle); }
@media (max-width: 640px) {
  .rff-kv { grid-template-columns: 1fr; }
}
</style>
