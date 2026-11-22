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

const emit = defineEmits(["goto-resume", "session-created"]);

const md = new MarkdownIt({ linkify: true, html: false, breaks: true });
const renderMd = (t) => md.render(t || "");
const KB_BASE = (import.meta.env.VITE_KB_SOURCE_BASE || "").replace(/\/$/, "");
const sourceHref = (url) => toMirrorHref(url, KB_BASE);

const items = ref([]);        // {role, text, steps?, reasoning?, sources?, cards?, error?, pending?}
const profile = reactive({ coverage: null, gaps: [], updates: [] });
const slotLabels = reactive({});   // 模板契约 slot→label（ProfilePanel 展示用）
const stage = ref("greeting");
const input = ref("");
const inputEl = ref(null);
const chips = ref([]);         // 建议回复：归位到输入框上方，不留在消息流里
const busy = ref(false);
const slow = ref(false);
const dead = ref("");         // 致命错误提示（引擎未开放/会话丢失）
const historyExpired = ref(false); // 48h 保留期：本会话旧消息已到期清除（非致命）
const deadFallbackV1 = ref(false);  // dead 时是否提供 v1 问答回退入口
const box = ref(null);
const sessionId = ref(getCurrentSessionId());
const MSG_ENGINE_OFF = "访谈引擎暂未开放（服务端 COACH_ENABLED 未开启）。可先用问答模式。";
const deadCanRetry = ref(false);  // dead 为瞬时故障（非 404/503）时可重试
let itemKey = 0;                  // items 稳定 key（index key 在 splice 后错位）
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
      restored.push({ role: "user", text: m.content, _k: ++itemKey });
    } else if (m.role === "assistant") {
      restored.push({
        role: "assistant", text: m.content || "", _k: ++itemKey,
        steps: m.steps || [], sources: m.sources || [],
        cards: (m.meta && m.meta.cards) || [],
        reasoning: (m.meta && m.meta.reasoning) || "",
        failed: m.status === "error",
      });
      // 服务端幂等墙不支持"重生成同一轮"：失败轮给明示，不造假重试按钮
      if (m.status === "error") {
        restored[restored.length - 1].error = "该轮回复生成失败，可重新发送同样的问题。";
      }
    }
  }
  const prof = bundle.profile || {};
  profile.coverage = typeof prof.coverage === "number" ? prof.coverage : null;
  profile.gaps = [];            // 重置旧缺口，等下一轮 profile 事件重填
  const filled = (prof.coverage_detail && prof.coverage_detail.filled) || {};
  profile.updates = Object.entries(filled).map(([slot, v]) => ({
    slot, value: typeof v === "object" && v !== null ? v.value : v,
  }));
  stage.value = (bundle.session && bundle.session.stage) || "probing";
  historyExpired.value = !!bundle.history_expired;
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
          emit("session-created", d.session_id);
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
      card(d) {
        if (d.type === "suggest_reply") { chips.value = d.payload?.replies || []; scroll(); return; }
        turn.cards.push(d); scroll();
      },
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
    _k: ++itemKey,
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
      dead.value = MSG_ENGINE_OFF;
      deadFallbackV1.value = true;
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
  if (!query || busy.value || dead.value || demoLoading.value) return;
  input.value = "";
  chips.value = [];
  items.value.push({ role: "user", text: query, _k: ++itemKey });
  stream({ sessionId: sessionId.value || null, message: query,
           clientMsgId: newClientMsgId(), userText: query });
  scroll();
}

function retry(turn) {
  if (busy.value) return;
  const idx = items.value.indexOf(turn);
  if (turn.greetingRetry) {          // 招呼轮无用户消息：重试=重走 boot 建会话
    if (idx >= 0) items.value.splice(idx, 1);
    boot();
    return;
  }
  if (!turn.retryText) return;
  if (idx >= 0) items.value.splice(idx, 1);
  stream({ sessionId: sessionId.value || null, message: turn.retryText,
           clientMsgId: turn.clientMsgId || newClientMsgId(), userText: turn.retryText });
}

function onCard(card) {
  if (card.type === "goto_resume") emit("goto-resume");
}

// 画像面板「待补充」点击 → 回填引导句并聚焦输入框
function onAskGap(label) {
  input.value = `关于${label}：`;
  inputEl.value?.focus();
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
      items.value.push({ role: "user", text: round.user, _k: ++itemKey });
      const turn = reactive({
        role: "assistant", text: "", steps: [], reasoning: "",
        sources: [], cards: [], done: false, error: "", retryable: false,
        _k: ++itemKey,
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
                            sources: [], cards: [], done: false, error: "",
                            retryable: false, _k: ++itemKey, greetingRetry: true });
    items.value.push(turn);
    const wired = wireTurn(turn, "");
    try {
      await chatStreamV2({ sessionId: null, message: "", handlers: wired.handlers });
    } catch (e) {
      items.value.pop();
      deadFallbackV1.value = e.status === 503;
      deadCanRetry.value = !deadFallbackV1.value;
      dead.value = deadFallbackV1.value
        ? ((e.data && e.data.detail) || MSG_ENGINE_OFF)
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
    dead.value = "";
    deadCanRetry.value = false;
    deadFallbackV1.value = false;
    scroll();
  } catch (e) {
    deadCanRetry.value = e.status !== 404;   // 瞬时故障给重试入口
    dead.value = e.status === 404
      ? "会话不存在或已过期，点上方「会话」新建一个。"
      : `会话加载失败：${e.message}`;
  }
}
onMounted(boot);
</script>

