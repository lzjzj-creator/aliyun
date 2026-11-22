<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import AdminView from "./views/AdminView.vue";
import ChatView from "./views/ChatView.vue";
import ContractDemoView from "./views/ContractDemoView.vue";
import InterviewView from "./views/InterviewView.vue";
import MemoryView from "./views/MemoryView.vue";
import ResumeLibraryView from "./views/ResumeLibraryView.vue";
import ResumeView from "./views/ResumeView.vue";
import ResumeWizardView from "./views/ResumeWizardView.vue";
import SessionList from "./components/SessionList.vue";
import UiIcon from "./components/UiIcon.vue";
import { setCurrentSessionId } from "./storage";

// hash 路由：#chat | #chat/:id | #chat-v1 | #resume | #resume-v1 | #demo | #admin（不引 vue-router）
const view = ref("chat");
const sidebar = ref(false);          // 移动端会话抽屉
const chatSid = ref("");   // 作为 InterviewView 的 key：切会话即重挂载走 boot()
const resumeDocId = ref(""); // #/resume/library/:id 的文档 id
const sessionListRef = ref(null);
const collapsed = ref(false);        // 侧栏折叠态（localStorage 记忆）

try {
  collapsed.value = localStorage.getItem("zm_sidebar_collapsed") === "1";
} catch { /* 存储不可用时保持展开 */ }

function toggleCollapsed() {
  collapsed.value = !collapsed.value;
  try { localStorage.setItem("zm_sidebar_collapsed", collapsed.value ? "1" : "0"); }
  catch { /* 忽略持久化失败 */ }
}

const NAV = [
  { hash: "#/chat", label: "访谈挖掘", short: "访谈", icon: "message",
    on: (v) => v === "chat" || v === "chatV1" },
  { hash: "#/resume", label: "简历向导", short: "向导", icon: "file-text",
    on: (v) => v === "resume" || v === "resumeV1" },
  { hash: "#/resume/library", label: "岗位简历库", short: "简历库", icon: "folder-kanban",
    on: (v) => v === "resumeLibrary" },
  { hash: "#/memory", label: "经历证据库", short: "经历库", icon: "database",
    on: (v) => v === "memory" },
];

const PAGE_META = {
  chat: ["访谈挖掘", "先聊出真实经历，再长成简历"],
  chatV1: ["问答（旧版）", "先聊出你的经历，再长出一份简历"],
  resume: ["简历向导", "从经历证据到岗位定制简历"],
  resumeV1: ["简历诊断（旧版）", "先聊出你的经历，再长出一份简历"],
  resumeLibrary: ["岗位简历库", "多版本定制 · 边改边学"],
  memory: ["个人经历库", "事实可溯源 · 你可完全掌控"],
  contractDemo: ["契约演示", ""],
};
const pageTitle = computed(() => (PAGE_META[view.value] || ["知脉 · 简历成长助手", ""])[0]);
const pageSub = computed(() => (PAGE_META[view.value] || ["", ""])[1]);

function syncRoute() {
  const h = location.hash || "#/chat";
  if (/^#\/?admin\/?$/.test(h)) { view.value = "admin"; return; }
  if (/^#\/?demo/.test(h)) { view.value = "contractDemo"; return; }
  if (/^#\/?memory\/?$/.test(h)) { view.value = "memory"; return; }
  if (/^#\/?resume-v1/.test(h)) { view.value = "resumeV1"; return; }
  const lib = h.match(/^#\/?resume\/library\/?([0-9a-zA-Z-]+)?/);
  if (lib) { view.value = "resumeLibrary"; resumeDocId.value = lib[1] || ""; return; }
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

  <div v-else class="workbench" :class="{ collapsed }">
    <nav class="app-sidebar" aria-label="主导航">
      <div class="sb-head">
        <div class="sb-logo" title="知脉">知</div>
        <span v-if="!collapsed" class="sb-brand">知脉</span>
        <button class="sb-collapse" :aria-label="collapsed ? '展开导航' : '折叠导航'"
                :title="collapsed ? '展开导航' : '折叠导航'" @click="toggleCollapsed">
          <UiIcon name="panel-left" :size="16" />
        </button>
      </div>

      <button v-for="item in NAV" :key="item.hash" class="sb-item"
              :class="{ on: item.on(view) }" :title="collapsed ? item.label : undefined"
              :aria-current="item.on(view) ? 'page' : undefined" @click="nav(item.hash)">
        <UiIcon :name="item.icon" :size="18" />
        <span v-if="!collapsed">{{ item.label }}</span>
      </button>

      <div v-if="!collapsed" class="sb-foot">
        <span class="sb-foot-title">成长闭环</span>
        <span class="sb-foot-line">聊经历 → 存证据 →<br />定制简历 → 学优化</span>
      </div>
    </nav>

    <div class="stage">
      <header class="topbar">
        <div class="tb-left">
          <button v-if="view === 'chat'" class="tb-sessions" @click="sidebar = true">☰ 会话</button>
          <span class="tb-title">{{ pageTitle }}</span>
          <span v-if="pageSub" class="tb-sub">{{ pageSub }}</span>
        </div>
        <details class="tb-privacy-pop">
          <summary><UiIcon name="shield" :size="13" /> 隐私保护中</summary>
          <div class="tb-privacy-card">
            对话 48 小时留存可删 · 长期记忆默认关闭 · 支持导出 JSON 与一键清空
          </div>
        </details>
      </header>

      <main class="main-area" :class="{ 'main-flush': view === 'chat' }">
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
        <ResumeWizardView v-else-if="view === 'resume'"
                          @goto-library="nav('#/resume/library')" />
        <ResumeLibraryView v-else-if="view === 'resumeLibrary'"
                           :doc-id="resumeDocId" :key="resumeDocId"
                           @goto-memory="nav('#/memory')" @nav="nav" />
        <MemoryView v-else-if="view === 'memory'" />
      </main>
    </div>

    <nav class="mobile-tabs" aria-label="主导航">
      <button v-for="item in NAV" :key="item.hash" class="mt-item"
              :class="{ on: item.on(view) }" :aria-current="item.on(view) ? 'page' : undefined"
              @click="nav(item.hash)">
        <UiIcon :name="item.icon" :size="20" />
        <span>{{ item.short }}</span>
      </button>
    </nav>

    <SessionList v-if="sidebar" :active-id="chatSid" @close="sidebar = false"
                 @select="onSelectSession" @new="onNewSession" />
  </div>
</template>
