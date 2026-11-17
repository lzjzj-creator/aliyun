// v2 API 底座：anon/令牌头注入、统一 JSON 请求、SSE 解析（§4/§5）
import { getAnonId, getSessionToken } from "../storage";

export const API_BASE = import.meta.env.VITE_API_BASE || "";

export function authHeaders(sessionId, extra = {}) {
  const h = { "X-Anon-Id": getAnonId(), ...extra };
  if (sessionId) {
    const t = getSessionToken(sessionId);
    if (t) h["X-Session-Token"] = t;
  }
  return h;
}

// 统一 JSON 请求；非 2xx 抛带 status 的 Error（404 用于越权判定）
export async function req(path, { method = "GET", body, sessionId, headers } = {}) {
  const h = authHeaders(sessionId, headers);
  if (body !== undefined) h["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// SSE POST 流解析（与 v1 chatStream 同一套手解协议），handlers[event](data)
export async function sse(path, { body, sessionId, headers, handlers } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders(sessionId, { "Content-Type": "application/json", ...headers }),
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
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
      handlers?.[event]?.(data);
    }
  }
}

// fixture 回放：把 demo/v2/*.json 的事件数组逐条派发给 handlers（离线开发用）
export async function replayEvents(events, handlers, { delay = 0 } = {}) {
  for (const { event, data } of events) {
    handlers?.[event]?.(data);
    if (delay) await new Promise((r) => setTimeout(r, delay));
  }
}
