// v2 访谈接口（A 监护契约：POST /api/v2/chat/stream，事件协议见 §4 与 demo/v2 fixture）
// session_id=null 时服务端建会话，session 事件一次性带回 session_token；
// 之后所有请求由 core.authHeaders 自动注入 X-Anon-Id / X-Session-Token。
import { sse } from "./core";

export function chatStreamV2({ sessionId = null, clientMsgId = null, message = "", handlers = {} } = {}) {
  return sse("/api/v2/chat/stream", {
    sessionId: sessionId || undefined,
    body: { session_id: sessionId || null, client_msg_id: clientMsgId, message },
    handlers,
  });
}
