<script setup>
// GuideTour 站内气泡教程（v5 §6）：单焦点气泡 + 进度点 + 可跳过。
// 每账号只播一次（服务端 guide_seen_at），换设备不重播。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { markGuideSeen } from "../api/auth";

const emit = defineEmits(["done", "skip", "goto"]);

// 步骤定义（§4：3–4 步）：target=CSS 选择器锚点；nav=先导航到哪个 hash 再锚定（可选）
const STEPS = [
  { title: "欢迎来知脉",
    text: "先聊聊你的经历——说得越具体，简历素材越扎实。",
    target: ".composer", nav: "#/chat" },
  { title: "经历证据库",
    text: "聊到的经历会抽成「事实」存进这里，每条都可核对、改删，还能随手记、对话记。",
    target: null, nav: "#/memory" },
  { title: "简历向导与简历库",
    text: "选好素材来源一键生成初稿，人工微调后导出；不同岗位可存多份定制版。",
    target: null, nav: "#/resume" },
  { title: "准备好了",
    text: "初始化画像已经就位，访谈不会再追问基础信息——直接从一段经历聊起吧。",
    target: ".composer", nav: "#/chat" },
];

const idx = ref(0);
const step = computed(() => STEPS[idx.value]);
const bubbleStyle = ref({});
const bubbleEl = ref(null);
const seenMarked = ref(false);
const busy = ref(false);

async function place() {
  await nextTick();
  const sel = step.value?.target;
  const el = sel ? document.querySelector(sel) : null;
  if (el) {
    const r = el.getBoundingClientRect();
    const bh = bubbleEl.value?.offsetHeight || 180;
    const bw = bubbleEl.value?.offsetWidth || 300;
    // 目标在视口下半部（如底部输入框）→ 气泡放其上方，否则下方
    const top = r.top > window.innerHeight * 0.55
      ? Math.max(r.top - bh - 10, 12)
      : Math.min(r.bottom + 8, Math.max(window.innerHeight - bh - 12, 12));
    const left = Math.min(Math.max(r.left, 12), Math.max(window.innerWidth - bw - 12, 12));
    bubbleStyle.value = { top: `${top}px`, left: `${left}px`, transform: "none" };
    el.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  } else {
    bubbleStyle.value = { top: "50%", left: "50%", transform: "translate(-50%,-50%)" };
  }
}

async function markDone() {
  if (seenMarked.value) return;
  seenMarked.value = true;
  try { await markGuideSeen(); } catch { /* 失败不阻塞 UI */ }
}

async function next() {
  busy.value = true;
  if (idx.value >= STEPS.length - 1) {
    await markDone();
    emit("done");
    busy.value = false;
    return;
  }
  const target = STEPS[idx.value + 1].nav;
  if (target && location.hash !== target) emit("goto", target);
  idx.value++;
  busy.value = false;
}

async function skip() {
  await markDone();
  emit("skip");
}

// 会话区/素材面板异步渲染会把目标挪位，首次测量会失效；布局一变就重新贴靠。
let ro = null;
let mo = null;
let raf = 0;
let timers = [];

function schedulePlace() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; place(); });
}

// 切步骤/重新挂载时会话区还没渲染出来，先居中兜底，等它出来再锚定
function placeSoon() {
  place();
  timers.forEach(clearTimeout);
  timers = [setTimeout(place, 180), setTimeout(place, 700)];
}

watch(idx, placeSoon);
onMounted(() => {
  if (step.value.nav && location.hash !== step.value.nav) emit("goto", step.value.nav);
  placeSoon();
  window.addEventListener("resize", place);
  window.addEventListener("scroll", schedulePlace, true);
  ro = new ResizeObserver(schedulePlace);
  ro.observe(document.body);
  // 目标上方的消息/快捷入口是插入出来的，body 高度不变，只靠尺寸观测追不到
  mo = new MutationObserver(schedulePlace);
  mo.observe(document.body, { childList: true, subtree: true });
});
onUnmounted(() => {
  window.removeEventListener("resize", place);
  window.removeEventListener("scroll", schedulePlace, true);
  ro?.disconnect();
  mo?.disconnect();
  timers.forEach(clearTimeout);
  if (raf) cancelAnimationFrame(raf);
});
</script>

<template>
  <div class="gt-layer" role="dialog" aria-modal="true" aria-label="新手引导">
    <div class="gt-dim" />
    <div ref="bubbleEl" class="gt-bubble" :style="bubbleStyle">
      <p class="gt-mascot">🦉</p>
      <h3>{{ step.title }}</h3>
      <p class="gt-text">{{ step.text }}</p>
      <div class="gt-foot">
        <span class="gt-dots">
          <i v-for="(s, i) in STEPS" :key="i" :class="{ on: i === idx }" />
        </span>
        <span class="gt-btns">
          <button class="gt-skip" :disabled="busy" @click="skip">跳过</button>
          <button class="gt-next" :disabled="busy" @click="next">
            {{ idx === STEPS.length - 1 ? "开始访谈" : "下一步" }}
          </button>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gt-layer { position: fixed; inset: 0; z-index: 90; pointer-events: none; }
.gt-dim { position: absolute; inset: 0; background: rgba(15, 23, 42, .3); }
.gt-bubble {
  position: absolute; width: 300px; pointer-events: auto;
  background: var(--bg-surface); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg); padding: 18px;
  animation: gt-in .18s ease-out; transition: top .25s, left .25s, transform .25s;
}
@keyframes gt-in { from { opacity: 0; transform: translateY(6px); } }
.gt-mascot { font-size: 1.6rem; margin: 0 0 4px; }
.gt-bubble h3 { margin: 0 0 6px; font-size: .95rem; }
.gt-text { font-size: .8rem; color: var(--ink-secondary); margin: 0 0 12px; line-height: 1.5; }
.gt-foot { display: flex; justify-content: space-between; align-items: center; }
.gt-dots { display: inline-flex; gap: 5px; }
.gt-dots i {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--border); transition: background .15s;
}
.gt-dots i.on { background: var(--brand); }
.gt-btns { display: inline-flex; gap: 8px; }
.gt-skip {
  border: none; background: none; cursor: pointer; font: inherit;
  font-size: .78rem; color: var(--ink-muted); padding: 6px 8px;
}
.gt-skip:hover { color: var(--ink); }
.gt-next {
  border: none; border-radius: 8px; padding: 8px 16px; cursor: pointer;
  background: var(--brand); color: #fff; font: inherit; font-weight: 600; font-size: .82rem;
}
</style>
