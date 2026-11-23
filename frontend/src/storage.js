// v5 本地只存一把钥匙：auth_token（guest 也算）。
// 业务数据（会话/消息/画像/事实/草稿/照片/设置）全部在 Supabase 服务端，
// 由 user_id 归属；注册=升级同一行，浏览器无数据迁移。
// 旧 zm_* / zhimai.* 数据键上线即清（存量为测试数据，不做迁移）。

const TOKEN_KEY = "zhimai.auth_token";

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setAuthToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

const LEGACY_PREFIXES = ["zm_", "zhimai."];
// 不被清理的非业务键：UI 偏好（侧栏折叠）。auth_token 自身显然保留。
const KEEP = new Set([TOKEN_KEY, "zm_sidebar_collapsed", "zm_authwall_at"]);

export function clearLegacyKeys() {
  try {
    for (const key of Object.keys(localStorage)) {
      if (KEEP.has(key)) continue;
      if (LEGACY_PREFIXES.some((p) => key.startsWith(p))) {
        localStorage.removeItem(key);
      }
    }
  } catch { /* 存储不可用则跳过 */ }
}
