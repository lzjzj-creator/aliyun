// v4 账号接口：guest 签发 / 注册升级 / 登录 / me / init_profile / settings / 草稿
// 本地只存 auth_token；业务数据全在服务端（v5 计划 §1/§4）。
// Bearer 注入集中在 api/core.js（authHeaders/req），本文件只做语义封装。
import { req } from "./core";
import { getAuthToken, setAuthToken, clearLegacyKeys } from "../storage";

const req4 = req;   // 同一封装：Bearer 由 core 统一注入，错误带 status/code/issues

let guestPromise = null;

// 静默 guest 启动：无 token 时自动签发；有 token 时直接复用。
// 失败不阻塞 UI（返回 null），调用方降级为未认证态再重试。
export function ensureAuth() {
  if (getAuthToken()) return Promise.resolve(getAuthToken());
  if (!guestPromise) {
    guestPromise = req4("/api/v4/auth/guest", { method: "POST" })
      .then((d) => {
        setAuthToken(d.auth_token);
        clearLegacyKeys();
        return d.auth_token;
      })
      .catch((e) => {
        guestPromise = null;
        console.warn("guest 签发失败", e.status || e.message);
        return null;
      });
  }
  return guestPromise;
}

export const register = (username, password) =>
  req4("/api/v4/auth/register", { method: "POST", body: { username, password } })
    .then((d) => { if (d.auth_token) setAuthToken(d.auth_token); return d; });

export const login = (username, password) =>
  req4("/api/v4/auth/login", { method: "POST", body: { username, password } })
    .then((d) => { setAuthToken(d.auth_token); return d; });

export async function logout() {
  try { await req4("/api/v4/auth/logout", { method: "POST" }); }
  finally { setAuthToken(""); guestPromise = null; }
}

export const getMe = () => req4("/api/v4/me");

// PUT /me/init：body={init_profile:{...}}，缺必填返回 422+issues
export const putInit = (initProfile) =>
  req4("/api/v4/me/init", { method: "PUT", body: { init_profile: initProfile } });

export const putSettings = (patch) =>
  req4("/api/v4/me/settings", { method: "PUT", body: { settings: patch } });

export const markGuideSeen = () =>
  req4("/api/v4/me/guide-seen", { method: "POST" });

export const deleteAccount = () =>
  req4("/api/v4/me", { method: "DELETE", body: { confirm: "DELETE_ACCOUNT" } })
    .finally(() => { setAuthToken(""); guestPromise = null; });

// —— 服务端草稿 ——
export const listDrafts = () => req4("/api/v4/drafts");
export const getDraft = (id) => req4(`/api/v4/drafts/${id}`);
export const putDraft = (draft) =>
  req4("/api/v4/drafts", { method: "PUT", body: draft });
export const deleteDraft = (id) =>
  req4(`/api/v4/drafts/${id}`, { method: "DELETE" });
