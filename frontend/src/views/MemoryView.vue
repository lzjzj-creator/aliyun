<script setup>
// v3 #memory 页：长期记忆总览 + 事实确认/拒绝/删除 + 导出/清空（§9）
import { computed, onMounted, ref } from "vue";
import { listSessions } from "../api/sessions";
import {
  deleteAllMemory, deleteFact, disableMemory, enableMemory, exportMemory,
  backfill, hasSessionCredential, forgetSession, getMemory, getOwnerToken, listFacts,
  patchFact, recoverMemory, setOwnerToken,
} from "../api/v3";

const enabled = ref(false);
const booted = ref(false);
const status = ref(null);
const facts = ref([]);
const nextCursor = ref(null);
const filter = ref("");            // "" | proposed | confirmed | rejected
const kindFilter = ref("");
const busy = ref(false);
const error = ref("");
const onboarding = ref(null);      // {owner_token, recovery_code} 一次性展示
const confirmingWipe = ref(false);
const showConsent = ref(false);    // 开启前的明示同意弹窗
const recoveryCode = ref("");
const recoveryError = ref("");
const disabledMemory = ref(false);
const showRecovery = ref(false);
const sessionTitles = ref({});     // session_id -> v2 会话标题
const showPrivacy = ref(false);    // 隐私抽屉：导出/停用/清空集中在此

function sessionTitle(sid) {
  return sessionTitles.value[sid] || `会话 ${String(sid).slice(0, 8)}…`;
}

// 事实的 slot_path → 中文槽位名（合同槽位段名 → 显示名）
const SLOT_LABELS = {
  basics: "基本信息", basic: "基本信息", education: "教育经历",
  internships: "实习经历", internship: "实习经历", projects: "项目经历",
  project: "项目经历", skills: "技能", skill_groups: "技能",
  awards: "奖项", award: "奖项", summary: "自我评价", campus: "校园经历",
  volunteer: "志愿经历", publications: "论文发表", name: "姓名",
  school: "学校", role: "目标岗位", objective: "求职意向",
};
function slotLabel(path) {
  if (!path) return "其他";
  const seg = String(path).split(".")[0];
  return SLOT_LABELS[seg] || path;
}

// KPI：事实总量 + 各状态计数（来自 status.by_status）
const kpiProposed = computed(() => status.value?.by_status?.proposed || 0);
const kpiConfirmed = computed(() => status.value?.by_status?.confirmed || 0);
const kpiRejected = computed(() => status.value?.by_status?.rejected || 0);

const KIND_LABELS = {
  identity: "身份", education: "教育", experience: "经历",
  skill: "技能", metric: "成果", award: "奖项", goal: "意向",
};
const STATUS_LABELS = {
  proposed: "待确认", confirmed: "已确认",
  rejected: "已拒绝", superseded: "已取代",
};

// 来源已删：证据被清空（忘掉会话）或证据会话指针全部置空（D6 清理）
function sourceExpired(fact) {
  const evs = fact.evidence || [];
  return evs.length === 0 || evs.every((e) => e.session_id == null);
}

// ---- #70 历史回填：会话多选 + 分块顺序调用 + 进度 ----
const allSessions = ref([]);       // v2 会话全量（供勾选）
const picked = ref(new Set());
const backfilling = ref(false);
const backfillDone = ref(0);
const backfillTotal = ref(0);
const backfillResults = ref([]);   // 每会话 {session_id, added/.../error}

function togglePick(sid) {
  if (!hasSessionCredential(sid)) {
    error.value = "缺少该会话的本地凭据，无法回填；请使用原浏览器。";
    return;
  }
  const s = new Set(picked.value);
  if (s.has(sid)) s.delete(sid); else s.add(sid);
  picked.value = s;
}

