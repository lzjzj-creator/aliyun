import html2canvas from "html2canvas";
import { jsPDF } from "jspdf";

/**
 * Render a DOM element into a downloadable A4 PDF file without invoking window.print().
 */
export async function downloadElementAsPdf(el, filename = "resume.pdf") {
  if (!el) throw new Error("未找到可导出的简历预览区域");
  const canvas = await html2canvas(el, {
    scale: 2,
    useCORS: true,
    backgroundColor: "#ffffff",
    logging: false,
  });

  const pdf = new jsPDF({ orientation: "p", unit: "mm", format: "a4" });
  const pageW = pdf.internal.pageSize.getWidth();
  const pageH = pdf.internal.pageSize.getHeight();
  const margin = 10;
  const contentW = pageW - margin * 2;
  const contentH = pageH - margin * 2;
  const imgH = (canvas.height * contentW) / canvas.width;
  const imgData = canvas.toDataURL("image/png");

  let heightLeft = imgH;
  let offset = margin;
  pdf.addImage(imgData, "PNG", margin, offset, contentW, imgH);
  heightLeft -= contentH;

  while (heightLeft > 0) {
    offset = margin - (imgH - heightLeft);
    pdf.addPage();
    pdf.addImage(imgData, "PNG", margin, offset, contentW, imgH);
    heightLeft -= contentH;
  }

  const safe = (filename || "resume").replace(/[\\/:*?"<>|]+/g, "_").trim() || "resume";
  pdf.save(safe.endsWith(".pdf") ? safe : `${safe}.pdf`);
}
