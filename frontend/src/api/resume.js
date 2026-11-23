// v2 简历域接口（B 监护契约：template/autofill/optimize）
import { API_BASE, authHeaders, req } from "./core";

// 明确字段快照即可导出；Bearer 归属下服务端按 user_id 校验素材权限。
export async function exportDraftPdf(payload) {
  const headers = authHeaders(payload.session_id, {
    "Content-Type": "application/json", Accept: "application/pdf",
  });
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

// {session_ids[] | messages+profile, include_facts, template_version}
// → {fields, evidence_map, gaps}（v5：多会话+事实合并素材）
export const autofill = (payload, sessionId) =>
  req("/api/v2/resume/autofill", { method: "POST", body: payload, sessionId });

// {template_version, section?, fields} → STAR 法则扩写后的字段快照
export const expand = (payload) =>
  req("/api/v2/resume/expand", { method: "POST", body: payload });

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
