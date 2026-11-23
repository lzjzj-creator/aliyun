<script setup>
// v5 #memory 页：记忆总览 + 事实确认/拒绝/删除 + 随手记（分类+对话） + 导出/清空
// 归属 = Bearer user_id：记忆随账号走，不再需要 owner_token/恢复码/会话凭据。
import { computed, onMounted, ref } from "vue";
import { listSessions } from "../api/sessions";
import {
  deleteAllMemory, deleteFact, dialogFact, disableMemory, enableMemory,
  exportMemory, backfill, createFacts, forgetSession, getMemory,
  listFacts, patchFact,
} from "../api/v3";
import { getDraft, putDraft } from "../api/auth";

const enabled = ref(false);
const booted = ref(false);
const status = ref(null);
const facts = ref([]);
const nextCursor = ref(null);
const filter = ref("");            // "" | proposed | confirmed | rejected
const kindFilter = ref("");
const busy = ref(false);
const error = ref("");
const confirmingWipe = ref(false);
const disabledMemory = ref(false);
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

// 事实展示模式：rows=行式分区（默认） | cards=证据卡（旧版）
const viewMode = ref("rows");
const openFacts = ref(new Set());   // 行式模式下展开证据的行 id
function toggleFactRow(id) {
  const s = new Set(openFacts.value);
  if (s.has(id)) s.delete(id); else s.add(id);
  openFacts.value = s;
}

// 行式分区：按槽位根段归类到简历素材板块（未知槽位归入"其他"）
const ROW_SECTIONS = [
  { key: "basics", label: "基本信息", roots: ["basics", "basic", "name", "school", "role", "objective", "goal", "goals"] },
  { key: "campus", label: "校内经历", roots: ["campus", "education", "student_work", "classwork"] },
  { key: "outside", label: "校外经历", roots: ["internships", "internship", "projects", "project", "volunteer", "experience", "experiences"] },
  { key: "skills", label: "技能清单", roots: ["skills", "skill_groups", "skill"] },
  { key: "awards", label: "奖项荣誉", roots: ["awards", "award", "publications", "honors"] },
  { key: "other", label: "其他", roots: ["summary"] },
];
const factSections = computed(() => {
  const groups = ROW_SECTIONS.map((s) => ({ ...s, items: [] }));
  const fallback = groups[groups.length - 1];
  for (const f of facts.value) {
    const root = String(f.slot_path || "").split(".")[0];
    (groups.find((s) => s.roots.includes(root)) || fallback).items.push(f);
  }
  return groups.filter((g) => g.items.length);
});

const KIND_LABELS = {
  identity: "身份", education: "教育", experience: "经历",
  skill: "技能", metric: "成果", award: "奖项", goal: "意向",
};
const STATUS_LABELS = {
  proposed: "待确认", confirmed: "已确认",
  rejected: "已拒绝", superseded: "已取代",
};

// 来源已删：证据被清空（忘掉会话）或证据会话指针全部置空（D6 清理）；
// 随手记手动证据（source_type=manual）不受会话删除影响
function sourceExpired(fact) {
  const evs = fact.evidence || [];
  return evs.length === 0 ||
    evs.every((e) => e.session_id == null && e.source_type !== "manual");
}

// ---- 随手记（v5：先选分类再写，可攒多条一次提交；草稿存服务端） ----
// 随手记分类（v5 §4 规格：8 类 chip）——个人情况/意向走初始化与访谈，不进随手记。
const NOTE_CATEGORIES = [
  { key: "project", label: "项目经历", hint: "项目名、你的角色、时间、做了什么、结果",
    kind: "experience", slot: "projects.项目" },
  { key: "intern", label: "实习经历", hint: "公司、岗位、时间、工作内容、产出",
    kind: "experience", slot: "internships.实习" },
  { key: "campus", label: "校园经历", hint: "部门或社团、职务、时间、做了什么、成果",
    kind: "experience", slot: "campus.校园" },
  { key: "competition", label: "竞赛获奖", hint: "赛事名、奖项、时间、你的角色",
    kind: "award", slot: "awards.竞赛" },
  { key: "cert", label: "证书", hint: "英语、计算机、专业证书、奖学金…",
    kind: "award", slot: "awards.证书" },
  { key: "skill", label: "技能", hint: "专业技能、软件工具、语言能力、通用能力",
    kind: "skill", slot: "skills.notes" },
  { key: "self_eval", label: "自我评价", hint: "你的特点、优势、想突出的方面…",
    kind: "metric", slot: "summary.notes" },
  { key: "other", label: "其他", hint: "志愿、支教、访学、作品链接、活动照片说明…",
    kind: "experience", slot: "experience.其他" },
];
const catByKey = (k) => NOTE_CATEGORIES.find((c) => c.key === k) || NOTE_CATEGORIES[0];

