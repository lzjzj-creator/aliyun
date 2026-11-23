<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { renderResume } from "../../../backend/resume_assets/render.mjs";

const props = defineProps({
  template: { type: Object, required: true },
  fields: { type: Object, default: () => ({}) },
  // Compatibility only: editing aids remain in the external form/optimization panel.
  evidence: { type: Object, default: () => ({}) },
  gaps: { type: Array, default: () => [] },
  compare: { type: Object, default: null },
  focus: { type: String, default: "" },
  layout: { type: String, default: "classic" },
});

const html = computed(() => renderResume(props.template, props.fields, props.layout));
const viewport = ref(null);
const sheet = ref(null);
const scale = ref(1);
const width = ref(0);
const height = ref(0);
const frameStyle = computed(() => ({ width: `${width.value}px`, height: `${height.value}px` }));
const sheetStyle = computed(() => ({ transform: `scale(${scale.value})` }));
let observer;

function measure() {
  if (!viewport.value || !sheet.value) return;
  // offset dimensions are untransformed: measuring a scaled bounding rect would
  // feed the previous scale back into the next ResizeObserver update.
  const availableWidth = viewport.value.clientWidth;
  const paperWidth = sheet.value.offsetWidth;
  const paperHeight = sheet.value.offsetHeight;
  if (availableWidth <= 0 || paperWidth <= 0) return;
  scale.value = Math.min(1, availableWidth / paperWidth);
  width.value = paperWidth * scale.value;
  height.value = paperHeight * scale.value;
}

onMounted(() => {
  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(measure);
    observer.observe(viewport.value);
    // Content edits and late local-font loading can change height without a window resize.
    observer.observe(sheet.value);
  } else {
    window.addEventListener("resize", measure);
  }
  measure();
});
watch(html, measure, { flush: "post" });
onBeforeUnmount(() => {
  observer?.disconnect();
  window.removeEventListener("resize", measure);
});
</script>

<template>
  <div ref="viewport" class="resume-preview">
    <div class="resume-preview-frame" :style="frameStyle">
      <div ref="sheet" class="resume-preview-sheet" :style="sheetStyle" v-html="html"></div>
    </div>
  </div>
</template>

<!-- S5：预览用 GB2312 子集 woff2 + swap；服务端 PDF 仍走 fonts.css 全量 TTF -->
<style src="../../../backend/resume_assets/fonts-preview.css"></style>
<style src="../../../backend/resume_assets/resume.css"></style>

<style scoped>
.resume-preview {
  width: 100%;
  min-width: 0;
  overflow: hidden;
}

.resume-preview-frame {
  position: relative;
  margin: 0 auto;
}

.resume-preview-sheet {
  width: 210mm;
  position: absolute;
  top: 0;
  left: 0;
  transform-origin: top left;
  display: flow-root;
}

@media print {
  .resume-preview,
  .resume-preview-frame,
  .resume-preview-sheet {
    width: auto !important;
    height: auto !important;
    margin: 0;
    position: static;
    overflow: visible;
    transform: none !important;
  }
}
</style>
