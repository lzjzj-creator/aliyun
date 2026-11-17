<script setup>
// #16 访谈主界面：v2 coach SSE 整合（会话恢复 + 步骤回放 + 画像面板 + 引导卡 + 断线提示）
import { ref, reactive, nextTick, onMounted, onUnmounted } from "vue";
import MarkdownIt from "markdown-it";
import { chatStreamV2 } from "../api/chat";
import { getSession } from "../api/sessions";
import { getTemplate } from "../api/resume";
import { replayEvents } from "../api/core";
import ThinkingSteps from "../components/ThinkingSteps.vue";
import ProfilePanel from "../components/ProfilePanel.vue";
import {
  getCurrentSessionId, setCurrentSessionId, setSessionToken,
} from "../storage";
import { toMirrorHref } from "../kbSource.mjs";

const emit = defineEmits(["goto-resume"]);

const md = new MarkdownIt({ linkify: true, html: false, breaks: true });
const renderMd = (t) => md.render(t || "");
const KB_BASE = (import.meta.env.VITE_KB_SOURCE_BASE || "").replace(/\/$/, "");
const sourceHref = (url) => toMirrorHref(url, KB_BASE);

const items = ref([]);        // {role, text, steps?, reasoning?, sources?, cards?, error?, pending?}
const profile = reactive({ coverage: null, gaps: [], updates: [] });
const slotLabels = reactive({});   // 模板契约 slot→label（ProfilePanel 展示用）
const stage = ref("greeting");
const input = ref("");
const busy = ref(false);
const slow = ref(false);
const dead = ref("");         // 致命错误提示（引擎未开放/会话丢失）
const box = ref(null);
const sessionId = ref(getCurrentSessionId());
let slowTimer = null;

function newClientMsgId() {
  return crypto.randomUUID?.() || `cm-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
async function scroll() {
  await nextTick();
  box.value?.scrollTo({ top: box.value.scrollHeight, behavior: "smooth" });
}
function startSlow() {
  clearTimeout(slowTimer);
  slow.value = false;
  slowTimer = setTimeout(() => { slow.value = true; }, 1500);
}
function stopSlow() { clearTimeout(slowTimer); slow.value = false; }
onUnmounted(stopSlow);

// ---------- 会话恢复：历史消息 + 步骤回放 + 画像 ----------
function restoreMessages(bundle) {
  const restored = [];
  for (const m of bundle.messages || []) {
    if (m.role === "user") {
      restored.push({ role: "user", text: m.content });
    } else if (m.role === "assistant") {
      restored.push({
        role: "assistant", text: m.content || "",
        steps: m.steps || [], sources: m.sources || [],
        cards: (m.meta && m.meta.cards) || [],
        reasoning: (m.meta && m.meta.reasoning) || "",
        failed: m.status === "error",
      });
    }
  }
  const prof = bundle.profile || {};
  profile.coverage = typeof prof.coverage === "number" ? prof.coverage : null;
  const filled = (prof.coverage_detail && prof.coverage_detail.filled) || {};
  profile.updates = Object.entries(filled).map(([slot, v]) => ({
    slot, value: typeof v === "object" && v !== null ? v.value : v,
  }));
  stage.value = (bundle.session && bundle.session.stage) || "probing";
  items.value = restored;
}

// ---------- 主流程 ----------
function wireTurn(turn, lastUserText, demo = false) {
  let finished = false;
  return {
    handlers: {
      session(d) {
        if (demo) {
          if (d.stage) stage.value = d.stage;
          return;   // 演示回放不写会话令牌/当前会话
        }
        if (d.session_token) setSessionToken(d.session_id, d.session_token);
        if (d.session_id && d.session_id !== sessionId.value) {
          sessionId.value = d.session_id;
          setCurrentSessionId(d.session_id);
          history.replaceState(null, "", `#/chat/${d.session_id}`);
        }
        if (d.stage) stage.value = d.stage;
      },
      step_start(d) {
        turn.steps.push({ ...d, status: "running" });
        scroll();
      },
      step_end(d) {
        const s = turn.steps.find((x) => x.id === d.id);
        if (s) Object.assign(s, d);
        scroll();
      },
      reasoning_summary(d) { turn.reasoning = d; scroll(); },
      token(d) { turn.text += d; scroll(); },
      sources(d) { turn.sources = d; scroll(); },
      card(d) { turn.cards.push(d); scroll(); },
      profile(d) {
        profile.coverage = d.coverage ?? profile.coverage;
        profile.gaps = d.gaps || profile.gaps;
        profile.updates.push(...(d.updates || []));
      },
      done(d) {
        finished = true;
        turn.done = true;
        if (d.stage) stage.value = d.stage;
        if (d.coverage != null) profile.coverage = d.coverage;
        scroll();
      },
      error(d) {
        finished = true;
        turn.done = true;
        turn.dupError = d.code === "DUPLICATE";   // 服务端已完成本轮 → 对账恢复
        turn.error = d.message || "出错了";
        turn.retryable = d.retryable !== false && !turn.dupError;
        turn.retryText = lastUserText;
        scroll();
      },
    },
    finish() {
      if (!finished && !turn.error) {
        turn.error = "连接中断，请重试";
        turn.retryable = true;
        turn.retryText = lastUserText;
        turn.done = true;
      }
    },
  };
}

