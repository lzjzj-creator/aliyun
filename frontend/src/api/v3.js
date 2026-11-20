// v3 长期记忆 + 岗位简历库 API（§8 契约）
// 鉴权只认 X-Owner-Token（存 localStorage），与 anon_id 完全解耦；
// 令牌/恢复码只在 enable/recover 响应里出现一次，之后服务端仅存哈希。
import { API_BASE } from "./core";

const TOKEN_KEY = "zhimai.owner_token";

export function getOwnerToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setOwnerToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function req3(path, { method = "GET", body, headers } = {}) {
  const h = { ...headers };
  const token = getOwnerToken();
  if (token) h["X-Owner-Token"] = token;
  if (body !== undefined) h["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(
      (typeof data.detail === "string" && data.detail) ||
      data.detail?.message || data.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// --- memory lifecycle -------------------------------------------------------

export const enableMemory = ({ recovery = true } = {}) =>
  req3("/api/v3/memory/enable", { method: "POST", body: { recovery } });

export const getMemory = () => req3("/api/v3/memory");

export const disableMemory = () =>
  req3("/api/v3/memory/disable", { method: "POST" });

export const deleteAllMemory = () =>
  req3("/api/v3/memory", { method: "DELETE", body: { confirm: "DELETE_ALL" } });

// JSON 导出：fetch 携带令牌拿 blob 再触发下载（不能裸链，令牌不出现在 URL）
export async function exportMemory() {
  const res = await fetch(`${API_BASE}/api/v3/memory/export`, {
    headers: { "X-Owner-Token": getOwnerToken() },
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

export const patchFact = (id, body) =>
  req3(`/api/v3/facts/${id}`, { method: "PATCH", body });

export const deleteFact = (id) =>
  req3(`/api/v3/facts/${id}`, { method: "DELETE" });

export const extractFacts = (sessionId) =>
  req3("/api/v3/facts/extract", { method: "POST", body: { session_id: sessionId } });

// --- resume library ---------------------------------------------------------

export const listResumes = () => req3("/api/v3/resumes");

export const createResume = (body) =>
  req3("/api/v3/resumes", { method: "POST", body });

export const getResume = (id) => req3(`/api/v3/resumes/${id}`);

export const patchResume = (id, body, version) =>
  req3(`/api/v3/resumes/${id}`, {
    method: "PATCH", body, headers: { "If-Match": String(version) },
  });

export const deleteResume = (id) =>
  req3(`/api/v3/resumes/${id}`, { method: "DELETE" });

export const regenerateResume = (id, body = {}) =>
  req3(`/api/v3/resumes/${id}/regenerate`, { method: "POST", body });
