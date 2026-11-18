// v2 会话接口（A 监护契约：GET/DELETE /api/v2/sessions、POST /api/v2/messages）
import { req } from "./core";

export const listSessions = () => req("/api/v2/sessions");

export const getSession = (id) => req(`/api/v2/sessions/${id}`, { sessionId: id });

export const deleteSession = (id) =>
  req(`/api/v2/sessions/${id}`, { method: "DELETE", sessionId: id });

// P0 过渡回写：{session_id?, client_msg_id, user, assistant, meta}
// 无 session_id 时由服务端建会话并返回 {session_id, session_token}
export const writebackRound = (payload, sessionId) =>
  req("/api/v2/messages", { method: "POST", body: payload, sessionId });
