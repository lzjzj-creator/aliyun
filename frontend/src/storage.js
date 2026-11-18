// v2 本地存储：anon_id、会话令牌、简历草稿（不落服务端，§3/§11）
// 令牌只存本机 localStorage，不进 URL、不写日志。

const ANON_KEY = "zm_anon_id";
const TOKENS_KEY = "zm_session_tokens";
const CURRENT_KEY = "zm_current_session";
const SCHEMA_VERSION = 1;

export const DRAFT_KEY = "resume_draft_v1";
export const OPT_DRAFT_KEY = "resume_draft_optimized";

function readJSON(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 存储满了等异常静默降级：不阻塞主流程 */
  }
}

export function getAnonId() {
  let id = localStorage.getItem(ANON_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(ANON_KEY, id);
  }
  return id;
}

// ---- 会话令牌：{session_id: token} ----
export function getSessionToken(sessionId) {
  return readJSON(TOKENS_KEY, {})[sessionId] || "";
}

export function setSessionToken(sessionId, token) {
  const map = readJSON(TOKENS_KEY, {});
  map[sessionId] = token;
  writeJSON(TOKENS_KEY, map);
}

export function removeSessionToken(sessionId) {
  const map = readJSON(TOKENS_KEY, {});
  delete map[sessionId];
  writeJSON(TOKENS_KEY, map);
}

export function getCurrentSessionId() {
  return localStorage.getItem(CURRENT_KEY) || "";
}

export function setCurrentSessionId(id) {
  if (id) localStorage.setItem(CURRENT_KEY, id);
  else localStorage.removeItem(CURRENT_KEY);
}

// ---- 简历草稿：带 schema_version + template_version ----
// 结构 {schema_version, template_version, fields, evidence_map, gaps, saved_at}
export function saveDraft(key, { template_version = "", fields = {}, evidence_map = {}, gaps = [] } = {}) {
  writeJSON(key, {
    schema_version: SCHEMA_VERSION,
    template_version,
    fields,
    evidence_map,
    gaps,
    saved_at: new Date().toISOString(),
  });
}

export function loadDraft(key) {
  const d = readJSON(key);
  if (!d || d.schema_version !== SCHEMA_VERSION) return null;
  return d;
}

export function clearDraft(key) {
  localStorage.removeItem(key);
}
