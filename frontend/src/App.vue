<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import AdminView from "./views/AdminView.vue";
import ChatView from "./views/ChatView.vue";
import ContractDemoView from "./views/ContractDemoView.vue";
import InterviewView from "./views/InterviewView.vue";
import ResumeView from "./views/ResumeView.vue";
import ResumeWizardView from "./views/ResumeWizardView.vue";
import SessionList from "./components/SessionList.vue";
import { setCurrentSessionId } from "./storage";

// hash 路由：#chat | #chat/:id | #chat-v1 | #resume | #resume-v1 | #demo | #admin（不引 vue-router）
const view = ref("chat");
const sidebar = ref(false);

function syncRoute() {
  const h = location.hash || "#/chat";
  if (/^#\/?admin\/?$/.test(h)) { view.value = "admin"; return; }
  if (/^#\/?demo/.test(h)) { view.value = "contractDemo"; return; }
  if (/^#\/?resume-v1/.test(h)) { view.value = "resumeV1"; return; }
  if (/^#\/?resume/.test(h)) { view.value = "resume"; return; }
  if (/^#\/?chat-v1/.test(h)) { view.value = "chatV1"; return; }
  const m = h.match(/^#\/?chat\/?([0-9a-zA-Z-]+)?/);
  view.value = "chat";
  setCurrentSessionId(m?.[1] || "");
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

  <div v-else class="shell">
    <header class="top">
      <div class="brand">
        <h1>知脉 · 简历成长助手</h1>
        <p class="tagline">先聊出你的经历，再长出一份简历——所有内容都要有出处。</p>
      </div>
      <nav class="tabs">
        <button class="hamburger" title="会话" @click="sidebar = true">☰</button>
        <button :class="{ on: view === 'chat' }" @click="nav('#/chat')">访谈</button>
        <button :class="{ on: view === 'resume' }" @click="nav('#/resume')">建简历</button>
      </nav>
    </header>

    <section class="intro">
      <p class="slogan">证据优先的大学规划与简历助手</p>
      <div class="intro-entries">
        <span><b>访谈</b>：AI 主动追问你的经历与目标，顺带可问知识库里的规划/求职问题，全程可见思考过程。</span>
        <span><b>建简历</b>：一键把访谈素材填进优秀模板，人工修改后可预览导出，还能逐段安全优化。</span>
      </div>
    </section>

    <p class="privacy">
      对话将保存用于历史记录，可随时在会话列表删除；简历成稿只存本机浏览器，不上传服务器。
      建议不要在对话中发送姓名、电话等敏感信息。
    </p>

    <SessionList v-if="sidebar" @close="sidebar = false" @select="onSelectSession" @new="onNewSession" />

    <main>
      <InterviewView v-if="view === 'chat'" @goto-resume="nav('#/resume')" />
      <ChatView v-else-if="view === 'chatV1'" @goto-resume="nav('#/resume')" />
      <ContractDemoView v-else-if="view === 'contractDemo'" />
      <ResumeWizardView v-else-if="view === 'resume'" />
      <ResumeView v-else-if="view === 'resumeV1'" />
    </main>
  </div>
</template>
