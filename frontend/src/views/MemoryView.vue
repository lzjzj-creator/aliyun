<script setup>
// v3 #memory 页：长期记忆总览 + 事实确认/拒绝/删除 + 导出/清空（§9）
import { computed, onMounted, ref } from "vue";
import { listSessions } from "../api/sessions";
import {
  deleteAllMemory, deleteFact, disableMemory, enableMemory, exportMemory,
  backfill, extractFacts, forgetSession, getMemory, getOwnerToken, listFacts,
  patchFact, setOwnerToken,
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
const sessionTitles = ref({});     // session_id -> v2 会话标题

function sessionTitle(sid) {
  return sessionTitles.value[sid] || `会话 ${String(sid).slice(0, 8)}…`;
}

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
  const s = new Set(picked.value);
  if (s.has(sid)) s.delete(sid); else s.add(sid);
  picked.value = s;
}

async function onBackfill() {
  const ids = [...picked.value];
  if (!ids.length) return;
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
  } catch {
    error.value = "回填中断，已完成部分已生效";
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
    enabled.value = true;
    // 会话标题映射用于事实卡片来源标注；拿不到（无 v2 会话）不阻塞
    listSessions().then((list) => {
      const map = {};
      for (const s of list || []) if (s && s.id) map[s.id] = s.title;
      sessionTitles.value = map;
      allSessions.value = list || [];
    }).catch(() => {});
    await reload();
  } catch (e) {
    enabled.value = false;          // 404：令牌失效/未开启 → 回到引导
    if (e.status !== 404) error.value = "记忆服务暂时不可用";
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

    <div v-if="!booted" class="mem-hint">加载中…</div>

    <!-- 未开启：引导而非空壳 -->
    <div v-else-if="!enabled" class="mem-empty">
      <p>长期记忆会在会话间记住你的经历、技能与成果，用于生成多份岗位简历。</p>
      <p class="mem-sub">默认关闭，需明示开启；事实可逐条查看、确认、拒绝或删除。</p>
      <button class="mem-btn primary" :disabled="busy"
              @click="showConsent = true">
        开启长期记忆
      </button>
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
      <!-- 一次性凭据展示 -->
      <div v-if="onboarding" class="mem-onboard">
        <p><b>请立即保存以下凭据，关闭后不再显示：</b></p>
        <p>访问令牌：<code>{{ onboarding.owner_token }}</code></p>
        <p v-if="onboarding.recovery_code">
          恢复码：<code>{{ onboarding.recovery_code }}</code>
        </p>
        <div class="mem-ops">
          <button class="mem-btn" @click="copyCredentials">复制</button>
          <button class="mem-btn" @click="downloadCredentials">下载保存</button>
          <button class="mem-btn" @click="dismissOnboarding">我已保存</button>
        </div>
      </div>

      <div v-if="status?.retention_warning" class="mem-stale">
        你的记忆已 {{ status.retention_days }} 天保留期临近到期（30 天内无活动将删除），
        建议先「导出 JSON」备份。
      </div>

      <!-- 状态卡 -->
      <div v-if="status" class="mem-card">
        <div class="mem-stats">
          <span>{{ status.fact_count }} 条事实</span>
          <span v-for="(n, s) in status.by_status" :key="s">
            {{ STATUS_LABELS[s] || s }} {{ n }}
          </span>
          <span v-if="status.last_extracted_at">
            最近抽取 {{ status.last_extracted_at.slice(0, 16).replace("T", " ") }}
          </span>
        </div>
        <div class="mem-ops">
          <button class="mem-btn" :disabled="busy" @click="onExport">导出 JSON</button>
          <button class="mem-btn" :disabled="busy" @click="onDisable">停用记忆</button>
          <button class="mem-btn danger" :disabled="busy"
                  @click="confirmingWipe = true">清空全部</button>
        </div>
      </div>

      <div v-if="confirmingWipe" class="mem-wipe">
        将永久删除全部事实与简历文档，不可恢复。
        <button class="mem-btn danger" :disabled="busy" @click="onWipe">确认清空</button>
        <button class="mem-btn" @click="confirmingWipe = false">取消</button>
      </div>

      <!-- 历史回填：勾选会话 → 逐块抽取 → 进度 + 结果 -->
      <div v-if="allSessions.length" class="mem-card">
        <div class="mem-sec-title">回填历史会话</div>
        <div class="bf-list">
          <label v-for="s in allSessions" :key="s.id" class="bf-item">
            <input type="checkbox" :checked="picked.has(s.id)"
                   :disabled="backfilling" @change="togglePick(s.id)" />
            {{ s.title || s.id.slice(0, 8) }}
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
            <span class="mf-slot">{{ f.slot_path }}</span>
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
.mem-title { font-size: 20px; margin: 0 0 16px; }
.mem-empty, .mem-card, .mem-onboard, .mem-wipe {
  background: var(--panel, #fff); border: 1px solid var(--line, #e5e7eb);
  border-radius: 10px; padding: 16px; margin-bottom: 14px;
}
.mem-sub { color: #6b7280; font-size: 13px; }
.mem-hint { color: #6b7280; }
.mem-err { color: #b91c1c; font-size: 13px; }
.mem-stats { display: flex; flex-wrap: wrap; gap: 10px; font-size: 13px;
  color: #374151; margin-bottom: 10px; }
.mem-ops { display: flex; gap: 8px; flex-wrap: wrap; }
.mem-btn {
  border: 1px solid #d1d5db; background: #fff; border-radius: 8px;
  padding: 7px 14px; font-size: 13px; cursor: pointer;
}
.mem-btn.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.mem-btn.danger { color: #b91c1c; border-color: #fca5a5; }
.mem-btn.sm { padding: 4px 10px; font-size: 12px; }
.mem-btn:disabled { opacity: 0.5; cursor: default; }
.mem-filters { display: flex; gap: 8px; margin-bottom: 12px; }
.mem-chip {
  border: 1px solid #d1d5db; border-radius: 999px; background: #fff;
  padding: 4px 12px; font-size: 12px; cursor: pointer;
}
.mem-chip.on { background: #eff6ff; border-color: #2563eb; color: #1d4ed8; }
.mem-facts { list-style: none; margin: 0; padding: 0; }
.mem-fact {
  border: 1px solid #e5e7eb; border-radius: 10px; background: #fff;
  padding: 12px 14px; margin-bottom: 10px;
}
.mf-head { display: flex; gap: 8px; align-items: center; font-size: 12px; }
.mf-kind { background: #eef2ff; color: #3730a3; border-radius: 4px;
  padding: 1px 6px; }
.mf-status[data-s="confirmed"] { color: #047857; }
.mf-status[data-s="proposed"] { color: #b45309; }
.mf-status[data-s="rejected"], .mf-status[data-s="superseded"] { color: #9ca3af; }
.mf-slot { color: #9ca3af; font-family: monospace; }
.mf-expired { color: #b45309; font-size: 11px; }
.mf-value { font-size: 14px; margin: 6px 0; }
.mf-quote { font-size: 12px; color: #6b7280; margin: 2px 0; }
.mf-src { display: block; font-size: 11px; color: #9ca3af; }
.mem-consent {
  position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 50;
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.mem-consent-box {
  background: #fff; border-radius: 12px; padding: 20px;
  max-width: 480px; width: 100%;
}
.mem-consent-box h3 { margin: 0 0 10px; font-size: 16px; }
.mem-consent-box ul { margin: 0 0 14px; padding-left: 20px; font-size: 13px;
  color: #374151; }
.mem-consent-box li { margin: 6px 0; }
.mem-session-row { display: flex; gap: 10px; align-items: center;
  font-size: 13px; margin: 6px 0; }
.mem-sec-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.bf-list { max-height: 150px; overflow-y: auto; margin: 4px 0 10px; }
.bf-item { display: block; font-size: 13px; padding: 3px 0; }
.bf-progress { height: 6px; background: #f3f4f6; border-radius: 3px;
  margin-top: 10px; overflow: hidden; }
.bf-bar { height: 100%; background: #2563eb; transition: width .3s; }
.bf-results { margin: 8px 0 0; padding-left: 18px; font-size: 12px;
  color: #6b7280; }
.mf-ops { display: flex; gap: 8px; margin-top: 8px; }
.mem-wipe { border-color: #fca5a5; color: #b91c1c; font-size: 13px; }
.mem-stale { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px;
  padding: 10px 12px; font-size: 13px; color: #92400e; margin-bottom: 14px; }
code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px;
  word-break: break-all; font-size: 12px; }
@media (max-width: 420px) { .mem-page { padding: 14px 10px 40px; } }
</style>
