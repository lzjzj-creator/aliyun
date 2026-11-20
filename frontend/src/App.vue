<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import AdminView from "./views/AdminView.vue";
import ChatView from "./views/ChatView.vue";
import ContractDemoView from "./views/ContractDemoView.vue";
import InterviewView from "./views/InterviewView.vue";
import MemoryView from "./views/MemoryView.vue";
import ResumeView from "./views/ResumeView.vue";
import ResumeWizardView from "./views/ResumeWizardView.vue";
import SessionList from "./components/SessionList.vue";
import { setCurrentSessionId } from "./storage";

// hash 路由：#chat | #chat/:id | #chat-v1 | #resume | #resume-v1 | #demo | #admin（不引 vue-router）
const view = ref("chat");
const sidebar = ref(false);          // 移动端会话抽屉
const chatSid = ref("");   // 作为 InterviewView 的 key：切会话即重挂载走 boot()
const sessionListRef = ref(null);

function syncRoute() {
  const h = location.hash || "#/chat";
  if (/^#\/?admin\/?$/.test(h)) { view.value = "admin"; return; }
  if (/^#\/?demo/.test(h)) { view.value = "contractDemo"; return; }
  if (/^#\/?memory\/?$/.test(h)) { view.value = "memory"; return; }
  if (/^#\/?resume-v1/.test(h)) { view.value = "resumeV1"; return; }
  if (/^#\/?resume/.test(h)) { view.value = "resume"; return; }
  if (/^#\/?chat-v1/.test(h)) { view.value = "chatV1"; return; }
  const m = h.match(/^#\/?chat\/?([0-9a-zA-Z-]+)?/);
  view.value = "chat";
  chatSid.value = m?.[1] || "";
  setCurrentSessionId(chatSid.value);
}

function nav(target) {
  location.hash = target;
}

function onSelectSession(id) {
  sidebar.value = false;
  nav(id ? `#/chat/${id}` : "#/chat");
}

function onNewSession() {
  sidebar.value = false;
  setCurrentSessionId("");
  nav("#/chat");
}

function onSessionCreated() {
  sessionListRef.value?.reload?.();
}

onMounted(() => {
  if (!location.hash) location.hash = "#/chat";
  syncRoute();
  window.addEventListener("hashchange", syncRoute);
});
onUnmounted(() => window.removeEventListener("hashchange", syncRoute));
</script>

<template>
  <div v-if="view === 'admin'" class="shell admin-shell">
    <AdminView />
  </div>

  <div v-else class="workbench">
    <nav class="rail" aria-label="主导航">
      <div class="rail-logo" title="知脉">知</div>
      <button class="rail-item" :class="{ on: view === 'chat' || view === 'chatV1' }"
              @click="nav('#/chat')">
        <span class="rail-ico">◈</span><span>访谈</span>
      </button>
      <button class="rail-item" :class="{ on: view === 'resume' || view === 'resumeV1' }"
              @click="nav('#/resume')">
        <span class="rail-ico">▤</span><span>简历</span>
      </button>
      <button class="rail-item" :class="{ on: view === 'memory' }"
              @click="nav('#/memory')">
        <span class="rail-ico">◉</span><span>记忆</span>
      </button>
    </nav>

    <div class="stage">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">知脉 · 简历成长助手</span>
          <span class="tb-sub">先聊出你的经历，再长出一份简历</span>
        </div>
        <button v-if="view === 'chat'" class="tb-sessions" @click="sidebar = true">☰ 会话</button>
        <span class="tb-privacy">对话留存可删 · 简历只在本机</span>
      </header>

      <main class="main-area">
        <!-- 访谈：会话栏 | 消息流 | 画像栏 三柱工作台 -->
        <div v-if="view === 'chat'" class="iv-workbench">
          <SessionList ref="sessionListRef" class="iv-side-col" inline :active-id="chatSid"
                       @select="onSelectSession" @new="onNewSession" />
          <InterviewView class="iv-main-col" :key="chatSid"
                         @goto-resume="nav('#/resume')"
                         @session-created="onSessionCreated" />
        </div>
        <div v-else-if="view === 'chatV1' || view === 'resumeV1' || view === 'contractDemo'" class="v1-wrap">
          <ChatView v-if="view === 'chatV1'" @goto-resume="nav('#/resume')" />
          <ContractDemoView v-else-if="view === 'contractDemo'" />
          <ResumeView v-else />
        </div>
        <ResumeWizardView v-else-if="view === 'resume'" />
        <MemoryView v-else-if="view === 'memory'" />
      </main>
    </div>

    <SessionList v-if="sidebar" :active-id="chatSid" @close="sidebar = false"
                 @select="onSelectSession" @new="onNewSession" />
  </div>
</template>
