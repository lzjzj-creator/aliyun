<script setup>
// v3 #memory 页：长期记忆总览 + 事实确认/拒绝/删除 + 导出/清空（§9）
import { onMounted, ref } from "vue";
import {
  deleteAllMemory, deleteFact, disableMemory, enableMemory, exportMemory,
  extractFacts, getMemory, getOwnerToken, listFacts, patchFact,
  setOwnerToken,
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

const KIND_LABELS = {
  identity: "身份", education: "教育", experience: "经历",
  skill: "技能", metric: "成果", award: "奖项", goal: "意向",
};
const STATUS_LABELS = {
  proposed: "待确认", confirmed: "已确认",
  rejected: "已拒绝", superseded: "已取代",
};

// D6：v2 会话清理后 source_session_id 置空，证据 quote 保留但标「来源已过期」
function sourceExpired(fact) {
  const evs = fact.evidence || [];
  return evs.length > 0 && evs.every((e) => e.session_id == null);
}

async function boot() {
  booted.value = false;
  error.value = "";
  if (!getOwnerToken()) { enabled.value = false; booted.value = true; return; }
  try {
    status.value = await getMemory();
    enabled.value = true;
    await reload();
  } catch (e) {
    enabled.value = false;          // 404：令牌失效/未开启 → 回到引导
    if (e.status !== 404) error.value = "记忆服务暂时不可用";
  } finally {
    booted.value = true;
  }
}

async function onEnable() {
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
      <button class="mem-btn primary" :disabled="busy" @click="onEnable">
        开启长期记忆
      </button>
      <p v-if="error" class="mem-err">{{ error }}</p>
    </div>

    <template v-else>
      <!-- 一次性凭据展示 -->
      <div v-if="onboarding" class="mem-onboard">
        <p><b>请立即保存以下凭据，关闭后不再显示：</b></p>
        <p>访问令牌：<code>{{ onboarding.owner_token }}</code></p>
        <p v-if="onboarding.recovery_code">
          恢复码：<code>{{ onboarding.recovery_code }}</code>
        </p>
        <button class="mem-btn" @click="dismissOnboarding">我已保存</button>
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

      <!-- 筛选 + 事实列表 -->
      <div class="mem-filters">
        <button v-for="f in ['', 'proposed', 'confirmed', 'rejected']" :key="f"
                class="mem-chip" :class="{ on: filter === f }"
                @click="filter = f; reload()">
          {{ f ? STATUS_LABELS[f] : "全部" }}
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
.mf-ops { display: flex; gap: 8px; margin-top: 8px; }
.mem-wipe { border-color: #fca5a5; color: #b91c1c; font-size: 13px; }
code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px;
  word-break: break-all; font-size: 12px; }
@media (max-width: 420px) { .mem-page { padding: 14px 10px 40px; } }
</style>
