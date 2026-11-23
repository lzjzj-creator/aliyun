<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from "vue";
import SessionList from "./components/SessionList.vue";
import UiIcon from "./components/UiIcon.vue";
import AuthWall from "./components/AuthWall.vue";
import GuideTour from "./components/GuideTour.vue";
import { authwallAllowed } from "./components/authwall";
import { API_BASE } from "./api/core";
import { ensureAuth, getMe, logout, deleteAccount } from "./api/auth";

// S4 首包瘦身：非首屏视图全部异步 chunk，进入路由时才下载（S6 骨架屏兜底）
import ViewSkeleton from "./components/ViewSkeleton.vue";
const lazy = (loader) => defineAsyncComponent({
  loader, loadingComponent: ViewSkeleton, delay: 120,
});
const AdminView = lazy(() => import("./views/AdminView.vue"));
const ChatView = lazy(() => import("./views/ChatView.vue"));
const ContractDemoView = lazy(() => import("./views/ContractDemoView.vue"));
const InterviewView = lazy(() => import("./views/InterviewView.vue"));
const MemoryView = lazy(() => import("./views/MemoryView.vue"));
const ResumeLibraryView = lazy(() => import("./views/ResumeLibraryView.vue"));
const ResumeView = lazy(() => import("./views/ResumeView.vue"));
const ResumeWizardView = lazy(() => import("./views/ResumeWizardView.vue"));
const AuthView = lazy(() => import("./views/AuthView.vue"));
const OnboardingView = lazy(() => import("./views/OnboardingView.vue"));

// hash 路由：#chat | #chat/:id | #chat-v1 | #resume | #resume-v1 | #demo | #admin | #auth | #onboarding
const view = ref("chat");
const sidebar = ref(false);          // 移动端会话抽屉
const chatSid = ref("");   // 作为 InterviewView 的 key：切会话即重挂载走 boot()
const resumeDocId = ref(""); // #/resume/library/:id 的文档 id
const sessionListRef = ref(null);
const collapsed = ref(false);        // 侧栏折叠态（UI 偏好，非业务数据）

// —— 账号态（v5）：guest 静默签发 → me 拉取 → 顶栏登录态/引导 ——
const me = ref(null);                // {id, kind, username, init_done, guide_seen, ...}
const showAuthWall = ref(false);
const showTour = ref(false);
const deleteArmed = ref(false);      // 顶栏删除账号的两步确认态
const deleting = ref(false);
const authReturn = ref("#/chat");    // 登录/注册成功后的回原页目标
let authwallArmed = false;           // 本次会话内是否已在价值时刻弹过一次

try {
  collapsed.value = localStorage.getItem("zm_sidebar_collapsed") === "1";
} catch { /* 存储不可用时保持展开 */ }

function toggleCollapsed() {
  collapsed.value = !collapsed.value;
  try { localStorage.setItem("zm_sidebar_collapsed", collapsed.value ? "1" : "0"); }
  catch { /* 忽略持久化失败 */ }
}

const isGuest = computed(() => !me.value || me.value.kind === "guest");
const accountLabel = computed(() =>
  me.value?.kind === "user" ? (me.value.username || "我的账号") : "访客");

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
  auth: ["账号", "登录或注册，数据都在云端"],
  onboarding: ["初始化画像", "一次填写，访谈不再查户口"],
};
const pageTitle = computed(() => (PAGE_META[view.value] || ["知脉 · 简历成长助手", ""])[0]);
const pageSub = computed(() => (PAGE_META[view.value] || ["", ""])[1]);

