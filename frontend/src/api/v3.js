// v3 长期记忆 + 岗位简历库 API（§8 契约）
// owner 资源由 X-Owner-Token 鉴权；会话抽取还需 anon + 本地会话令牌。
// 令牌/恢复码只在 enable/recover 响应里出现一次，之后服务端仅存哈希。
import { API_BASE } from "./core";
import { getAnonId, getSessionToken } from "../storage";

const TOKEN_KEY = "zhimai.owner_token";

export function getOwnerToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setOwnerToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function req3(path, { method = "GET", body, headers, responseType } = {}) {
  const h = { ...headers, "X-Anon-Id": getAnonId() };
  const token = getOwnerToken();
  if (token) h["X-Owner-Token"] = token;
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

export const enableMemory = ({ recovery = true } = {}) =>
  req3("/api/v3/memory/enable", { method: "POST", body: { recovery } });

export async function recoverMemory(recoveryCode) {
  const code = recoveryCode.trim();
  if (!code) throw new Error("请输入恢复码");
  const data = await req3("/api/v3/memory/recover", {
    method: "POST", body: { recovery_code: code },
  });
  if (!data.owner_token) throw new Error("恢复响应缺少访问令牌，请勿关闭当前页面并联系支持");
  // 必须在后续状态请求前替换旧令牌；失败时绝不清除原凭据。
  setOwnerToken(data.owner_token);
  return data;
}

export const getMemory = () => req3("/api/v3/memory");

export const disableMemory = () =>
  req3("/api/v3/memory/disable", { method: "POST" });

export const deleteAllMemory = () =>
  req3("/api/v3/memory", { method: "DELETE", body: { confirm: "DELETE_ALL" } });

// JSON 导出：fetch 携带令牌拿 blob 再触发下载（不能裸链，令牌不出现在 URL）
export async function exportMemory() {
  const res = await fetch(`${API_BASE}/api/v3/memory/export`, {
    headers: { "X-Owner-Token": getOwnerToken(), "X-Anon-Id": getAnonId() },
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

export function hasSessionCredential(sessionId) {
  const token = getSessionToken(sessionId);
  return typeof token === "string" && token.length > 0;
}

function requireSessionToken(sessionId) {
  if (!sessionId || !hasSessionCredential(sessionId)) {
    const error = new Error("缺少该会话的本地凭据，无法抽取或回填；请在持有原会话凭据的浏览器操作");
    error.code = "missing_session_token";
    throw error;
  }
  return getSessionToken(sessionId);
}

export async function extractFacts(sessionId) {
  const token = requireSessionToken(sessionId);
  return req3("/api/v3/facts/extract", {
    method: "POST", body: { session_id: sessionId },
    headers: { "X-Session-Token": token },
  });
}

export async function backfill(sessionIds) {
  // 不让空参数隐式抓取 anon 下所有会话；只提交显式选择且持有凭据的会话。
  if (!Array.isArray(sessionIds) || !sessionIds.length) {
    throw new Error("请选择持有本地凭据的会话后再回填");
  }
  const ids = [...new Set(sessionIds)];
  const sessionTokens = Object.fromEntries(ids.map((sid) => [sid, requireSessionToken(sid)]));
  return req3("/api/v3/memory/backfill", {
    method: "POST", body: { session_ids: ids, session_tokens: sessionTokens },
  });
}

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
