// v3 长期记忆 + 岗位简历库 API（v5：Bearer 统一鉴权）
// owner 资源由 Authorization: Bearer 鉴权，服务端按 user_id 解析归属；
// 注册/guest 共用一把 token，记忆默认可用、无需恢复码。
import { API_BASE, authHeaders } from "./core";

async function req3(path, { method = "GET", body, headers, responseType, sessionId } = {}) {
  const h = authHeaders(sessionId, headers);
  if (body !== undefined) h["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.ok && responseType === "blob") {
    if (!res.headers.get("Content-Type")?.toLowerCase().startsWith("application/pdf")) {
      throw new Error("导出服务未返回 PDF，请重试");
    }
    return res.blob();
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(
      (typeof data.detail === "string" && data.detail) ||
      data.detail?.message || data.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.code = data.detail?.code;
    err.issues = data.detail?.issues;
    err.data = data;
    throw err;
  }
  return data;
}

// --- memory lifecycle -------------------------------------------------------

export const enableMemory = () =>
  req3("/api/v3/memory/enable", { method: "POST" });

export const getMemory = () => req3("/api/v3/memory");

export const disableMemory = () =>
  req3("/api/v3/memory/disable", { method: "POST" });

export const deleteAllMemory = () =>
  req3("/api/v3/memory", { method: "DELETE", body: { confirm: "DELETE_ALL" } });

// JSON 导出：fetch 携带令牌拿 blob 再触发下载（不能裸链，令牌不出现在 URL）
export async function exportMemory() {
  const res = await fetch(`${API_BASE}/api/v3/memory/export`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.blob();
}

// --- facts ------------------------------------------------------------------

export const listFacts = ({ kind, status, cursor, limit = 50 } = {}) => {
  const params = new URLSearchParams();
  if (kind) params.set("kind", kind);
  if (status) params.set("status", status);
  if (cursor) params.set("cursor", cursor);
  params.set("limit", String(limit));
  return req3(`/api/v3/facts?${params.toString()}`);
};

export const createFacts = (facts) =>
  req3("/api/v3/facts", { method: "POST", body: { facts } });

export const patchFact = (id, body) =>
  req3(`/api/v3/facts/${id}`, { method: "PATCH", body });

export const deleteFact = (id) =>
  req3(`/api/v3/facts/${id}`, { method: "DELETE" });

// 会话抽取/回填：Bearer 下服务端按 user_id 校验归属，不再需要会话令牌。
export const extractFacts = (sessionId) =>
  req3("/api/v3/facts/extract", {
    method: "POST", body: { session_id: sessionId }, sessionId,
  });

// 对话建随手记（R3）：{message, history_tail?} → {reply, draft_facts[]}
export const dialogFact = (body) =>
  req3("/api/v3/facts/dialog", { method: "POST", body });

export const backfill = (sessionIds) =>
  req3("/api/v3/memory/backfill", {
    method: "POST", body: { session_ids: [...new Set(sessionIds || [])] },
  });

export const forgetSession = (sessionId) =>
  req3(`/api/v3/memory/sessions/${sessionId}`, { method: "DELETE" });

// --- resume library ---------------------------------------------------------

export const listResumes = () => req3("/api/v3/resumes");

export const exportResumePdf = (id, { version, layout = "classic" }) =>
  req3(`/api/v3/resumes/${encodeURIComponent(id)}/pdf`, {
    method: "POST", body: { version, layout }, responseType: "blob",
    headers: { Accept: "application/pdf" },
  });

export const createResume = (body) =>
  req3("/api/v3/resumes", { method: "POST", body });

export const getResume = (id) => req3(`/api/v3/resumes/${id}`);

export const patchResume = (id, body, version) =>
  req3(`/api/v3/resumes/${id}`, {
    method: "PATCH", body, headers: { "If-Match": String(version) },
  });

export const duplicateResume = (id) =>
  req3(`/api/v3/resumes/${id}/duplicate`, { method: "POST" });

export const deleteResume = (id) =>
  req3(`/api/v3/resumes/${id}`, { method: "DELETE" });

export const regenerateResume = (id, body = {}) =>
  req3(`/api/v3/resumes/${id}/regenerate`, { method: "POST", body });

export const listVersions = (id) =>
  req3(`/api/v3/resumes/${id}/versions`);

export const restoreVersion = (id, version) =>
  req3(`/api/v3/resumes/${id}/versions/${version}/restore`,
       { method: "POST" });
