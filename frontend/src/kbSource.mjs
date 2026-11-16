const ZERO_WIDTH = /[\u200b-\u200f\u202a-\u202e\u2060\ufeff]/g;

const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");

export function toMirrorHref(url, base) {
  if (!url) return "#";
  const mirrorBase = (base || "").replace(/\/+$/, "");
  if (!mirrorBase) return "#";

  let path = url;
  if (!url.startsWith("/")) {
    try {
      path = new URL(url).pathname;
    } catch {
      return "#";
    }
  }

  try {
    path = decodeURIComponent(path);
  } catch {
    // 保留原路径，后续仍按静态页面规则处理
  }
  path = path.replace(ZERO_WIDTH, "");
  if (path.endsWith("/index/index.html")) {
    path = path.slice(0, -"/index.html".length);
  }

  const stripped = path.replace(/\/+$/, "");
  if (stripped.endsWith("/index")) path = stripped + ".html";
  else if (path.endsWith("/")) path += "index.html";
  else if (!/\.[a-zA-Z0-9]{1,8}$/.test(path)) path += "/index.html";

  return mirrorBase + encodePath(path);
}