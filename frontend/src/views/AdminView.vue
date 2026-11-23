<script setup>
import { computed, onMounted, ref } from "vue";
import { adminDocuments, adminLogin, adminOverview, adminUsers } from "../api";
import { toMirrorHref } from "../kbSource.mjs";

const TOKEN_KEY = "zhimai_admin_token";
const KB_BASE = (import.meta.env.VITE_KB_SOURCE_BASE || "").replace(/\/$/, "");

const SCOPE_CN = {
  freshman: "大一入门",
  sophomore_junior: "大二大三规划",
  senior: "大四升学 / 求职",
  video: "视频总结",
  cases: "案例库",
  major: "专业库",
  meta: "其他",
};

const token = ref("");
const password = ref("");
const loginError = ref("");
const loginBusy = ref(false);

const overview = ref(null);
const users = ref([]);
const usersOpen = ref(false);
const loading = ref(false);
const loadError = ref("");
const query = ref("");
const scope = ref("");
const rows = ref([]);
const tableTitle = ref("最近入库");
const searching = ref(false);

const loggedIn = computed(() => Boolean(token.value));
const scopeEntries = computed(() =>
  Object.entries(overview.value?.by_scope || {}).sort((a, b) => b[1] - a[1])
);

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function saveToken(value) {
  token.value = value;
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {}
}

function relogin(message = "") {
  saveToken("");
  overview.value = null;
  rows.value = [];
  loginError.value = message;
}

async function handleError(e) {
  if (e.status === 401) {
    relogin("登录已失效，请重新登录。");
    return;
  }
  loadError.value = e.message || "请求失败";
}

async function submitLogin() {
  if (!password.value || loginBusy.value) return;
  loginBusy.value = true;
  loginError.value = "";
  try {
    const res = await adminLogin(password.value);
    saveToken(res.token);
    password.value = "";
    await loadOverview();
  } catch (e) {
    loginError.value = e.message || "登录失败";
  } finally {
    loginBusy.value = false;
  }
}

async function loadOverview() {
  loading.value = true;
  loadError.value = "";
  try {
    const res = await adminOverview(token.value);
    overview.value = res.data;
    // v5 账号列表：失败不阻塞知识库面板
    adminUsers(token.value)
      .then((r) => { users.value = r.data?.users || []; })
      .catch(() => {});
    rows.value = res.data.recent || [];
    tableTitle.value = "最近入库";
    query.value = "";
    scope.value = "";
  } catch (e) {
    await handleError(e);
  } finally {
    loading.value = false;
  }
}

async function search() {
  if (!token.value || searching.value) return;
  searching.value = true;
  loadError.value = "";
  try {
    const res = await adminDocuments(token.value, query.value, scope.value);
    rows.value = res.data.items || [];
    tableTitle.value = query.value.trim() || scope.value
      ? `检索结果 ${rows.value.length} 条`
      : "最近入库";
  } catch (e) {
    await handleError(e);
  } finally {
    searching.value = false;
  }
}

function resetSearch() {
  query.value = "";
  scope.value = "";
  if (overview.value) {
    rows.value = overview.value.recent || [];
    tableTitle.value = "最近入库";
  }
}

function logout() {
  relogin("");
}

function sourceHref(row) {
  return toMirrorHref(row.source_url, KB_BASE);
}

onMounted(async () => {
  const saved = readToken();
  if (!saved) return;
  saveToken(saved);
  await loadOverview();
});
</script>