async function onBackfill() {
  const ids = [...picked.value];
  if (!ids.length) return;
  error.value = "";
  if (ids.some((sid) => !hasSessionCredential(sid))) {
    error.value = "所选会话缺少本地凭据，无法回填；请重新选择。";
    return;
  }
  backfilling.value = true;
  backfillDone.value = 0;
  backfillTotal.value = ids.length;
  backfillResults.value = [];
  try {
    // 分块≤5：留在一小时限额内，同时让进度条真实前进
    for (let i = 0; i < ids.length; i += 5) {
      const res = await backfill(ids.slice(i, i + 5));
      backfillResults.value.push(...(res.results || []));
      backfillDone.value += (res.results || []).length;
    }
  } catch (e) {
    error.value = `回填中断，已完成部分已生效：${e.message}`;
  } finally {
    backfilling.value = false;
    picked.value = new Set();
    await reload().catch(() => {});
    status.value = await getMemory().catch(() => status.value);
  }
}

// 事实证据里出现过的会话列表（§9 来源级删除入口）
const sessions = computed(() => {
  const map = new Map();
  for (const f of facts.value) {
    for (const e of f.evidence || []) {
      if (e && e.session_id) {
        const cur = map.get(e.session_id) || { session_id: e.session_id, count: 0 };
        cur.count += 1;
        map.set(e.session_id, cur);
      }
    }
  }
  return [...map.values()];
});

const forgetting = ref(null);   // 待确认忘掉的 session_id

async function boot() {
  booted.value = false;
  error.value = "";
  if (!getOwnerToken()) { enabled.value = false; booted.value = true; return; }
  try {
    status.value = await getMemory();
    enabled.value = status.value.enabled === true;
    disabledMemory.value = status.value.enabled === false;
    if (!enabled.value) { facts.value = []; return; }
    // 会话标题映射用于事实卡片来源标注；拿不到（无 v2 会话）不阻塞
    listSessions().then((list) => {
      const map = {};
      for (const s of list || []) if (s && s.id) map[s.id] = s.title;
      sessionTitles.value = map;
      allSessions.value = list || [];
    }).catch(() => {});
    await reload();
  } catch (e) {
    enabled.value = false;
    facts.value = [];
    status.value = null;
    error.value = e.status === 404
      ? "当前令牌无效或记忆已停用。可用原令牌重新开启，或用恢复码换发新凭据。"
      : "记忆服务暂时不可用，请稍后重试";
  } finally {
    booted.value = true;
  }
}

async function onEnable() {
  showConsent.value = false;
  busy.value = true;
  try {
    const res = await enableMemory({ recovery: true });
    if (res.owner_token) {
      setOwnerToken(res.owner_token);
      onboarding.value = res;       // 明文仅此一次展示
    }
    await boot();
  } catch {
    error.value = "开启失败，请稍后重试";
  } finally {
    busy.value = false;
  }
}

async function onRecover() {
  recoveryError.value = "";
  if (!recoveryCode.value.trim()) { recoveryError.value = "请输入恢复码"; return; }
  busy.value = true;
  try {
    const res = await recoverMemory(recoveryCode.value);
    onboarding.value = res;
    recoveryCode.value = "";
    showRecovery.value = false;
    disabledMemory.value = false;
    await boot();
  } catch (e) {
    recoveryError.value = e.status === 404 ? "恢复码无效或已使用，请核对最新保存的恢复码。"
      : e.status === 429 ? "尝试过于频繁，请稍后重试。"
      : e.status === 422 ? "恢复码格式不正确，请检查输入。"
      : "恢复失败，请稍后重试；原有本地令牌未被清除。";
  } finally {
    busy.value = false;
  }
}

function copyCredentials() {
  const o = onboarding.value;
  if (!o) return;
  const text = `知脉长期记忆凭据\n访问令牌: ${o.owner_token}\n` +
    (o.recovery_code ? `恢复码: ${o.recovery_code}\n` : "");
  navigator.clipboard?.writeText(text).catch(() => {});
}

function downloadCredentials() {
  const o = onboarding.value;
  if (!o) return;
  const text = `知脉长期记忆凭据（请离线保存，勿泄露）\n\n` +
    `访问令牌: ${o.owner_token}\n` +
    (o.recovery_code ? `恢复码: ${o.recovery_code}\n` : "") +
    `\n令牌与恢复码只显示这一次，丢失后可用恢复码换发新令牌。`;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "zhimai-credentials.txt";
  a.click();
  URL.revokeObjectURL(a.href);
}

