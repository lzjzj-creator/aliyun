<script setup>
import { ref } from "vue";
import ChatView from "./views/ChatView.vue";
import ResumeView from "./views/ResumeView.vue";

const tab = ref("chat");
</script>

<template>
  <div class="shell">
    <header class="top">
      <div class="brand">
        <h1>知脉 · 简历成长助手</h1>
        <p class="tagline">先查资料，再改简历——所有建议都要有出处。</p>
      </div>
      <nav class="tabs">
        <button :class="{ on: tab === 'chat' }" @click="tab = 'chat'">知识问答</button>
        <button :class="{ on: tab === 'resume' }" @click="tab = 'resume'">简历助手</button>
      </nav>
    </header>

    <p class="privacy">
      简历只在浏览器内存读取、提交分析才出网；不存储简历内容，不同会话互相隔离。
      建议先删除姓名、电话等敏感信息。
    </p>

    <main>
      <ChatView v-if="tab === 'chat'" @goto-resume="tab = 'resume'" />
      <ResumeView v-else />
    </main>
  </div>
</template>
