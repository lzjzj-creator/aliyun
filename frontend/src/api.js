// 知脉前端 API 层
// 本地开发走 vite proxy → :8788；上线后把 API_BASE 换成后端公网地址

const API_BASE = import.meta.env.VITE_API_BASE || "";
// 简历诊断：复用 A 的服务（部署后填你的 CloudBase 地址）
const ANALYZE_URL = import.meta.env.VITE_ANALYZE_URL || "/analyze-proxy";

export function routeIntent(query, hasAttachment = false) {
  return fetch(`${API_BASE}/api/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, has_attachment: hasAttachment }),
  }).then((r) => r.json());
}

// SSE 流式问答：fetch + ReadableStream 手解（EventSource 不支持 POST）
export async function chatStream(query, history, handlers) {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, history }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const part of parts) {
      const ev = part.match(/^event: (\w+)\ndata: (.*)$/s);
      if (!ev) continue;
      const [, event, dataStr] = ev;
      let data;
      try { data = JSON.parse(dataStr); } catch { data = dataStr; }
      handlers[event]?.(data);
    }
  }
}

export function analyzeResume(resumeText, jobText = "") {
  return fetch(ANALYZE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: jobText.trim() ? "targeted" : "general",
      engine: "deepseek",
      resume_text: resumeText,
      job_text: jobText,
    }),
  }).then((r) => r.json());
}

export function rewriteBullets(bullets, confirmedFacts = "") {
  return fetch(`${API_BASE}/api/resume/rewrite`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bullets, confirmed_facts: confirmedFacts }),
  }).then((r) => r.json());
}

async function adminFetch(path, { token = "", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function adminLogin(password) {
  return adminFetch("/api/admin/login", { body: { password } });
}

export function adminOverview(token) {
  return adminFetch("/api/admin/overview", { token });
}

export function adminDocuments(token, q = "", scope = "") {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (scope) params.set("scope", scope);
  const qs = params.toString();
  return adminFetch(`/api/admin/documents${qs ? `?${qs}` : ""}`, { token });
}