<template>
  <section class="admin">
    <header class="admin-top">
      <div>
        <p class="admin-kicker">知脉 · 管理后台</p>
        <h2>知识库运营面板</h2>
      </div>
      <div class="admin-actions">
        <a class="admin-link" href="./">返回站点</a>
        <button v-if="loggedIn" class="ghost" type="button" @click="logout">退出登录</button>
      </div>
    </header>

    <form v-if="!loggedIn" class="admin-login" @submit.prevent="submitLogin">
      <h3>管理员登录</h3>
      <p class="admin-hint">仅限管理员访问。连续失败 5 次会暂停 5 分钟，会话在两小时后自动失效。</p>
      <label for="admin-pass">管理密码</label>
      <input
        id="admin-pass"
        v-model="password"
        type="password"
        autocomplete="current-password"
        placeholder="请输入管理密码"
        :disabled="loginBusy"
      />
      <p v-if="loginError" class="warn">{{ loginError }}</p>
      <button type="submit" :disabled="!password || loginBusy">
        {{ loginBusy ? "验证中…" : "进入后台" }}
      </button>
    </form>

    <template v-else>
      <p v-if="loadError" class="warn admin-gap">{{ loadError }}</p>
      <p v-if="loading && !overview" class="admin-hint">正在加载知识库统计…</p>

      <template v-if="overview">
        <div class="admin-stats">
          <div class="stat">
            <span>知识文档</span>
            <b>{{ overview.documents }}</b>
          </div>
          <div class="stat">
            <span>检索分段</span>
            <b>{{ overview.chunks }}</b>
          </div>
          <div v-for="[key, count] in scopeEntries" :key="key" class="stat">
            <span>{{ SCOPE_CN[key] || key }}</span>
            <b>{{ count }}</b>
          </div>
        </div>

        <form class="admin-search" @submit.prevent="search">
          <input v-model="query" type="search" placeholder="按标题搜索文档" maxlength="80" />
          <select v-model="scope">
            <option value="">全部范围</option>
            <option v-for="[key, count] in scopeEntries" :key="key" :value="key">
              {{ SCOPE_CN[key] || key }}（{{ count }}）
            </option>
          </select>
          <button type="submit" :disabled="searching">{{ searching ? "检索中…" : "检索" }}</button>
          <button type="button" class="ghost" @click="resetSearch">重置</button>
        </form>

        <div class="admin-table-wrap">
          <div class="admin-table-head">
            <h3>{{ tableTitle }}</h3>
            <span v-if="overview.recent?.length && tableTitle === '最近入库'">最新 8 篇</span>
          </div>
          <table v-if="rows.length" class="admin-table">
            <thead>
              <tr>
                <th>标题</th>
                <th>范围</th>
                <th>字数</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.slug">
                <td class="admin-title">{{ row.title || row.slug }}</td>
                <td><span class="chip hit">{{ SCOPE_CN[row.kb_scope] || row.kb_scope || "其他" }}</span></td>
                <td>{{ row.char_count ?? "—" }}</td>
                <td>
                  <a v-if="sourceHref(row) !== '#'" :href="sourceHref(row)" target="_blank" rel="noopener">
                    镜像
                  </a>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="admin-hint">没有匹配的文档。</p>
        </div>

        <!-- v5 账号用户列表 -->
        <div class="admin-table-wrap">
          <div class="admin-table-head">
            <h3>账号用户（{{ users.length }}）</h3>
            <button type="button" class="ghost" @click="usersOpen = !usersOpen">
              {{ usersOpen ? "收起" : "展开" }}
            </button>
          </div>
          <table v-if="usersOpen && users.length" class="admin-table">
            <thead>
              <tr><th>用户名</th><th>类型</th><th>初始化</th><th>注册时间</th><th>最近登录</th></tr>
            </thead>
            <tbody>
              <tr v-for="u in users" :key="u.id">
                <td class="admin-title">{{ u.username || "（访客）" }}</td>
                <td><span class="chip" :class="{ hit: u.kind === 'user' }">{{ u.kind === "user" ? "注册" : "访客" }}</span></td>
                <td>{{ u.init_done ? "✓" : "—" }}</td>
                <td>{{ (u.created_at || "").slice(0, 16).replace("T", " ") }}</td>
                <td>{{ (u.last_login_at || "").slice(0, 16).replace("T", " ") || "—" }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else-if="usersOpen" class="admin-hint">暂无账号数据。</p>
        </div>
      </template>
    </template>
  </section>
</template>