async function stream({ sessionId: sid, message, clientMsgId, userText }) {
  const turn = reactive({
    role: "assistant", text: "", steps: [], reasoning: "",
    sources: [], cards: [], done: false, error: "", retryable: false, retryText: "",
    clientMsgId,    // 重试沿用同一幂等键，服务端可识别已完成的轮次
  });
  items.value.push(turn);
  busy.value = true;
  startSlow();
  const wired = wireTurn(turn, userText);
  try {
    await chatStreamV2({ sessionId: sid, clientMsgId, message, handlers: wired.handlers });
  } catch (e) {
    if (e.status === 503) {
      dead.value = "访谈引擎暂未开放（服务端 COACH_ENABLED 未开启）。可先用问答模式。";
      items.value.pop();
      return;
    }
    turn.error = `网络错误：${e.message}`;
    turn.retryable = true;
    turn.retryText = userText;
    turn.done = true;
  } finally {
    wired.finish();
    if (turn.dupError && sessionId.value) {
      // 原请求其实已完成 → 拉取服务端权威状态替换本地视图
      try { restoreMessages(await getSession(sessionId.value)); } catch { /* 保持现状 */ }
    }
    stopSlow();
    busy.value = false;
    scroll();
  }
}

function send(text) {
  const query = (text ?? input.value).trim();
  if (!query || busy.value || dead.value) return;
  input.value = "";
  items.value.push({ role: "user", text: query });
  stream({ sessionId: sessionId.value || null, message: query,
           clientMsgId: newClientMsgId(), userText: query });
  scroll();
}

function retry(turn) {
  if (busy.value || !turn.retryText) return;
  const idx = items.value.indexOf(turn);
  if (idx >= 0) items.value.splice(idx, 1);
  stream({ sessionId: sessionId.value || null, message: turn.retryText,
           clientMsgId: turn.clientMsgId || newClientMsgId(), userText: turn.retryText });
}

function onCard(card) {
  if (card.type === "goto_resume") emit("goto-resume");
}

// ---------- #24 离线演示回放：demo/v2 stream fixture 逐条回放，不走真实接口 ----------
const DEMO_ROUNDS = [
  { file: "stream-interview.json", user: "我大二，在浙大读计算机，想找后端实习" },
  { file: "stream-kbqa.json", user: "实习一般什么时候开始准备？" },
  { file: "stream-wrapup.json", user: "差不多了，帮我整理成简历吧" },
];
const demoLoading = ref(false);

async function loadDemo() {
  if (busy.value || demoLoading.value) return;
  demoLoading.value = true;
  dead.value = "";
  try {
    for (const round of DEMO_ROUNDS) {
      const events = await fetch(`/demo/v2/${round.file}`).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      items.value.push({ role: "user", text: round.user });
      const turn = reactive({
        role: "assistant", text: "", steps: [], reasoning: "",
        sources: [], cards: [], done: false, error: "", retryable: false,
      });
      items.value.push(turn);
      const wired = wireTurn(turn, round.user, true);
      await replayEvents(events, wired.handlers, { delay: 160 });
      wired.finish();
      scroll();
    }
  } catch (e) {
    items.value.push({ role: "assistant", text: `演示快照加载失败：${e.message}`,
                       steps: [], sources: [], cards: [], done: true });
  } finally {
    demoLoading.value = false;
    scroll();
  }
}

async function boot() {
  // 模板契约拉取：slot→label 展示映射，失败不阻塞（面板退化为原始 id）
  getTemplate().then((t) => {
    for (const s of t.interview_slots || []) slotLabels[s.id] = s.label;
  }).catch(() => {});
  const sid = sessionId.value;
  if (!sid) {
    // 新会话：打招呼流建会话，session 事件带回 token
    busy.value = true;
    startSlow();
    const turn = reactive({ role: "assistant", text: "", steps: [], reasoning: "",
                            sources: [], cards: [], done: false, error: "", retryable: false });
    items.value.push(turn);
    const wired = wireTurn(turn, "");
    try {
      await chatStreamV2({ sessionId: null, message: "", handlers: wired.handlers });
    } catch (e) {
      items.value.pop();
      dead.value = e.status === 503
        ? "访谈引擎暂未开放（服务端 COACH_ENABLED 未开启）。可先用问答模式。"
        : `无法连接访谈服务：${e.message}`;
    } finally {
      wired.finish();
      stopSlow();
      busy.value = false;
      scroll();
    }
    return;
  }
  try {
    const bundle = await getSession(sid);
    restoreMessages(bundle);
    scroll();
  } catch (e) {
    dead.value = e.status === 404
      ? "会话不存在或已过期，点上方「会话」新建一个。"
      : `会话加载失败：${e.message}`;
  }
}
onMounted(boot);
</script>

