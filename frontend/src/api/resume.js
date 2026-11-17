// v2 简历域接口（B 监护契约：template/autofill/optimize）
import { req } from "./core";

export const getTemplate = () => req("/api/v2/resume/template");

// {session_id | messages+profile, template_version} → {fields, evidence_map, gaps}
export const autofill = (payload, sessionId) =>
  req("/api/v2/resume/autofill", { method: "POST", body: payload, sessionId });

// {template_version, fields} → {sections:[{key,title,priority,optimizable,status,...}]}
export const optimizePlan = (payload) =>
  req("/api/v2/resume/optimize/plan", { method: "POST", body: payload });

// {template_version, section, fields} → {optimized, change_log, placeholders, ...}
export const optimizeSection = (payload) =>
  req("/api/v2/resume/optimize/section", { method: "POST", body: payload });
