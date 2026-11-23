<script setup>
// #/auth 登录/注册页：用户名+密码（无邮箱/短信）。注册=升级当前 guest。
import { onMounted, ref } from "vue";
import { login, register } from "../api/auth";
import UiIcon from "../components/UiIcon.vue";

const emit = defineEmits(["authed", "back"]);

const mode = ref("login");          // login | register
const username = ref("");
const password = ref("");
const confirm = ref("");
const busy = ref(false);
const err = ref("");

function switchMode(m) {
  mode.value = m;
  err.value = "";
}

async function submit() {
  const name = username.value.trim();
  if (!name || !password.value) { err.value = "请填写用户名和密码"; return; }
  if (mode.value === "register") {
    if (password.value.length < 8) { err.value = "密码至少 8 位"; return; }
    if (password.value !== confirm.value) { err.value = "两次输入的密码不一致"; return; }
  }
  busy.value = true;
  err.value = "";
  try {
    const data = mode.value === "register"
      ? await register(name, password.value)
      : await login(name, password.value);
    emit("authed", data.user || {});
  } catch (e) {
    err.value = e.status === 409 ? "该用户名已被注册"
      : e.status === 401 ? "用户名或密码不正确"
      : e.status === 429 ? "尝试太频繁，请稍后再试"
      : e.issues ? "输入不符合要求，请检查后重试"
      : `操作失败：${e.message}`;
  } finally {
    busy.value = false;
  }
}
onMounted(() => { mode.value = new URLSearchParams(location.hash.split("?")[1] || "").get("mode") === "register" ? "register" : "login"; });
</script>

<template>
  <section class="auth-page">
    <button class="auth-back" @click="emit('back')">
      <UiIcon name="arrow-left" :size="14" /> 返回
    </button>
    <div class="auth-card">
      <div class="auth-logo">知</div>
      <h1 class="auth-title">{{ mode === "register" ? "创建账号" : "登录" }}</h1>
      <p class="auth-sub">
        {{ mode === "register"
          ? "注册后，你当前的所有访谈与草稿自动归入账号，换设备也能用"
          : "登录后同步你的访谈、经历库与简历草稿" }}
      </p>
      <div class="auth-tabs" role="tablist">
        <button :class="{ on: mode === 'login' }" role="tab" @click="switchMode('login')">登录</button>
        <button :class="{ on: mode === 'register' }" role="tab" @click="switchMode('register')">注册</button>
      </div>
      <form class="auth-form" @submit.prevent="submit">
        <label>用户名
          <input v-model="username" autocomplete="username" :disabled="busy"
                 placeholder="2–24 位中英文、数字或 -_" />
        </label>
        <label>密码
          <input v-model="password" type="password" :disabled="busy"
                 :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
                 :placeholder="mode === 'register' ? '至少 8 位' : '登录密码'" />
        </label>
        <label v-if="mode === 'register'">确认密码
          <input v-model="confirm" type="password" autocomplete="new-password"
                 :disabled="busy" placeholder="再输一遍" />
        </label>
        <p v-if="err" class="auth-err" role="alert">{{ err }}</p>
        <button class="auth-submit" type="submit" :disabled="busy">
          {{ busy ? "处理中…" : mode === "register" ? "注册" : "登录" }}
        </button>
      </form>
      <p class="auth-note">
        不注册也可以继续用；数据已在云端与你的身份绑定。<br />
        请牢记密码——暂不提供找回渠道。
      </p>
    </div>
  </section>
</template>

<style scoped>
.auth-page { padding: 20px 16px; max-width: 420px; margin: 0 auto; }
.auth-back {
  border: none; background: none; cursor: pointer; color: var(--ink-secondary);
  font: inherit; font-size: .82rem; padding: 4px 0; margin-bottom: 16px;
}
.auth-back:hover { color: var(--brand); }
.auth-card {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 28px 24px; box-shadow: var(--shadow-sm);
  text-align: center;
}
.auth-logo {
  width: 44px; height: 44px; margin: 0 auto 10px; border-radius: 12px;
  background: var(--brand); color: #fff; font-weight: 700; font-size: 1.2rem;
  display: flex; align-items: center; justify-content: center;
}
.auth-title { font-size: 1.15rem; margin: 0 0 4px; }
.auth-sub { font-size: .8rem; color: var(--ink-secondary); margin: 0 0 16px; }
.auth-tabs {
  display: flex; gap: 4px; background: var(--bg-muted, #f1f3f5);
  border-radius: 10px; padding: 3px; margin-bottom: 16px;
}
.auth-tabs button {
  flex: 1; padding: 8px; border: none; border-radius: 8px; cursor: pointer;
  background: transparent; font: inherit; font-size: .85rem; color: var(--ink-secondary);
}
.auth-tabs button.on { background: var(--bg-surface); color: var(--ink); font-weight: 600; box-shadow: var(--shadow-xs); }
.auth-form { text-align: left; }
.auth-form label { display: block; font-size: .8rem; color: var(--ink-secondary); margin-bottom: 12px; }
.auth-form input {
  display: block; width: 100%; margin-top: 4px; padding: 10px 12px;
  border: 1px solid var(--border); border-radius: 10px; font: inherit; font-size: .9rem;
  background: var(--bg-surface); color: var(--ink); box-sizing: border-box;
}
.auth-form input:focus { outline: 2px solid var(--brand-soft, rgba(59,130,246,.3)); border-color: var(--brand); }
.auth-err { color: var(--danger); font-size: .8rem; margin: 0 0 10px; }
.auth-submit {
  width: 100%; padding: 11px; border: none; border-radius: 10px; cursor: pointer;
  background: var(--brand); color: #fff; font: inherit; font-weight: 600; font-size: .9rem;
}
.auth-submit:disabled { opacity: .6; cursor: default; }
.auth-note { font-size: .72rem; color: var(--ink-muted); margin: 14px 0 0; }
</style>
