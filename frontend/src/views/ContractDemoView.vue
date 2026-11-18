<script setup>
// #10 契约渲染 demo：fixture template.json + autofill.json → ResumePreview。
// 验证"契约驱动渲染"：改 demo/v2/template.json 的契约，渲染即时跟随，零硬编码字段。
import { onMounted, ref } from "vue";
import ResumePreview from "../components/ResumePreview.vue";

const tpl = ref(null);
const data = ref(null);
const err = ref("");

onMounted(async () => {
  try {
    const [t, a] = await Promise.all([
      fetch("/demo/v2/template.json").then((r) => r.json()),
      fetch("/demo/v2/autofill.json").then((r) => r.json()),
    ]);
    tpl.value = t;
    data.value = a;
  } catch (e) {
    err.value = `fixture 加载失败：${e.message}`;
  }
});
</script>

<template>
  <section class="contract-demo">
    <h2 class="cd-title">契约渲染 demo（#demo/contract）</h2>
    <p class="cd-hint">渲染源：/demo/v2/template.json + autofill.json · 改契约 JSON → 渲染跟随变</p>
    <p v-if="err" class="warn">{{ err }}</p>
    <ResumePreview
      v-if="tpl && data"
      :template="tpl"
      :fields="data.fields"
      :evidence="data.evidence_map"
      :gaps="data.gaps" />
  </section>
</template>

<style scoped>
.cd-title { color: var(--teal); font-size: 1.05rem; margin: 0 0 4px; }
.cd-hint { font-size: .75rem; color: var(--mute); margin: 0 0 10px; }
.contract-demo { max-width: 820px; margin: 0 auto; }
</style>
