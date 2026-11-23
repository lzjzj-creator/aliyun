<script setup>
// v2 会话侧栏（抽屉式，移动端全屏）。真实接口未就绪时（A 的 #3 上线前），
// 开发环境自动回落 demo/v2/sessions.json 契约快照。
import { computed, onMounted, reactive, ref } from "vue";
import { listSessions, deleteSession } from "../api/sessions";
import UiIcon from "./UiIcon.vue";

const emit = defineEmits(["select", "new", "close"]);
const props = defineProps({
  inline: { type: Boolean, default: false },   // true=桌面内嵌栏；false=固定抽屉
  activeId: { type: String, default: "" },     // 当前会话高亮
});

const STAGE_CN = { greeting: "招呼", probing: "访谈中", wrapping: "收尾", done: "已完成" };
// 按阶段分组：进行中（招呼/访谈/收尾）置顶，已完成靠后；组可折叠
const GROUPS = [
  { key: "active", label: "进行中", match: (s) => s.stage !== "done" },
  { key: "done", label: "已完成", match: (s) => s.stage === "done" },
];
const groupOpen = reactive({ active: true, done: true });
const groups = computed(() =>
  GROUPS.map((g) => ({ ...g, items: sessions.value.filter(g.match) }))
        .filter((g) => g.items.length));

const sessions = ref([]);
const loading = ref(true);
const errMsg = ref("");
const confirmId = ref("");
const busy = ref(false);
const demoMode = ref(false);

function fmtTime(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const diff = Date.now() - t.getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  return `${t.getMonth() + 1}/${t.getDate()}`;
}

async function load() {
  loading.value = true;
  errMsg.value = "";
  try {
    sessions.value = await listSessions();
    demoMode.value = false;
  } catch (e) {
    sessions.value = [];
    if (import.meta.env.DEV) {
      try {
        sessions.value = await fetch("/demo/v2/sessions.json").then((r) => r.json());
        demoMode.value = true;
      } catch {
        errMsg.value = "会话服务未就绪";
      }
    } else {
      errMsg.value = "会话服务未就绪";
    }
  } finally {
    loading.value = false;
  }
}

async function doDelete(id) {
  busy.value = true;
  try {
    if (!demoMode.value) await deleteSession(id);
    sessions.value = sessions.value.filter((s) => s.id !== id);
    // 只有删掉的是当前会话才跳回新访谈；删别的会话不打断用户
    if (id === props.activeId) emit("select", "");
  } catch (e) {
    if (e.status === 404) {
      // 已不存在：本地同步移除
      sessions.value = sessions.value.filter((s) => s.id !== id);
      } else {
      errMsg.value = "删除失败，请重试";
    }
  } finally {
    busy.value = false;
    confirmId.value = "";
  }
}

onMounted(load);
defineExpose({ reload: load });
</script>

<template>
  <aside :class="inline ? 'session-col' : 'session-drawer'">
    <header class="sd-head">
      <b>会话</b>
      <button v-if="!inline" class="sd-close" @click="emit('close')" aria-label="关闭">
        <UiIcon name="x" :size="18" />
      </button>
    </header>

    <button class="sd-new" @click="emit('new')">
      <UiIcon name="plus" :size="15" /> 新建访谈
    </button>

    <p v-if="loading" class="sd-hint">加载中…</p>
    <p v-else-if="errMsg" class="sd-hint">{{ errMsg }}</p>
    <p v-else-if="!sessions.length" class="sd-hint">还没有会话，开始一段访谈吧。</p>

    <div v-else class="sd-list">
      <div v-for="g in groups" :key="g.key" class="sd-grp">
        <button class="sd-grp-head" :class="{ open: groupOpen[g.key] }"
                :aria-expanded="groupOpen[g.key]"
                @click="groupOpen[g.key] = !groupOpen[g.key]">
          <UiIcon class="grp-chev" name="chevron-right" :size="12" />
          {{ g.label }}<span class="grp-count">{{ g.items.length }}</span>
        </button>
        <ul v-show="groupOpen[g.key]">
      <li v-for="s in g.items" :key="s.id" class="sd-item" :class="{ active: s.id === activeId }">
        <button class="sd-sess" @click="emit('select', s.id)">
          <span class="t">{{ s.title }}</span>
          <span class="meta">
            <i class="stage" :data-s="s.stage">{{ STAGE_CN[s.stage] || s.stage }}</i>
            {{ fmtTime(s.last_message_at) }}
          </span>
        </button>

        <div v-if="confirmId === s.id" class="sd-confirm">
          <span>确认删除？消息与画像将一并删除。</span>
          <div class="sd-confirm-btns">
            <button class="danger" :disabled="busy" @click="doDelete(s.id)">删除</button>
            <button @click="confirmId = ''">取消</button>
          </div>
        </div>
        <button v-else class="sd-trash" title="删除会话" aria-label="删除会话" @click="confirmId = s.id">
          <UiIcon name="trash" :size="15" />
        </button>
      </li>
        </ul>
      </div>
    </div>

    <p v-if="demoMode" class="sd-demo">示例数据（接口未接入）</p>
  </aside>