async function confirmAll() {
  busy.value = true;
  try {
    for (const f of facts.value.filter((x) => x.status === "proposed")) {
      await patchFact(f.id, { status: "confirmed" });
    }
    await reload();
    status.value = await getMemory();
  } catch {
    error.value = "批量确认中断，请重试剩余项";
  } finally {
    busy.value = false;
  }
}

function dismissOnboarding() {
  onboarding.value = null;
}

async function reload() {
  const page = await listFacts({
    status: filter.value || undefined,
    kind: kindFilter.value || undefined,
  });
  facts.value = page.facts;
  nextCursor.value = page.next_cursor;
}

async function loadMore() {
  if (!nextCursor.value) return;
  const page = await listFacts({
    status: filter.value || undefined,
    kind: kindFilter.value || undefined,
    cursor: nextCursor.value,
  });
  facts.value = facts.value.concat(page.facts);
  nextCursor.value = page.next_cursor;
}

async function act(fact, action) {
  busy.value = true;
  try {
    if (action === "delete") await deleteFact(fact.id);
    else await patchFact(fact.id, { status: action });
    await reload();
    status.value = await getMemory();
  } catch (e) {
    error.value = e.status === 404 ? "该事实已不存在" : "操作失败，请重试";
  } finally {
    busy.value = false;
  }
}

async function onExport() {
  busy.value = true;
  try {
    const blob = await exportMemory();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "zhimai-memory.json";
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    error.value = "导出失败，请稍后重试";
  } finally {
    busy.value = false;
  }
}

async function onDisable() {
  if (!confirm("停用后不再从新会话抽取事实，已有事实保留。确认停用？")) return;
  busy.value = true;
  try {
    await disableMemory();
    enabled.value = false;
    disabledMemory.value = true;
    facts.value = [];
    status.value = null;
    picked.value = new Set();
    onboarding.value = null;
  } catch {
    error.value = "操作失败";
  } finally {
    busy.value = false;
  }
}

async function onForgetSession(sid) {
  busy.value = true;
  try {
    await forgetSession(sid);
    forgetting.value = null;
    await reload();
  } catch (e) {
    error.value = e.status === 404 ? "会话不存在" : "操作失败，请重试";
  } finally {
    busy.value = false;
  }
}

async function onWipe() {
  busy.value = true;
  try {
    await deleteAllMemory();
    setOwnerToken("");
    onboarding.value = null;
    disabledMemory.value = false;
    facts.value = [];
    status.value = null;
    enabled.value = false;
    confirmingWipe.value = false;
  } catch {
    error.value = "清空失败，请重试";
  } finally {
    busy.value = false;
  }
}

onMounted(boot);
</script>

