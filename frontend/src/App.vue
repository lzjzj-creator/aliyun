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

    <section class="intro">
      <p class="slogan">证据优先的大学规划与简历助手</p>
      <div class="intro-entries">
        <span><b>知识问答</b>：检索知脉知识库 2790 篇规划/求职笔记，回答附真实来源，没有依据就明说不知道。</span>
        <span><b>简历助手</b>：证据诊断 → 不虚构事实的安全改写，粘贴 JD 可看岗位匹配与缺失关键词。</span>
      </div>
    </section>

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
