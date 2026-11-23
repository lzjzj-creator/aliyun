// AuthWall 节奏控制（24h 节流）。zm_authwall_at 只是弹窗时间戳，非业务数据。
const AUTHWALL_KEY = "zm_authwall_at";

export function authwallAllowed() {
  try {
    const at = Number(localStorage.getItem(AUTHWALL_KEY) || 0);
    return Date.now() - at > 24 * 3600 * 1000;
  } catch { return true; }
}

export function authwallSnoozed() {
  try { localStorage.setItem(AUTHWALL_KEY, String(Date.now())); } catch { /* 忽略 */ }
}
