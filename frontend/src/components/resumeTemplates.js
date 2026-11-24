// 简历模板注册表：key 与后端 render.mjs / resume_pdf.py 的 layout 枚举对齐。
export const RESUME_TEMPLATES = [
  {
    key: "classic",
    name: "经典黑",
    desc: "单栏竖排，黑白灰克制配色",
    badge: "ATS 友好",
    hint: "校招网申、国企外企稳妥首选",
  },
  {
    key: "modern",
    name: "墨蓝双栏",
    desc: "深色侧栏，教育/奖项左置，经历右栏展开",
    badge: "视觉突出",
    hint: "作品集投递、设计感场景更抓眼",
  },
];

export const templateName = (key) =>
  RESUME_TEMPLATES.find((t) => t.key === key)?.name || "经典黑";