<template>
  <section class="chat interview iv-grid">
    <div class="iv-main">
    <div v-if="dead" class="dead-banner">
      {{ dead }}
      <a v-if="deadFallbackV1" href="#/chat-v1">去问答模式 →</a>
      <button v-if="deadCanRetry && !busy" class="demo-link" @click="boot">重试</button>
      <button v-if="!busy" class="demo-link" :disabled="demoLoading" @click="loadDemo">
        ▶ 演示访谈（离线回放）
      </button>
    </div>

    <div v-if="historyExpired" class="expired-banner">
      本对话已超过 48 小时，历史消息已按隐私政策清除；可继续对话，或点上方「会话」新建。
    </div>

    <div v-if="!items.length && !busy" class="empty">
      <h2>先来聊聊你的经历</h2>
      <p class="empty-hint">我会主动追问，把能写进简历的素材一点点挖出来。</p>
      <button class="demo-chat" :disabled="demoLoading" @click="loadDemo">
        ▶ 演示访谈（离线快照，无需联网）
      </button>
    </div>

    <div ref="box" class="stream">
      <div v-for="(m, i) in items" :key="m._k ?? i" :class="['msg', m.role]">
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

    <div v-if="chips.length" class="suggest-replies" :class="{ hero: stage === 'greeting' }">
      <button v-for="r in chips" :key="r" :disabled="busy" @click="send(r)">{{ r }}</button>
    </div>
    <form class="composer" @submit.prevent="send()">
      <input ref="inputEl" v-model="input" :disabled="busy || !!dead"
             :placeholder="busy ? (slow ? '首次唤醒服务，约 3–8 秒…' : '思考中…') : '聊聊你的经历，或直接提问…'" />
      <button type="submit" :disabled="busy || !input.trim() || !!dead">发送</button>
    </form>
    </div>

    <ProfilePanel class="iv-side" :coverage="profile.coverage" :gaps="profile.gaps"
                  :updates="profile.updates" :labels="slotLabels" @ask="onAskGap" />
  </section>
</template>

<style scoped>
.dead-banner {
  margin: 0 0 10px; padding: 10px 14px; border-radius: 10px;
  background: var(--accent-bg); border: 1px solid var(--accent-line); color: var(--err); font-size: .85rem;
}
.dead-banner a { color: var(--accent); margin-left: 6px; }
.expired-banner {
  margin: 0 0 10px; padding: 10px 14px; border-radius: 10px;
  background: var(--accent-bg); border: 1px solid var(--accent-line);
  color: var(--accent); font-size: .85rem;
}
.turn-error { margin-top: 8px; color: var(--err); font-size: .82rem; }
.turn-error .retry {
  margin-left: 8px; padding: 2px 12px; font-size: .78rem; border-radius: 999px;
  border: 1px solid var(--accent); color: var(--accent); background: #fff; cursor: pointer;
}
.coach-cards { margin-top: 10px; }
.coach-card.go {
  width: 100%; padding: 10px; border-radius: 10px; border: 1px solid var(--accent);
  background: var(--accent-bg); color: var(--accent); font-size: .9rem; cursor: pointer;
}
.coach-card.go:hover { background: var(--accent-hover); }
.suggest-replies { display: flex; flex-wrap: wrap; gap: 8px; }
.suggest-replies button {
  padding: 6px 14px; border-radius: 999px; font-size: .82rem; cursor: pointer;
  border: 1px dashed var(--teal); background: #fff; color: var(--teal);
}
.suggest-replies button:hover:not(:disabled) { background: var(--hover-teal); }
.suggest-replies button:disabled { opacity: .5; cursor: default; }
/* greeting 阶段：入口级双卡（开始访谈 / 直接提问），不是普通快捷回复 */
.suggest-replies.hero { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.suggest-replies.hero button {
  padding: 16px; font-size: .92rem; text-align: left; line-height: 1.5;
  border-radius: 12px; border-style: solid; border-color: var(--teal);
}
@media (max-width: 640px) {
  .suggest-replies.hero { grid-template-columns: 1fr; }
}
.stage-done { margin: 6px 0 0; font-size: .78rem; color: var(--mute); text-align: center; }
.demo-link {
  margin-left: 8px; padding: 2px 12px; font-size: .78rem; border-radius: 999px;
  border: 1px solid var(--accent); color: var(--accent); background: #fff; cursor: pointer;
}
.empty { padding: 24px 0 8px; }
.empty-hint { color: var(--ink-soft); font-size: .85rem; margin: 4px 0 12px; }
</style>
