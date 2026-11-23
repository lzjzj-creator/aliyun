<template>
  <!-- S6 骨架屏：异步视图 chunk 下载期间的占位，避免白屏 -->
  <div class="vs-skel" role="status" aria-label="加载中">
    <div class="vs-bar w60" />
    <div class="vs-block" />
    <div class="vs-block h80" />
    <div class="vs-bar w40" />
  </div>
</template>

<style scoped>
.vs-skel { padding: 24px 18px; max-width: 720px; margin: 0 auto; }
.vs-bar, .vs-block {
  border-radius: 8px; margin-bottom: 14px;
  background: linear-gradient(90deg, var(--bg-subtle) 25%, var(--bg-hover) 50%, var(--bg-subtle) 75%);
  background-size: 200% 100%;
  animation: vs-shine 1.2s infinite linear;
}
.vs-bar { height: 14px; }
.vs-bar.w60 { width: 60%; }
.vs-bar.w40 { width: 40%; }
.vs-block { height: 44px; }
.vs-block.h80 { height: 80px; }
@keyframes vs-shine { from { background-position: 200% 0; } to { background-position: -200% 0; } }
</style>