<template>
  <div class="mem-page">
    <h2 class="mem-title">长期记忆</h2>

    <div v-if="onboarding" class="mem-onboard">
      <p><b>请立即保存以下新凭据，关闭后不再显示；换发后旧令牌与旧恢复码失效：</b></p>
      <p>访问令牌：<code>{{ onboarding.owner_token }}</code></p>
      <p v-if="onboarding.recovery_code">恢复码：<code>{{ onboarding.recovery_code }}</code></p>
      <div class="mem-ops">
        <button class="mem-btn" @click="copyCredentials">复制</button>
        <button class="mem-btn" @click="downloadCredentials">下载保存</button>
        <button class="mem-btn" @click="dismissOnboarding">我已保存</button>
      </div>
    </div>

    <div class="mem-card">
      <button class="mem-btn" :disabled="busy" @click="showRecovery = !showRecovery">使用恢复码换发凭据</button>
      <form v-if="showRecovery" @submit.prevent="onRecover">
        <p class="mem-sub">换发后旧令牌及旧恢复码失效，请保存新凭据。恢复记忆不会自动恢复本机缺失的会话凭据。</p>
        <label>恢复码 <input v-model="recoveryCode" type="password" autocomplete="off" :disabled="busy" /></label>
        <button class="mem-btn primary" type="submit" :disabled="busy || !recoveryCode.trim()">确认兑换</button>
        <p v-if="recoveryError" class="mem-err" role="alert">{{ recoveryError }}</p>
      </form>
    </div>

    <div v-if="!booted" class="mem-hint">加载中…</div>

    <!-- 未开启：引导而非空壳 -->
    <div v-else-if="!enabled" class="mem-empty">
      <p>长期记忆会在会话间记住你的经历、技能与成果，用于生成多份岗位简历。</p>
      <p class="mem-sub">默认关闭，需明示开启；事实可逐条查看、确认、拒绝或删除。</p>
      <button class="mem-btn primary" :disabled="busy"
              @click="showConsent = true">
        {{ disabledMemory ? '重新开启长期记忆' : '开启长期记忆' }}
      </button>
      <p v-if="disabledMemory" class="mem-sub">长期记忆已停用，已有事实保留。重新开启将使用本机原令牌恢复同一份记忆。</p>
      <p v-if="error" class="mem-err">{{ error }}</p>

      <!-- 明示同意弹窗（§9：确认后才调 enable 接口） -->
      <div v-if="showConsent" class="mem-consent">
        <div class="mem-consent-box">
          <h3>开启长期记忆前请确认</h3>
          <ul>
            <li>将跨会话记住你主动说出的经历、技能与成果，用于生成岗位简历。</li>
            <li>每条事实都带原话证据，可随时查看、确认、拒绝、删除或导出。</li>
            <li>数据保留 365 天，可随时一键清空全部；关闭后不再抽取。</li>
            <li>令牌与恢复码只显示一次，请妥善保存；丢失可用恢复码找回。</li>
          </ul>
          <div class="mem-ops">
            <button class="mem-btn primary" :disabled="busy" @click="onEnable">
              我同意，开启长期记忆
            </button>
            <button class="mem-btn" @click="showConsent = false">再想想</button>
          </div>
        </div>
      </div>
    </div>

    <template v-else>

      <div v-if="status?.retention_warning" class="mem-stale">
        你的记忆已 {{ status.retention_days }} 天保留期临近到期（30 天内无活动将删除），
        建议先「导出 JSON」备份。
      </div>

      <!-- KPI 卡片：事实总量与各状态计数 -->
      <div v-if="status" class="mem-kpis">
        <div class="kpi"><b>{{ status.fact_count }}</b><span>事实总数</span></div>
        <div class="kpi kpi-warn"><b>{{ kpiProposed }}</b><span>待确认</span></div>
        <div class="kpi kpi-ok"><b>{{ kpiConfirmed }}</b><span>已确认</span></div>
        <div class="kpi"><b>{{ kpiRejected }}</b><span>已拒绝</span></div>
      </div>

      <div class="mem-statusline">
        <span v-if="status?.last_extracted_at">
          最近抽取 {{ status.last_extracted_at.slice(0, 16).replace("T", " ") }}
        </span>
        <button class="mem-btn" :disabled="busy" @click="showPrivacy = true">隐私与数据</button>
      </div>

      <!-- 隐私抽屉：导出 / 停用 / 清空集中收纳 -->
      <div v-if="showPrivacy" class="mem-drawer-mask" @click.self="showPrivacy = false">
        <aside class="mem-drawer" role="dialog" aria-modal="true" aria-label="隐私与数据">
          <div class="md-head">
            <b>隐私与数据</b>
            <button class="md-x" aria-label="关闭" @click="showPrivacy = false">×</button>
          </div>
          <p class="mem-sub">每条事实都可逐条确认、拒绝或删除；下方操作影响全部数据。</p>
          <button class="mem-btn wide" :disabled="busy" @click="onExport">导出 JSON 备份</button>
          <button class="mem-btn wide" :disabled="busy" @click="onDisable">停用记忆（保留已有事实）</button>
          <div class="md-danger">
            <b>危险区</b>
            <button v-if="!confirmingWipe" class="mem-btn danger wide" :disabled="busy"
                    @click="confirmingWipe = true">清空全部</button>
            <template v-else>
              <p class="mem-wipe-text">将永久删除全部事实与简历文档，不可恢复。</p>
              <button class="mem-btn danger wide" :disabled="busy" @click="onWipe">确认清空</button>
              <button class="mem-btn wide" @click="confirmingWipe = false">取消</button>
            </template>
          </div>
        </aside>
      </div>

      <!-- 历史回填：勾选会话 → 逐块抽取 → 进度 + 结果 -->
      <div v-if="allSessions.length" class="mem-card">
        <div class="mem-sec-title">回填历史会话</div>
        <div class="bf-list">
          <label v-for="s in allSessions" :key="s.id" class="bf-item">
            <input type="checkbox" :checked="picked.has(s.id)"
                   :disabled="backfilling || !hasSessionCredential(s.id)" @change="togglePick(s.id)" />
            {{ s.title || s.id.slice(0, 8) }}
            <span v-if="!hasSessionCredential(s.id)" class="mem-sub">（缺少本地会话凭据，无法回填，请使用原浏览器）</span>
          </label>
        </div>
        <div class="mem-ops">
          <button class="mem-btn" :disabled="backfilling || !picked.size"
                  @click="onBackfill">
            {{ backfilling ? `回填中 ${backfillDone}/${backfillTotal}` : `回填所选（${picked.size}）` }}
          </button>
        </div>
        <div v-if="backfilling || backfillResults.length" class="bf-progress">
          <div class="bf-bar"
               :style="{ width: backfillTotal ? (backfillDone / backfillTotal * 100) + '%' : '0%' }" />
        </div>
        <ul v-if="backfillResults.length" class="bf-results">
          <li v-for="r in backfillResults" :key="r.session_id">
            {{ sessionTitle(r.session_id) }}：
            <template v-if="r.error">{{ {daily_quota: "今日额度已尽", not_found: "会话不存在", failed: "抽取失败"}[r.error] || r.error }}</template>
            <template v-else>+{{ r.added }} 新 / {{ r.merged }} 合并 / {{ r.skipped }} 跳过</template>
          </li>
        </ul>
      </div>

      <!-- 来源级删除：忘掉某个会话 -->
      <div v-if="sessions.length" class="mem-card mem-sessions">
        <div class="mem-sec-title">来源会话</div>
        <div v-for="s in sessions" :key="s.session_id" class="mem-session-row">
          <span class="mem-session-id">{{ sessionTitle(s.session_id) }}</span>
          <span class="mem-sub">{{ s.count }} 条证据</span>
          <button v-if="forgetting !== s.session_id" class="mem-btn"
                  :disabled="busy" @click="forgetting = s.session_id">
            忘掉这个会话
          </button>
          <template v-else>
            <button class="mem-btn danger" :disabled="busy"
                    @click="onForgetSession(s.session_id)">确认忘掉</button>
            <button class="mem-btn" @click="forgetting = null">取消</button>
          </template>
        </div>
        <p class="mem-sub">
          忘掉后将清除该会话的来源指针与证据原话（含简历中的引用），事实本身保留，可逐条决定去留。
        </p>
      </div>

      <!-- 筛选 + 事实列表 -->
      <div class="mem-filters">
        <button v-for="f in ['', 'proposed', 'confirmed', 'rejected']" :key="f"
                class="mem-chip" :class="{ on: filter === f }"
                @click="filter = f; reload()">
          {{ f ? STATUS_LABELS[f] : "全部" }}
        </button>
        <button v-if="status?.by_status?.proposed" class="mem-chip"
                :disabled="busy" @click="confirmAll">
          全部确认（{{ status.by_status.proposed }}）
        </button>
      </div>

      <ul class="mem-facts">
        <li v-for="f in facts" :key="f.id" class="mem-fact">
          <div class="mf-head">
            <span class="mf-kind">{{ KIND_LABELS[f.kind] || f.kind }}</span>
            <span class="mf-status" :data-s="f.status">
              {{ STATUS_LABELS[f.status] || f.status }}
            </span>
            <span class="mf-slot">{{ slotLabel(f.slot_path) }}</span>
            <span v-if="sourceExpired(f)" class="mf-expired">来源会话已删除</span>
          </div>
          <div class="mf-value">{{ typeof f.value === 'string' ? f.value : JSON.stringify(f.value) }}</div>
          <div v-for="(e, i) in f.evidence" :key="i" class="mf-quote">
            <span v-if="e.session_id" class="mf-src">
              {{ sessionTitle(e.session_id) }}<template v-if="e.msg_id != null"> · 第{{ e.msg_id }}条</template>
            </span>
            “{{ e.quote }}”
          </div>
          <div class="mf-ops">
            <button v-if="f.status === 'proposed'" class="mem-btn sm"
                    :disabled="busy" @click="act(f, 'confirmed')">确认</button>
            <button v-if="f.status === 'proposed'" class="mem-btn sm"
                    :disabled="busy" @click="act(f, 'rejected')">拒绝</button>
            <button class="mem-btn sm danger" :disabled="busy"
                    @click="act(f, 'delete')">删除</button>
          </div>
        </li>
      </ul>
      <button v-if="nextCursor" class="mem-btn" :disabled="busy" @click="loadMore">
        加载更多
      </button>
      <p v-if="error" class="mem-err">{{ error }}</p>
    </template>
  </div>
