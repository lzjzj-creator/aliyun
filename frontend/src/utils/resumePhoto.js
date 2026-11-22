import { isValidResumePhoto, jpegDimensions } from "../../../backend/resume_assets/render.mjs";

const HEADER_LIMIT = 256 * 1024;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const QUALITIES = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1];

function pngDimensions(bytes) {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82];
  if (bytes.length < 33 || !signature.every((value, i) => bytes[i] === value)) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

function checkDimensions(size) {
  if (!size || !Number.isInteger(size.width) || !Number.isInteger(size.height) || size.width < 1 || size.height < 1) {
    throw new Error("无法读取图片尺寸，请换一张有效的 JPEG 或 PNG 照片。");
  }
  if (size.width > 4096 || size.height > 4096 || size.width * size.height > 12000000) {
    throw new Error("照片尺寸过大：每边最多 4096 像素，总像素不超过 1200 万。请缩小后重试。");
  }
}

// Inspect bounded headers before decoding; rasterization strips metadata and flattens alpha.
export async function prepareResumePhoto(file) {
  if (!file || !["image/jpeg", "image/png"].includes(file.type)) {
    throw new Error("请选择 JPEG 或 PNG 照片，不支持 SVG、GIF 或外部链接。");
  }
  if (!Number.isFinite(file.size) || file.size <= 0 || file.size > MAX_FILE_BYTES) {
    throw new Error("照片不能为空且不能超过 5 MB，请压缩后重试。");
  }
  const bytes = new Uint8Array(await file.slice(0, HEADER_LIMIT).arrayBuffer());
  checkDimensions(file.type === "image/jpeg" ? jpegDimensions(bytes) : pngDimensions(bytes));

  let image, objectUrl;
  try {
    if (typeof createImageBitmap === "function") {
      image = await createImageBitmap(file);
    } else {
      objectUrl = URL.createObjectURL(file);
      image = new Image();
      await new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error("照片解码失败，请换一张有效的 JPEG 或 PNG 照片。"));
        image.src = objectUrl;
      });
    }
    // Orientation handling and browser decoders may change the reported size.
    const width = image.naturalWidth ?? image.width;
    const height = image.naturalHeight ?? image.height;
    checkDimensions({ width, height });
    const ratio = Math.min(1, 240 / width, 320 / height);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(width * ratio));
    canvas.height = Math.max(1, Math.floor(height * ratio));
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("浏览器无法处理照片，请更换浏览器后重试。");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    for (const quality of QUALITIES) {
      const result = canvas.toDataURL("image/jpeg", quality);
      if (isValidResumePhoto(result)) return result;
    }
    throw new Error("照片压缩失败（需不超过 30 KB），请换一张较小的照片。");
  } catch (error) {
    throw new Error(`照片处理失败：${error.message || "请重新选择有效的 JPEG 或 PNG 图片。"}`);
  } finally {
    image?.close?.();
    if (objectUrl) {
      try {
        if (image) {
          image.onload = null;
          image.onerror = null;
          image.src = "";
        }
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }
  }
}
