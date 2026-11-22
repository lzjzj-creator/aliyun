// 只使用本地 blob URL 下载，永不把访问凭据拼到 URL；不信任文档标题作为路径。
export function downloadResumePdf(blob, title = "简历") {
  const name = String(title)
    .replace(/[\u0000-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, "")
    .replace(/[<>:"/\\|?*]/g, "_")
    .replace(/\.(?:pdf|exe|com|bat|cmd|scr|js|html?)(?=\.|$)/gi, "")
    .replace(/^[.\s]+|[.\s]+$/g, "")
    .slice(0, 100) || "简历";
  const safeName = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name) ? `简历_${name}` : name;
  const url = URL.createObjectURL(blob);
  let link;
  try {
    link = document.createElement("a");
    link.href = url;
    link.download = `${safeName}.pdf`;
    document.body.appendChild(link);
    link.click();
  } finally {
    link?.remove();
    // 给浏览器时间接管下载，再释放资源；异常路径也必须释放。
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
