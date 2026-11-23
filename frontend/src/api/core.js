// v5 API 底座：Bearer 注入、统一 JSON 请求、SSE 解析（v5 计划 §3/§4）
// 浏览器只持有 auth_token（guest 也算）；旧的 X-Anon-Id / X-Session-Token /
// X-Owner-Token 三套头已废除，服务端按 Authorization 解析 user_id。
import { getAuthToken } from "../storage";

export const API_BASE = import.meta.env.VITE_API_BASE || "";

export function authHeaders(_sessionId, extra = {}) {
  // 参数保留 sessionId 兼容旧调用签名；Bearer 下不需要会话级令牌。
  const h = { ...extra };
  const t = getAuthToken();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

// 统一 JSON 请求；非 2xx 抛带 status/code/issues 的 Error（404 用于越权判定，
// detail 可能是字符串或 {code,message,retryable,issues} 对象——两种都展开）
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
    const detail = data.detail;
    const message = (typeof detail === "string" && detail)
      || detail?.message || data.message || `HTTP ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    err.code = detail?.code;
    err.issues = detail?.issues || data.issues;
    err.retryable = detail?.retryable;
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
    // 非 2xx 也尽量带 detail（与 req() 一致），区分"未开放"与"预算耗尽"等 503
    const data = await res.json().catch(() => ({}));
    const err = new Error(data.detail || data.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // Normalize after accumulation: CR and LF may arrive in separate chunks.
    buf += decoder.decode(value, { stream: true });
    buf = buf.replace(/\r\n/g, "\n");
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