<template>
  <section class="chat interview">
    <ProfilePanel :coverage="profile.coverage" :gaps="profile.gaps"
                  :updates="profile.updates" :labels="slotLabels" />

    <div v-if="dead" class="dead-banner">
      {{ dead }}
      <a v-if="dead.includes('问答模式')" href="#/chat-v1">去问答模式 →</a>
      <button v-if="!busy" class="demo-link" :disabled="demoLoading" @click="loadDemo">
        ▶ 演示访谈（离线回放）
      </button>
    </div>

    <div v-if="!items.length && !busy" class="empty">
      <h2>先来聊聊你的经历</h2>
      <p class="empty-hint">我会主动追问，把能写进简历的素材一点点挖出来。</p>
      <button class="demo-chat" :disabled="demoLoading" @click="loadDemo">
        ▶ 演示访谈（离线快照，无需联网）
      </button>
    </div>

    <div ref="box" class="stream">
      <div v-for="(m, i) in items" :key="i" :class="['msg', m.role]">
        <div class="bubble" :class="{ pending: busy && i === items.length - 1 && !m.text }">
          <ThinkingSteps v-if="m.role === 'assistant'"
                         :steps="m.steps" :reasoning="m.reasoning"
                         :live="busy && i === items.length - 1 && !m.done" />

          <div v-if="m.text" class="md" v-html="renderMd(m.text)"></div>
          <template v-else-if="busy && i === items.length - 1 && m.role === 'assistant'">
            {{ slow ? "首次唤醒服务，约 3–8 秒，请稍候…" : "思考中…" }}
          </template>

          <div v-if="m.error" class="turn-error">
            {{ m.error }}
            <button v-if="m.retryable" class="retry" @click="retry(m)">重试</button>
          </div>

          <div v-if="m.cards?.length" class="coach-cards">
            <template v-for="(c, ci) in m.cards" :key="ci">
              <button v-if="c.type === 'goto_resume'" class="coach-card go"
                      @click="onCard(c)">{{ c.payload?.label || "去一键填写简历" }} →</button>
              <div v-else-if="c.type === 'suggest_reply'" class="suggest-replies">
                <button v-for="r in c.payload?.replies || []" :key="r"
                        :disabled="busy" @click="send(r)">{{ r }}</button>
              </div>
            </template>
          </div>

          <div v-if="m.sources?.length" class="sources">
            <span class="sources-label">来源</span>
            <div class="source-cards">
              <a v-for="(s, si) in m.sources" :key="(s.url || '') + s.seq" class="source-card"
                 :href="sourceHref(s.url)" target="_blank" rel="noopener">
                <span class="seq">{{ si + 1 }}</span>
                <span class="src-title">{{ s.title }}</span>
                <span class="src-domain">分段 {{ s.seq }}</span>
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>

    <p v-if="stage === 'done'" class="stage-done">访谈已收尾，素材画像已就绪。</p>

    <form class="composer" @submit.prevent="send()">
      <input v-model="input" :disabled="busy || !!dead"
             :placeholder="busy ? (slow ? '首次唤醒服务，约 3–8 秒…' : '思考中…') : '聊聊你的经历，或直接提问…'" />
      <button type="submit" :disabled="busy || !input.trim() || !!dead">发送</button>
    </form>
  </section>
</template>

<style scoped>
.dead-banner {
  margin: 0 0 10px; padding: 10px 14px; border-radius: 10px;
  background: #fdf3ee; border: 1px solid #f0cdb9; color: #b3402a; font-size: .85rem;
}
.dead-banner a { color: var(--accent); margin-left: 6px; }
.turn-error { margin-top: 8px; color: #b3402a; font-size: .82rem; }
.turn-error .retry {
  margin-left: 8px; padding: 2px 12px; font-size: .78rem; border-radius: 999px;
  border: 1px solid var(--accent); color: var(--accent); background: #fff; cursor: pointer;
}
.coach-cards { margin-top: 10px; }
.coach-card.go {
  width: 100%; padding: 10px; border-radius: 10px; border: 1px solid var(--accent);
  background: #fdf3ee; color: var(--accent); font-size: .9rem; cursor: pointer;
}
.coach-card.go:hover { background: #f9e7dd; }
.suggest-replies { display: flex; flex-wrap: wrap; gap: 8px; }
.suggest-replies button {
  padding: 6px 14px; border-radius: 999px; font-size: .82rem; cursor: pointer;
  border: 1px dashed var(--teal); background: #fff; color: var(--teal);
}
.suggest-replies button:hover:not(:disabled) { background: #eef4f4; }
.suggest-replies button:disabled { opacity: .5; cursor: default; }
.stage-done { margin: 6px 0 0; font-size: .78rem; color: #9a9186; text-align: center; }
.demo-link {
  margin-left: 8px; padding: 2px 12px; font-size: .78rem; border-radius: 999px;
  border: 1px solid var(--accent); color: var(--accent); background: #fff; cursor: pointer;
}
.empty { padding: 24px 0 8px; }
.empty-hint { color: #7a6f63; font-size: .85rem; margin: 4px 0 12px; }
</style>