const notesOpen = ref(false);
const noteCat = ref("project");
const noteText = ref("");
const pendingNotes = ref([]);      // [{cat, text}]
const noteMsg = ref("");
const noteBusy = ref(false);
const noteDraftId = "notes";

// 服务端草稿：pendingNotes + 当前输入，跨设备不丢
let noteDraftTimer = null;
function saveNoteDraft() {
  clearTimeout(noteDraftTimer);
  noteDraftTimer = setTimeout(() => {
    putDraft({
      draft_id: noteDraftId, template_version: "notes", title: "随手记草稿",
      fields: { cat: noteCat.value, text: noteText.value, pending: pendingNotes.value },
    }).catch(() => { /* 草稿保存失败不打扰 */ });
  }, 600);
}

async function loadNoteDraft() {
  try {
    const { draft } = await getDraft(noteDraftId);
    const f = draft?.fields || {};
    if (f.cat) noteCat.value = f.cat;
    if (typeof f.text === "string") noteText.value = f.text;
    if (Array.isArray(f.pending)) pendingNotes.value = f.pending;
  } catch { /* 无草稿即空 */ }
}

function addNote() {
  const text = noteText.value.trim();
  if (!text) return;
  pendingNotes.value.push({ cat: noteCat.value, text });
  noteText.value = "";
  saveNoteDraft();
}

function removePending(i) {
  pendingNotes.value.splice(i, 1);
  saveNoteDraft();
}

async function submitNotes() {
  const items = [...pendingNotes.value];
  if (noteText.value.trim()) items.push({ cat: noteCat.value, text: noteText.value.trim() });
  if (!items.length) { noteMsg.value = "先写点什么再提交，填多少算多少。"; return; }
  noteBusy.value = true;
  noteMsg.value = "";
  try {
    const res = await createFacts(items.map((n) => {
      const c = catByKey(n.cat);
      return { kind: c.kind, slot_path: c.slot,
               value: n.text.slice(0, 480), quote: n.text.slice(0, 2000) };
    }));
    pendingNotes.value = [];
    noteText.value = "";
    saveNoteDraft();
    noteMsg.value = `已存入 ${res.created} 条新事实` +
      (res.merged ? `，${res.merged} 条与已有事实合并` : "") + "，下方列表可见。";
    await reload();
    status.value = await getMemory();
  } catch (e) {
    noteMsg.value = e.status === 429 ? "提交太频繁，请稍后再试。"
      : "保存失败，请稍后重试；草稿已保留在云端。";
  } finally {
    noteBusy.value = false;
  }
}

// ---- 边聊边记（R3）：AI 对话起草 → 待确认 → 确认落库 ----
const dialogOpen = ref(false);
const dialogMsgs = ref([]);        // [{role:'user'|'assistant', text, drafts?}]
const dialogInput = ref("");
const dialogBusy = ref(false);
let dialogKey = 0;

async function sendDialog() {
  const text = dialogInput.value.trim();
  if (!text || dialogBusy.value) return;
  dialogInput.value = "";
  dialogMsgs.value.push({ role: "user", text, _k: ++dialogKey });
  dialogBusy.value = true;
  try {
    const res = await dialogFact({
      message: text,
      history_tail: dialogMsgs.value.slice(-8)
        .map((m) => ({ role: m.role, content: m.text })),
    });
    dialogMsgs.value.push({
      role: "assistant", _k: ++dialogKey,
      text: res.reply || "我整理了一下，看看这几条对不对：",
      drafts: res.draft_facts || [],
    });
  } catch (e) {
    dialogMsgs.value.push({
      role: "assistant", _k: ++dialogKey,
      text: e.status === 429 ? "今天的对话条数用完了，明天再聊。"
          : "整理失败了，换种说法再试试。",
      drafts: [],
    });
  } finally {
    dialogBusy.value = false;
  }
}