</template>

<style scoped>
.session-drawer {
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 30;
  width: 300px; max-width: 88vw; padding: 14px 12px;
  background: var(--bg-surface); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 10px;
  box-shadow: var(--shadow-md);
}
/* 桌面内嵌栏：占据工作台一柱，内部滚动 */
.session-col {
  display: flex; flex-direction: column; gap: 8px;
  height: 100%; min-height: 0; padding: 14px 10px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md);
}
.sd-head { display: flex; justify-content: space-between; align-items: center; padding: 0 4px; }
.sd-head b { color: var(--ink); font-size: .9rem; }
.sd-close {
  border: none; background: none; cursor: pointer; color: var(--ink-muted);
  width: 30px; height: 30px; border-radius: var(--radius-xs);
  display: flex; align-items: center; justify-content: center;
}
.sd-close:hover { background: var(--bg-hover); color: var(--ink); }
.sd-new {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  border: 1px solid var(--border); background: var(--bg-surface); color: var(--brand);
  border-radius: var(--radius-sm); padding: 8px 14px; cursor: pointer;
  font: inherit; font-size: 13.5px; font-weight: 500;
  transition: border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease);
}
.sd-new:hover { border-color: var(--brand); background: var(--brand-soft); }
.sd-hint { font-size: .82rem; color: var(--ink-muted); padding: 4px 6px; }
.sd-list {
  overflow-y: auto; flex: 1; min-height: 0;
  display: flex; flex-direction: column; gap: 2px;
}
.sd-list ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.sd-grp-head {
  display: flex; align-items: center; gap: 5px; width: 100%;
  padding: 7px 6px 4px; border: none; background: none; cursor: pointer;
  font: inherit; font-size: .72rem; color: var(--ink-muted); letter-spacing: .04em;
}
.sd-grp-head .grp-count { margin-left: auto; font-size: .68rem; color: var(--ink-faint, var(--ink-muted)); }
.sd-grp-head .grp-chev { transition: transform var(--dur-fast) var(--ease); }
.sd-grp-head.open .grp-chev { transform: rotate(90deg); }
.sd-grp-head:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; border-radius: 6px; }
.sd-item {
  position: relative; display: flex; align-items: flex-start; gap: 2px;
  padding: 8px 8px 8px 11px; border-radius: 10px;
  transition: background var(--dur-fast) var(--ease);
}
.sd-item:hover { background: var(--bg-subtle); }
.sd-item.active {
  background: var(--bg-subtle); box-shadow: var(--shadow-xs);
}
.sd-item.active .sd-sess .t { color: var(--brand); font-weight: 600; }
.sd-item.active::before {
  content: ""; position: absolute; left: 0; top: 18%; bottom: 18%;
  width: 3px; border-radius: 2px; background: var(--brand);
}
.sd-sess {
  flex: 1; min-width: 0; text-align: left; border: none; background: none;
  cursor: pointer; padding: 2px; font: inherit; color: var(--ink);
}
.sd-sess .t { display: block; font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-sess .meta { display: flex; align-items: center; gap: 6px; font-size: .72rem; color: var(--ink-muted); margin-top: 3px; }
.stage { font-style: normal; padding: 0 6px; border-radius: var(--radius-pill); background: var(--brand-soft); color: var(--brand); }
.stage[data-s="done"] { background: var(--agent-soft); color: var(--agent); }
.sd-trash {
  border: none; background: none; cursor: pointer; color: var(--ink-muted);
  width: 26px; height: 26px; border-radius: var(--radius-xs); padding: 0;
  display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: opacity var(--dur-fast) var(--ease);
}
.sd-item:hover .sd-trash, .sd-item:focus-within .sd-trash { opacity: 1; }
.sd-trash:hover { color: var(--danger); background: var(--danger-soft); }
@media (hover: none) { .sd-trash { opacity: .6; } }
.sd-confirm { font-size: .75rem; color: var(--danger); padding: 4px 6px; }
.sd-confirm-btns { display: flex; gap: 6px; margin-top: 4px; }
.sd-confirm-btns button {
  border: 1px solid var(--border); background: var(--bg-surface);
  border-radius: var(--radius-pill); padding: 3px 10px; font-size: .75rem; cursor: pointer;
  font-family: inherit; color: var(--ink);
}
.sd-confirm-btns .danger { border-color: var(--danger); background: var(--danger); color: #fff; }
.sd-demo { font-size: .7rem; color: var(--danger); text-align: center; }
@media (max-width: 640px) {
  .session-drawer { width: 100vw; max-width: 100vw; border-right: none; }
}
</style>