</template>

<style scoped>
.mem-page { max-width: 720px; margin: 0 auto; padding: 20px 16px 60px; }
.mem-title { font-size: 20px; margin: 0 0 16px; font-weight: 650; }
.mem-empty, .mem-card, .mem-onboard, .mem-wipe {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 16px; margin-bottom: 14px;
}
.mem-sub { color: var(--ink-secondary); font-size: 13px; }
.mem-hint { color: var(--ink-muted); }
.mem-err { color: var(--danger); font-size: 13px; }
.mem-ops { display: flex; gap: 8px; flex-wrap: wrap; }
.mem-btn {
  border: 1px solid var(--border-strong); background: var(--bg-surface); border-radius: 8px;
  padding: 7px 14px; font-size: 13px; cursor: pointer; font-family: inherit; color: var(--ink);
}
.mem-btn:hover:not(:disabled) { background: var(--bg-subtle); }
.mem-btn.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.mem-btn.primary:hover:not(:disabled) { background: var(--brand-hover); }
.mem-btn.danger { color: var(--danger); border-color: var(--danger-border); }
.mem-btn.danger:hover:not(:disabled) { background: var(--danger-soft); }
.mem-btn.sm { padding: 4px 10px; font-size: 12px; }
.mem-btn.wide { width: 100%; text-align: left; }
.mem-btn:disabled { opacity: 0.5; cursor: default; }