function syncRoute() {
  const h = location.hash || "#/chat";
  if (/^#\/?admin\/?$/.test(h)) { view.value = "admin"; return; }
  if (/^#\/?demo/.test(h)) { view.value = "contractDemo"; return; }
  if (/^#\/?memory\/?$/.test(h)) { view.value = "memory"; return; }
  if (/^#\/?auth\/?/.test(h)) { view.value = "auth"; return; }
  if (/^#\/?onboarding\/?$/.test(h)) { view.value = "onboarding"; return; }
  if (/^#\/?resume-v1/.test(h)) { view.value = "resumeV1"; return; }
  const lib = h.match(/^#\/?resume\/library\/?([0-9a-zA-Z-]+)?/);
  if (lib) { view.value = "resumeLibrary"; resumeDocId.value = lib[1] || ""; return; }
  if (/^#\/?resume/.test(h)) { view.value = "resume"; return; }
  if (/^#\/?chat-v1/.test(h)) { view.value = "chatV1"; return; }
  const m = h.match(/^#\/?chat\/?([0-9a-zA-Z-]+)?/);
  view.value = "chat";
  chatSid.value = m?.[1] || "";
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
  nav("#/chat");
}

function onSessionCreated() {
  sessionListRef.value?.reload?.();
  // 价值时刻：guest 完成第一条会话创建后弹一次软墙（可关、24h 节流）
  maybeAuthWall();
}

function maybeAuthWall() {
  if (!isGuest.value || authwallArmed || !authwallAllowed()) return;
  authwallArmed = true;
  showAuthWall.value = true;
}

// 软墙触发点（§4）：①建会话 ②进简历向导（点"生成简历草稿"）③开简历库/经历库
watch(view, (v) => {
  if (["resume", "resumeLibrary", "memory"].includes(v)) maybeAuthWall();
});

function navAuth(mode) {
  // 记住发起页：登录成功回原页（§4），注册成功则走初始化不回原页
  const h = location.hash || "#/chat";
  if (!h.startsWith("#/auth")) authReturn.value = h;
  nav(mode === "register" ? "#/auth?mode=register" : "#/auth");
}

async function refreshMe() {
  try { me.value = await getMe(); } catch { me.value = null; }
}

// 教程门控（§4 旅程图）：注册 + 初始化后才进工作台播气泡教程；guest 不播。
function maybeTour() {
  if (me.value?.kind === "user" && me.value.init_done === true &&
      me.value.guide_seen === false && view.value !== "admin") {
    showTour.value = true;
  }
}

async function onAuthed(user) {
  me.value = { ...me.value, ...user, kind: "user" };
  showAuthWall.value = false;
  // 新注册（needs_init）或未完成初始化 → 去初始化表单；否则回原页
  if (user.needs_init || me.value.init_done === false) {
    nav("#/onboarding");
  } else {
    const back = authReturn.value;
    nav(back && back.startsWith("#/") && !back.startsWith("#/auth") ? back : "#/chat");
  }
  refreshMe().then(maybeTour);
}

async function onLogout() {
  await logout();
  me.value = null;
  deleteArmed.value = false;
  await ensureAuth();        // 静默签新 guest，数据从空开始
  refreshMe();
  nav("#/chat");
}

async function onDeleteAccount() {
  if (!deleteArmed.value) { deleteArmed.value = true; return; }
  deleting.value = true;
  try {
    await deleteAccount();
    me.value = null;
    deleteArmed.value = false;
    await ensureAuth();      // 换新 guest 继续可用
    refreshMe();
    nav("#/chat");
  } catch {
    // 失败保留账号态，让用户重试
  } finally {
    deleting.value = false;
  }
}

function onInitDone() {
  if (me.value) me.value.init_done = true;
  nav("#/chat");
  refreshMe().then(maybeTour);
}

function onInitSkip() {
  nav("#/chat");             // 允许跳过：init_done 保持 false，顶栏「画像」可再进
}

function onTourDone() {
  showTour.value = false;
  nav("#/chat");             // 教程结尾 CTA：开始访谈
}

function onTourGoto(hash) {
  nav(hash);
}

onMounted(async () => {
  if (!location.hash) location.hash = "#/chat";
  syncRoute();
  window.addEventListener("hashchange", syncRoute);
  // S2 预热：preconnect API 源 + 打开页面即静默 ping /health
  try {
    if (API_BASE && /^https?:/.test(API_BASE)) {
      const link = document.createElement("link");
      link.rel = "preconnect";
      link.href = new URL(API_BASE).origin;
      document.head.appendChild(link);
    }
    fetch(`${API_BASE}/health`).catch(() => {});
  } catch { /* 忽略 */ }
  // 静默 guest 签发 → me → 决定是否播教程
  await ensureAuth();
  await refreshMe();
  maybeTour();
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
          <button v-if="view === 'chat'" class="tb-sessions" @click="sidebar = true">
            <UiIcon name="menu" :size="15" /> 会话
          </button>
          <h1 class="tb-title">{{ pageTitle }}</h1>
          <span v-if="pageSub" class="tb-sub">{{ pageSub }}</span>
        </div>
        <div class="tb-right">
          <details class="tb-privacy-pop">
            <summary><UiIcon name="shield" :size="13" /> 数据在云端</summary>
            <div class="tb-privacy-card">
              所有数据存在你的云端账号里：可随时逐条删除，注销账号即清空。
              {{ isGuest ? "当前为访客身份，注册后数据自动归入账号。" : "" }}
            </div>
          </details>
          <div class="tb-account">
            <template v-if="isGuest">
              <button class="tb-auth-btn ghost" @click="navAuth()">登录</button>
              <button class="tb-auth-btn" @click="navAuth('register')">注册</button>
            </template>
            <template v-else>
              <details class="tb-menu">
                <summary class="tb-user" :title="accountLabel">
                  <UiIcon name="user" :size="13" /> {{ accountLabel }}
                  <UiIcon name="chevron-down" :size="12" />
                </summary>
                <div class="tb-menu-card">
                  <button class="tb-menu-item" @click="nav('#/onboarding')">资料初始化</button>
                  <button class="tb-menu-item" @click="onLogout">退出登录</button>
                  <button class="tb-menu-item danger" :disabled="deleting" @click="onDeleteAccount">
                    {{ deleteArmed ? (deleting ? "删除中…" : "确认删除（不可恢复）") : "删除账号" }}
                  </button>
                </div>
              </details>
            </template>
          </div>
        </div>
      </header>

      <main class="main-area" :class="{ 'main-flush': view === 'chat' }">
        <!-- 访谈：会话栏 | 消息流 | 画像栏 三柱工作台 -->
        <div v-if="view === 'chat'" class="iv-workbench">
          <SessionList ref="sessionListRef" class="iv-side-col" inline :active-id="chatSid"
                       @select="onSelectSession" @new="onNewSession" />
          <InterviewView class="iv-main-col" :key="chatSid" :sid="chatSid"
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
        <AuthView v-else-if="view === 'auth'" @authed="onAuthed"
                  @back="nav(authReturn || '#/chat')" />
        <OnboardingView v-else-if="view === 'onboarding'"
                        @done="onInitDone" @skip="onInitSkip" />
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

    <!-- 注册软墙（guest 价值时刻一次，24h 节流） -->
    <AuthWall v-if="showAuthWall"
              @register="showAuthWall = false; navAuth('register')"
              @dismiss="showAuthWall = false" />
    <!-- 新手气泡教程：每账号一次 -->
    <GuideTour v-if="showTour"
               @goto="onTourGoto"
               @done="onTourDone" @skip="showTour = false" />
  </div>
</template>

<style scoped>
.tb-right { display: flex; align-items: center; gap: 10px; }
.tb-account { display: flex; align-items: center; gap: 6px; }
.tb-user {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: .8rem; color: var(--ink-secondary); max-width: 140px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  cursor: pointer; list-style: none;
}
.tb-menu { position: relative; }
.tb-menu > summary::-webkit-details-marker { display: none; }
.tb-menu-card {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: 60;
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: var(--shadow-lg); padding: 6px;
  min-width: 148px; display: flex; flex-direction: column;
}
.tb-menu-item {
  border: none; background: none; text-align: left; cursor: pointer;
  font: inherit; font-size: .8rem; color: var(--ink); padding: 8px 10px;
  border-radius: 7px;
}
.tb-menu-item:hover { background: var(--bg-muted, #f1f3f5); }
.tb-menu-item.danger { color: var(--danger); }
.tb-menu-item:disabled { opacity: .6; }
.tb-auth-btn {
  padding: 6px 12px; border-radius: var(--radius-pill); cursor: pointer;
  border: 1px solid var(--brand); background: var(--brand); color: #fff;
  font: inherit; font-size: .78rem; font-weight: 600;
}
.tb-auth-btn.ghost { background: var(--bg-surface); color: var(--brand); }
.tb-auth-btn:hover { filter: brightness(.96); }
</style>
