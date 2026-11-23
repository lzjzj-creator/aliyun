<script setup>
// 注册软墙（多邻国式延迟注册）：guest 用户在价值时刻弹出，可关闭、24h 一次。
// 节流时间戳记 localStorage（zm_authwall_at）——非业务数据，仅存弹窗节奏。
import { ref } from "vue";
import { authwallSnoozed } from "./authwall";

const emit = defineEmits(["register", "dismiss"]);
const visible = ref(true);

function snooze() {
  authwallSnoozed();
  visible.value = false;
  emit("dismiss");
}
</script>

<template>
  <div v-if="visible" class="aw-mask" @click.self="snooze">
    <div class="aw-card" role="dialog" aria-modal="true" aria-label="创建账号">
      <p class="aw-mascot">🦉</p>
      <h3>你的内容已经在云端了</h3>
      <p class="aw-text">
        注册一个账号，把访谈记录、经历库和简历草稿绑定到用户名上——换设备也能接着用。
      </p>
      <p class="aw-note">资料存于服务端账号下，随账号删除而清空。</p>
      <button class="aw-primary" @click="emit('register')">免费注册（30 秒）</button>
      <button class="aw-ghost" @click="snooze">先不用，继续体验</button>
    </div>
  </div>
</template>

<style scoped>
.aw-mask {
  position: fixed; inset: 0; z-index: 80;
  background: rgba(15, 23, 42, .35); backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.aw-card {
  background: var(--bg-surface); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg); padding: 24px 22px; max-width: 340px;
  text-align: center; animation: aw-in .18s ease-out;
}
@keyframes aw-in { from { transform: translateY(8px); opacity: 0; } }
.aw-mascot { font-size: 2rem; margin: 0 0 6px; }
.aw-card h3 { margin: 0 0 8px; font-size: 1rem; }
.aw-text { font-size: .82rem; color: var(--ink-secondary); margin: 0 0 6px; line-height: 1.5; }
.aw-note { font-size: .7rem; color: var(--ink-muted); margin: 0 0 16px; }
.aw-primary {
  width: 100%; padding: 11px; border: none; border-radius: 10px; cursor: pointer;
  background: var(--brand); color: #fff; font: inherit; font-weight: 600; font-size: .88rem;
}
.aw-ghost {
  width: 100%; margin-top: 8px; padding: 9px; border: none; border-radius: 10px;
  background: none; cursor: pointer; font: inherit; font-size: .78rem; color: var(--ink-muted);
}
.aw-ghost:hover { color: var(--ink); }
</style>
