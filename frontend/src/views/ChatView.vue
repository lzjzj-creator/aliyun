<script setup>
import { ref, reactive, nextTick } from "vue";
import { chatStream } from "../api";

const emit = defineEmits(["goto-resume"]);
const SUGGESTED = [
  "大一应该重点补什么信息差？",
  "找第一份实习该怎么规划时间线？",
  "简历上的项目经历怎么写才有说服力？",
  "大三想进互联网大厂要准备什么？",
  "没有实习经历简历怎么写？",
  "大四秋招什么时候开始准备？",
];

const INTENT_CN = {
  kb_qa: "知识问答",
  resume_edit: "改简历",
  resume_qa: "简历咨询",
  chitchat: "闲聊",
  out_of_scope: "超范围",
};
const SCOPE_CN = {
  freshman: "大一",
  sophomore_junior: "大二大三",
  senior: "大四",
  cases: "案例",
  major: "专业",
  video: "视频",
  all: "全域",
};
const LAYER_CN = {
  rule: "规则层",
  "decision-model": "决策模型",
  llm: "兜底",
};

const cn = (map, key) => map[key] || key || "—";

function confidence(r) {
  const c = Number(r?.confidence);
  return Number.isFinite(c) ? c : null;
}
function confPct(r) {
  const c = confidence(r);
  return c === null ? "—" : `${Math.round(c * 100)}%`;
}
function isLowConf(r) {
  const c = confidence(r);
  return c !== null && c < 0.6;
}

const input = ref("");
const messages = ref([]); // {role:'user'|'assistant', text, route, sources, guide}
const busy = ref(false);
const box = ref(null);

function ask(q) {
  const query = (q ?? input.value).trim();
  if (!query || busy.value) return;
  input.value = "";
  messages.value.push({ role: "user", text: query });
  const ai = reactive({ role: "assistant", text: "", route: null, sources: [], guide: null });
  messages.value.push(ai);
  busy.value = true;

  const history = messages.value.slice(-7, -1)
    .map((m) => ({ role: m.role, content: m.text }));

  chatStream(query, history, {
    route: (r) => { ai.route = r; },
    guide: (g) => { ai.guide = g; scroll(); },
    sources: (s) => { ai.sources = s; scroll(); },
    token: (t) => { ai.text += t; scroll(); },
    error: (e) => { ai.text += `\n[出错了：${e.message || e}]`; },
    done: () => { busy.value = false; scroll(); },
  }).catch((e) => {
    ai.text += `\n[网络错误：${e.message}]`;
    busy.value = false;
  });
}

async function scroll() {
  await nextTick();
  box.value?.scrollTo({ top: box.value.scrollHeight, behavior: "smooth" });
}
</script>

<template>
  <section class="chat">
    <div v-if="!messages.length" class="empty">
      <h2>问点什么</h2>
      <div class="suggested">
        <button v-for="s in SUGGESTED" :key="s" @click="ask(s)">{{ s }}</button>
      </div>
    </div>

    <div ref="box" class="stream">
      <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
        <div class="bubble" :class="{ pending: busy && i === messages.length - 1 && !m.text }">
          <div v-if="m.route" class="route-badge" :class="{ low: isLowConf(m.route) }">
            <span class="pill">{{ cn(INTENT_CN, m.route.intent) }}</span>
            <span class="pill">{{ cn(SCOPE_CN, m.route.kb_scope) }}</span>
            <span class="pill">{{ confPct(m.route) }}</span>
            <span class="pill">{{ cn(LAYER_CN, m.route.route_layer) }}</span>
            <span v-if="isLowConf(m.route)" class="pill low-tag">低置信</span>
          </div>

          <template v-if="m.text">{{ m.text }}</template>
          <template v-else-if="busy && i === messages.length - 1">思考中…</template>

          <div v-if="m.guide" class="guide-card">
            {{ m.guide.text }}
            <button class="go" @click="emit('goto-resume')">前往简历助手 →</button>
          </div>

          <div v-if="m.sources?.length" class="sources">
            <span>来源：</span>
            <a v-for="s in m.sources" :key="s.url + s.seq" :href="s.url" target="_blank" rel="noopener">
              {{ s.title }}
            </a>
          </div>
        </div>
      </div>
    </div>

    <form class="composer" @submit.prevent="ask()">
      <input v-model="input" placeholder="问求职、实习、规划、简历写法…" :disabled="busy" />
      <button type="submit" :disabled="busy || !input.trim()">发送</button>
    </form>
  </section>
</template>
