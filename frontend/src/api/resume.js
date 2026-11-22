// v2 简历域接口（B 监护契约：template/autofill/optimize）
import { API_BASE, authHeaders, req } from "./core";

// 明确字段快照即可导出，不要求开启长期记忆；带会话 ID 时必须同时持有凭据。
export async function exportDraftPdf(payload) {
  const headers = authHeaders(payload.session_id, {
    "Content-Type": "application/json", Accept: "application/pdf",
  });
  if (payload.session_id && !headers["X-Session-Token"]) {
    throw new Error("缺少该会话的本地凭据，请使用当前草稿快照导出");
  }
  const res = await fetch(`${API_BASE}/api/v2/resume/pdf`, {
    method: "POST", headers, body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data.detail;
    const error = new Error(
      (typeof detail === "string" && detail) || detail?.message || `HTTP ${res.status}`);
    Object.assign(error, { status: res.status, code: detail?.code, issues: detail?.issues, data });
    throw error;
  }
  if (!res.headers.get("Content-Type")?.toLowerCase().startsWith("application/pdf")) {
    throw new Error("导出服务未返回 PDF，请重试");
  }
  return res.blob();
}

export const getTemplate = () => req("/api/v2/resume/template");

// {session_id | messages+profile, template_version} → {fields, evidence_map, gaps}
export const autofill = (payload, sessionId) =>
  req("/api/v2/resume/autofill", { method: "POST", body: payload, sessionId });

function optimizationPayload(payload) {
  const fields = { ...payload.fields };
  delete fields._meta;
  return { ...payload, fields };
}

// {template_version, fields} → {sections:[{key,title,priority,optimizable,status,...}]}
export const optimizePlan = (payload) =>
  req("/api/v2/resume/optimize/plan", { method: "POST", body: optimizationPayload(payload) });

// {template_version, section, fields} → {optimized, change_log, placeholders, ...}
export const optimizeSection = (payload) =>
  req("/api/v2/resume/optimize/section", { method: "POST", body: optimizationPayload(payload) });