async function confirmDialogFact(msg, draft, i) {
  busy.value = true;
  try {
    await createFacts([{ kind: draft.kind, slot_path: draft.slot_path,
                         value: draft.value, quote: draft.quote }]);
    msg.drafts.splice(i, 1);
    status.value = await getMemory();
    await reload();
  } catch {
    error.value = "保存失败，请重试";
  } finally {
    busy.value = false;
  }
}

function dismissDialogFact(msg, i) {
  msg.drafts.splice(i, 1);
}

// ---- 历史回填：会话多选 + 分块顺序调用 + 进度 ----
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
  error.value = "";
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

// 事实证据里出现过的会话列表（来源级删除入口）
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
  try {
    try {
      status.value = await getMemory();
    } catch (e) {
      // Bearer 下 owner 自动供给；仍 404 说明从未初始化 → 开通一次
      if (e.status === 404) {
        await enableMemory();
        status.value = await getMemory();
      } else throw e;
    }
    enabled.value = status.value.enabled === true;
    disabledMemory.value = status.value.enabled === false;
    if (!enabled.value) { facts.value = []; return; }
    listSessions().then((list) => {
      const map = {};
      for (const s of list || []) if (s && s.id) map[s.id] = s.title;
      sessionTitles.value = map;
      allSessions.value = list || [];
    }).catch(() => {});
    loadNoteDraft();
    await reload();
  } catch (e) {
    enabled.value = false;
    facts.value = [];
    status.value = null;
    error.value = "记忆服务暂时不可用，请稍后重试";
  } finally {
    booted.value = true;
  }
}