/* KPI 卡片 */
.mem-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.kpi {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md);
  padding: 14px 16px; display: flex; flex-direction: column; gap: 2px; box-shadow: var(--shadow-xs);
}
.kpi b { font-size: 1.35rem; font-variant-numeric: tabular-nums; font-weight: 700; }
.kpi span { font-size: .72rem; color: var(--ink-muted); }
.kpi-warn b { color: var(--coach); }
.kpi-ok b { color: var(--agent); }
.mem-statusline { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 14px; font-size: .78rem; color: var(--ink-muted); }

/* 隐私抽屉 */
.mem-drawer-mask {
  position: fixed; inset: 0; z-index: 60; background: rgba(15, 23, 42, .42);
  display: flex; justify-content: flex-end;
}
.mem-drawer {
  width: min(360px, 92vw); height: 100%; overflow-y: auto;
  background: var(--bg-surface); padding: 18px; display: flex; flex-direction: column; gap: 10px;
  box-shadow: var(--shadow-md); animation: drawer-in var(--dur-base) var(--ease);
}
@keyframes drawer-in { from { transform: translateX(24px); opacity: .6; } }
.md-head { display: flex; align-items: center; justify-content: space-between; }
.md-head b { font-size: .95rem; }
.md-x { border: none; background: none; font-size: 18px; color: var(--ink-muted); cursor: pointer; padding: 2px 6px; border-radius: var(--radius-xs); }
.md-x:hover { background: var(--bg-hover); color: var(--ink); }
.md-danger { margin-top: 8px; border-top: 1px dashed var(--danger-border); padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.md-danger b { font-size: .8rem; color: var(--danger); }
.mem-wipe-text { font-size: .8rem; color: var(--danger); margin: 0; }

.mem-filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.mem-chip {
  border: 1px solid var(--border); border-radius: var(--radius-pill); background: var(--bg-surface);
  padding: 4px 12px; font-size: 12px; cursor: pointer; font-family: inherit; color: var(--ink-secondary);
}
.mem-chip.on { background: var(--brand-soft); border-color: var(--brand-border); color: var(--brand); }
.mem-facts { list-style: none; margin: 0; padding: 0; }
.mem-fact {
  border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-surface);
  padding: 12px 14px; margin-bottom: 10px; box-shadow: var(--shadow-xs);
}
.mf-head { display: flex; gap: 8px; align-items: center; font-size: 12px; flex-wrap: wrap; }
.mf-kind { background: var(--brand-soft); color: var(--brand); border-radius: 4px; padding: 1px 6px; }
.mf-status[data-s="confirmed"] { color: var(--agent); }
.mf-status[data-s="proposed"] { color: var(--coach); }
.mf-status[data-s="rejected"], .mf-status[data-s="superseded"] { color: var(--ink-muted); }
.mf-slot { color: var(--ink-muted); font-size: .72rem; }
.mf-expired { color: var(--coach); font-size: 11px; }
.mf-value { font-size: 14px; margin: 6px 0; }
/* 证据引用卡 */
.mf-quote {
  font-size: .78rem; color: var(--ink-secondary); margin: 6px 0;
  background: var(--agent-soft); border-left: 2px solid var(--agent);
  border-radius: 0 8px 8px 0; padding: 7px 10px;
}
.mf-src { display: block; font-size: 11px; color: var(--agent); margin-bottom: 2px; }
.mem-consent {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .42); z-index: 50;
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.mem-consent-box {
  background: var(--bg-surface); border-radius: var(--radius-md); padding: 20px;
  max-width: 480px; width: 100%; box-shadow: var(--shadow-md);
}
.mem-consent-box h3 { margin: 0 0 10px; font-size: 16px; }
.mem-consent-box ul { margin: 0 0 14px; padding-left: 20px; font-size: 13px;
  color: var(--ink-secondary); }
