<script setup>
// v2 会话侧栏（抽屉式，移动端全屏）。真实接口未就绪时（A 的 #3 上线前），
// 开发环境自动回落 demo/v2/sessions.json 契约快照。
import { onMounted, ref } from "vue";
import { listSessions, deleteSession } from "../api/sessions";
import { removeSessionToken } from "../storage";

const emit = defineEmits(["select", "new", "close"]);

const STAGE_CN = { greeting: "招呼", probing: "访谈中", wrapping: "收尾", done: "已完成" };

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
    removeSessionToken(id);
    emit("select", "");
  } catch (e) {
    if (e.status === 404) {
      // 已不存在：本地同步移除
      sessions.value = sessions.value.filter((s) => s.id !== id);
      removeSessionToken(id);
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
  <aside class="session-drawer">
    <header class="sd-head">
      <b>会话</b>
      <button class="sd-close" @click="emit('close')" aria-label="关闭">×</button>
    </header>

    <button class="sd-new" @click="emit('new')">＋ 新建会话</button>

    <p v-if="loading" class="sd-hint">加载中…</p>
    <p v-else-if="errMsg" class="sd-hint">{{ errMsg }}</p>
    <p v-else-if="!sessions.length" class="sd-hint">还没有会话，开始一段访谈吧。</p>

    <ul v-else class="sd-list">
      <li v-for="s in sessions" :key="s.id" class="sd-item">
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
        <button v-else class="sd-trash" title="删除会话" @click="confirmId = s.id">🗑</button>
      </li>
    </ul>

    <p v-if="demoMode" class="sd-demo">示例数据（接口未接入）</p>
  </aside>
</template>

<style scoped>
.session-drawer {
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 30;
  width: 300px; max-width: 88vw; padding: 14px 12px;
  background: var(--paper); border-right: 1px solid var(--line);
  display: flex; flex-direction: column; gap: 10px;
  box-shadow: 4px 0 24px rgba(31, 42, 46, .08);
}
.sd-head { display: flex; justify-content: space-between; align-items: center; }
.sd-head b { color: var(--teal); }
.sd-close { border: none; background: none; font-size: 1.3rem; cursor: pointer; color: #7a6f63; }
.sd-new {
  border: 1px solid var(--teal); background: var(--teal); color: #fff;
  border-radius: 999px; padding: 9px 14px; cursor: pointer; font-size: .9rem;
}
.sd-hint { font-size: .82rem; color: #9a9186; padding: 4px 2px; }
.sd-list { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; }
.sd-item {
  display: flex; align-items: flex-start; gap: 4px;
  padding: 8px 6px; border-radius: 10px; border-bottom: 1px dashed var(--line);
}
.sd-item:hover { background: #f3eee6; }
.sd-sess {
  flex: 1; min-width: 0; text-align: left; border: none; background: none;
  cursor: pointer; padding: 2px; font: inherit; color: var(--ink);
}
.sd-sess .t { display: block; font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-sess .meta { display: flex; align-items: center; gap: 6px; font-size: .72rem; color: #9a9186; margin-top: 3px; }
.stage { font-style: normal; padding: 0 6px; border-radius: 999px; background: rgba(20,82,90,.08); color: var(--teal); }
.stage[data-s="done"] { background: rgba(14,138,22,.10); color: #0e8a16; }
.sd-trash { border: none; background: none; cursor: pointer; font-size: .85rem; opacity: .5; padding: 4px; }
.sd-trash:hover { opacity: 1; }
.sd-confirm { font-size: .75rem; color: #b3402a; padding: 4px 6px; }
.sd-confirm-btns { display: flex; gap: 6px; margin-top: 4px; }
.sd-confirm-btns button { border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 3px 10px; font-size: .75rem; cursor: pointer; }
.sd-confirm-btns .danger { border-color: #b3402a; background: #b3402a; color: #fff; }
.sd-demo { font-size: .7rem; color: #b3402a; text-align: center; }
@media (max-width: 640px) {
  .session-drawer { width: 100vw; max-width: 100vw; border-right: none; }
}
</style>