async function onEnable() {
  busy.value = true;
  try {
    await enableMemory();
    await boot();
  } catch {
    error.value = "开启失败，请稍后重试";
  } finally {
    busy.value = false;
  }
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
    <h2 class="mem-title">经历证据库</h2>

    <div v-if="!booted" class="mem-hint">加载中…</div>

    <!-- 停用状态：一键重新开启（数据都在，记忆随账号走） -->
    <div v-else-if="!enabled" class="mem-empty">
      <p>经历证据库会跨会话记住你的经历、技能与成果，用于生成多份岗位简历。</p>
      <p class="mem-sub">数据存在你的云端账号里，每条事实都可查看、确认、拒绝或删除。</p>
      <button class="mem-btn primary" :disabled="busy" @click="onEnable">
        {{ disabledMemory ? "重新开启" : "开启经历证据库" }}
      </button>
      <p v-if="error" class="mem-err">{{ error }}</p>
    </div>

    <template v-else>
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

      <!-- 随手记（v5）：先选分类再写，攒多条一次提交；草稿存云端 -->
      <div class="mem-card notes-card">
        <button class="notes-head" :aria-expanded="notesOpen" @click="notesOpen = !notesOpen">
          <span>
            <b>随手记</b>
            <span class="notes-sub">选个分类记一笔，攒几条一起存</span>
          </span>
          <span class="notes-count" v-if="pendingNotes.length">{{ pendingNotes.length }} 条待提交</span>
          <span class="notes-arrow" :class="{ open: notesOpen }">›</span>
        </button>
        <div v-if="notesOpen" class="notes-body">
          <div class="note-cats" role="radiogroup" aria-label="随手记分类">
            <button v-for="c in NOTE_CATEGORIES" :key="c.key" type="button"
                    class="mem-chip note-chip" :class="{ on: noteCat === c.key }"
                    :disabled="noteBusy" role="radio" :aria-checked="noteCat === c.key"
                    @click="noteCat = c.key; saveNoteDraft()">{{ c.label }}</button>
          </div>
          <div class="note-row">
            <textarea v-model="noteText" rows="2" class="note-text" :disabled="noteBusy"
                      :placeholder="catByKey(noteCat).hint" @input="saveNoteDraft" />
            <button class="mem-btn" :disabled="!noteText.trim()" @click="addNote">加入</button>
          </div>
          <ul v-if="pendingNotes.length" class="note-pending">
            <li v-for="(n, i) in pendingNotes" :key="i">
              <span class="note-pending-cat">{{ catByKey(n.cat).label }}</span>
              <span class="note-pending-text">{{ n.text }}</span>
              <button class="note-x" aria-label="移除" @click="removePending(i)">×</button>
            </li>
          </ul>
          <div class="mem-ops notes-ops">
            <button class="mem-btn primary" :disabled="noteBusy || (!pendingNotes.length && !noteText.trim())"
                    @click="submitNotes">
              {{ noteBusy ? "提交中…" : `提交到记忆（${pendingNotes.length + (noteText.trim() ? 1 : 0)} 条）` }}
            </button>
            <span class="mem-sub">草稿自动存云端；提交后存为「已确认」事实，可随时改删。</span>
          </div>
          <p v-if="noteMsg" class="notes-msg">{{ noteMsg }}</p>
        </div>
      </div>

      <!-- 边聊边记（R3）：对话起草 → 待确认队列 → 确认落库 -->
      <div class="mem-card notes-card">
        <button class="notes-head" :aria-expanded="dialogOpen" @click="dialogOpen = !dialogOpen">
          <span>
            <b>边聊边记</b>
            <span class="notes-sub">直接说出来，我帮你整理成随手记，你确认后入库</span>
          </span>
          <span class="notes-arrow" :class="{ open: dialogOpen }">›</span>
        </button>
        <div v-if="dialogOpen" class="notes-body">
          <div class="dlg-stream">
            <div v-for="m in dialogMsgs" :key="m._k" :class="['dlg-msg', m.role]">
              <p>{{ m.text }}</p>
              <ul v-if="m.drafts?.length" class="dlg-drafts">
                <li v-for="(d, i) in m.drafts" :key="i">
                  <span class="note-pending-cat">{{ slotLabel(d.slot_path) }}</span>
                  <span class="dlg-draft-text">{{ d.value }}</span>
                  <span class="dlg-draft-ops">
                    <button class="mem-btn sm" :disabled="busy"
                            @click="confirmDialogFact(m, d, i)">存入</button>
                    <button class="mem-btn sm" @click="dismissDialogFact(m, i)">不要</button>
                  </span>
                </li>
              </ul>
            </div>
            <p v-if="dialogBusy" class="mem-sub">整理中…</p>
          </div>
          <form class="dlg-form" @submit.prevent="sendDialog">
            <input v-model="dialogInput" :disabled="dialogBusy"
                   placeholder="比如：我大二在字节做过三个月后端实习，用 Go 写了订单服务…" />
            <button class="mem-btn primary" type="submit" :disabled="dialogBusy || !dialogInput.trim()">发送</button>
          </form>
          <p class="mem-sub">我只产出草稿，点「存入」才进证据库。</p>
        </div>
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

      <!-- 筛选 + 展示模式切换 + 事实列表 -->
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
        <span class="mem-mode">
          <button class="mem-chip" :class="{ on: viewMode === 'rows' }" @click="viewMode = 'rows'">行式</button>
          <button class="mem-chip" :class="{ on: viewMode === 'cards' }" @click="viewMode = 'cards'">卡片</button>
        </span>
      </div>

      <!-- 行式模式：按简历素材板块分区，一行一条事实，点行展开证据 -->
      <template v-if="viewMode === 'rows'">
        <section v-for="sec in factSections" :key="sec.key" class="ms-section">
          <div class="ms-sec-head">
            <b>{{ sec.label }}</b><span>{{ sec.items.length }} 条</span>
          </div>
          <ul class="ms-rows">
            <li v-for="f in sec.items" :key="f.id" class="ms-row"
                :class="{ open: openFacts.has(f.id), proposed: f.status === 'proposed' }">
              <div class="ms-line" role="button" tabindex="0"
                   @click="toggleFactRow(f.id)" @keydown.enter="toggleFactRow(f.id)">
                <span class="ms-kind">{{ KIND_LABELS[f.kind] || f.kind }}</span>
                <span class="ms-value">{{ typeof f.value === 'string' ? f.value : JSON.stringify(f.value) }}</span>
                <span class="ms-slot">{{ slotLabel(f.slot_path) }}</span>
                <span class="ms-ev">{{ (f.evidence || []).length }} 证据</span>
                <span class="mf-status" :data-s="f.status">{{ STATUS_LABELS[f.status] || f.status }}</span>
                <span v-if="sourceExpired(f)" class="ms-exp">来源已删</span>
                <span class="ms-ops" @click.stop>
                  <button v-if="f.status === 'proposed'" class="mem-btn sm"
                          :disabled="busy" @click="act(f, 'confirmed')">确认</button>
                  <button v-if="f.status === 'proposed'" class="mem-btn sm"
                          :disabled="busy" @click="act(f, 'rejected')">拒绝</button>
                  <button class="mem-btn sm danger" :disabled="busy"
                          @click="act(f, 'delete')">删除</button>
                </span>
              </div>
              <div v-if="openFacts.has(f.id)" class="ms-quotes">
                <div v-for="(e, i) in f.evidence" :key="i" class="mf-quote">
                  <span v-if="e.source_type === 'manual'" class="mf-src">随手记</span>
                  <span v-else-if="e.session_id" class="mf-src">
                    {{ sessionTitle(e.session_id) }}<template v-if="e.msg_id != null"> · 第{{ e.msg_id }}条</template>
                  </span>
                  “{{ e.quote }}”
                </div>
                <p v-if="!(f.evidence || []).length" class="mem-sub">无证据引用</p>
              </div>
            </li>
          </ul>
        </section>
      </template>

      <ul v-else class="mem-facts">
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
            <span v-if="e.source_type === 'manual'" class="mf-src">随手记</span>
            <span v-else-if="e.session_id" class="mf-src">
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
.mem-empty, .mem-card, .mem-wipe {
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

/* 随手记/边聊边记卡片 */
.notes-card { padding: 0; overflow: hidden; }
.notes-head {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 12px 16px; border: none; background: none; cursor: pointer;
  font-family: inherit; text-align: left;
}
.notes-head:hover { background: var(--bg-hover); }
.notes-head b { font-size: .9rem; font-weight: 650; }
.notes-sub { font-size: .75rem; color: var(--ink-muted); margin-left: 8px; }
.notes-count {
  margin-left: auto; flex: none; font-size: .7rem; color: var(--coach);
  background: var(--coach-soft); border-radius: var(--radius-pill); padding: 2px 8px;
}
.notes-arrow {
  flex: none; font-size: 18px; color: var(--ink-muted); line-height: 1;
  transform: rotate(90deg); transition: transform var(--dur-fast) var(--ease);
  margin-left: auto;
}
.notes-count + .notes-arrow { margin-left: 0; }
.notes-arrow.open { transform: rotate(-90deg); }
.notes-body { border-top: 1px solid var(--border-subtle); padding: 12px 16px 16px; }
.note-cats { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.note-chip { font-size: .78rem; padding: 4px 10px; }
.note-row { display: flex; gap: 8px; align-items: stretch; }
.note-text {
  flex: 1; font-family: inherit; font-size: .84rem; color: var(--ink); resize: vertical;
  border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px;
  background: var(--bg-surface); min-height: 40px; line-height: 1.5;
}
.note-text:focus { outline: none; border-color: var(--brand-border); }
.note-text::placeholder { color: var(--ink-muted); font-size: .8rem; }
.note-pending { list-style: none; margin: 10px 0; padding: 0; }
.note-pending li {
  display: flex; align-items: baseline; gap: 8px; padding: 6px 8px;
  border: 1px dashed var(--border-subtle); border-radius: 8px; margin-bottom: 6px;
  font-size: .82rem;
}
.note-pending-cat {
  flex: none; font-size: .68rem; color: var(--brand);
  background: var(--brand-soft); border-radius: 4px; padding: 1px 6px;
}
.note-pending-text { flex: 1; min-width: 0; color: var(--ink); }
.note-x {
  flex: none; border: none; background: none; color: var(--ink-muted);
  cursor: pointer; font-size: 14px; padding: 0 4px;
}
.note-x:hover { color: var(--danger); }
.notes-ops { align-items: center; }
.notes-msg { font-size: .8rem; color: var(--agent); margin: 8px 0 0; }

/* 边聊边记 */
.dlg-stream { max-height: 320px; overflow-y: auto; margin-bottom: 10px; }
.dlg-msg { margin-bottom: 10px; font-size: .84rem; }
.dlg-msg p { margin: 0; padding: 8px 12px; border-radius: 10px; display: inline-block; max-width: 92%; }
.dlg-msg.user { text-align: right; }
.dlg-msg.user p { background: var(--brand-soft); color: var(--ink); }
.dlg-msg.assistant p { background: var(--bg-subtle); color: var(--ink); }
.dlg-drafts { list-style: none; margin: 6px 0 0; padding: 0; }
.dlg-drafts li {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  border: 1px dashed var(--border-subtle); border-radius: 8px;
  padding: 6px 10px; margin-top: 6px; font-size: .8rem;
}
.dlg-draft-text { flex: 1; min-width: 0; color: var(--ink); }
.dlg-draft-ops { flex: none; display: flex; gap: 4px; }
.dlg-form { display: flex; gap: 8px; }
.dlg-form input {
  flex: 1; font: inherit; font-size: .84rem; padding: 8px 12px;
  border: 1px solid var(--border); border-radius: 10px;
  background: var(--bg-surface); color: var(--ink);
}
.dlg-form input:focus { outline: none; border-color: var(--brand-border); }

.mem-filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
.mem-mode { margin-left: auto; display: flex; gap: 4px; }
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
  background: var(--agent-soft); border: 1px solid var(--agent-border);
  border-radius: 8px; padding: 7px 10px;
}
.mf-src { display: block; font-size: 11px; color: var(--agent); margin-bottom: 2px; }

/* 行式模式：分区 + 紧凑行 */
.ms-section {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md);
  margin-bottom: 12px; overflow: hidden; box-shadow: var(--shadow-xs);
}
.ms-sec-head {
  display: flex; align-items: baseline; gap: 8px; padding: 10px 14px;
  background: var(--bg-subtle); border-bottom: 1px solid var(--border-subtle);
}
.ms-sec-head b { font-size: .85rem; font-weight: 650; }
.ms-sec-head span { font-size: .72rem; color: var(--ink-muted); }
.ms-rows { list-style: none; margin: 0; padding: 0; }
.ms-row { border-bottom: 1px solid var(--border-subtle); }
.ms-row:last-child { border-bottom: none; }
.ms-row.proposed { background: linear-gradient(90deg, var(--coach-soft), transparent 40%); }
.ms-line {
  display: flex; align-items: center; gap: 10px; padding: 9px 14px;
  font-size: .84rem; cursor: pointer; user-select: none;
}
.ms-line:hover { background: var(--bg-hover); }
.ms-kind {
  flex: none; width: 44px; text-align: center; font-size: .68rem;
  background: var(--brand-soft); color: var(--brand); border-radius: 4px; padding: 1px 4px;
}
.ms-value {
  flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--ink);
}
.ms-slot { flex: none; font-size: .72rem; color: var(--ink-muted); }
.ms-ev { flex: none; font-size: .7rem; color: var(--agent); background: var(--agent-soft); border-radius: var(--radius-pill); padding: 1px 8px; }
.ms-exp { flex: none; font-size: .68rem; color: var(--coach); }
.ms-ops { flex: none; display: flex; gap: 4px; }
.ms-quotes { padding: 2px 14px 12px 68px; border-top: 1px dashed var(--border-subtle); }
@media (max-width: 640px) {
  .ms-line { flex-wrap: wrap; row-gap: 4px; }
  .ms-value { flex-basis: 100%; order: -1; }
  .ms-quotes { padding-left: 14px; }
}
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
  .note-row { flex-direction: column; }
}
.mf-ops { display: flex; gap: 8px; margin-top: 8px; }
.mem-wipe { border-color: #fca5a5; color: #b91c1c; font-size: 13px; }
code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px;
  word-break: break-all; font-size: 12px; }
@media (max-width: 420px) { .mem-page { padding: 14px 10px 40px; } }
</style>