.mem-consent-box li { margin: 6px 0; }
.mem-session-row { display: flex; gap: 10px; align-items: center;
  flex-wrap: wrap; font-size: 13px; margin: 6px 0; }
.mem-sec-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.bf-list { max-height: 150px; overflow-y: auto; margin: 4px 0 10px; }
.bf-item { display: block; font-size: 13px; padding: 3px 0; }
.bf-progress { height: 6px; background: var(--bg-subtle); border-radius: 3px;
  margin-top: 10px; overflow: hidden; }
.bf-bar { height: 100%; background: var(--brand); transition: width .3s; }
.bf-results { margin: 8px 0 0; padding-left: 18px; font-size: 12px;
  color: var(--ink-secondary); }
@media (max-width: 560px) {
  .mem-kpis { grid-template-columns: repeat(2, 1fr); }
}
.mf-ops { display: flex; gap: 8px; margin-top: 8px; }
.mem-wipe { border-color: #fca5a5; color: #b91c1c; font-size: 13px; }
.mem-stale { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px;
  padding: 10px 12px; font-size: 13px; color: #92400e; margin-bottom: 14px; }
code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px;
  word-break: break-all; font-size: 12px; }
@media (max-width: 420px) { .mem-page { padding: 14px 10px 40px; } }
</style>
