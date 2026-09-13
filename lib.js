export const SERVER_URL = "http://127.0.0.1:8890";
export const MAX_PDF_BYTES = 100 * 1024 * 1024;
export const POLL_INTERVAL_MS = 1200;
export const TASK_TIMEOUT_MS = 45 * 60 * 1000;

const PDF_SUFFIX = /\.pdf(?:$|[?#])/i;
const GENERATED_SUFFIX = /(?:\.no_watermark)?(?:\.[a-z]{2}(?:-[a-z]{2})?)?(?:[._](?:lr|tb)_dual|\.mono(?:-cut)?|\.dual(?:-cut)?|\.crop-compare|\.compare)$/i;

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function safeFileName(value) {
  const leaf = String(value || "paper.pdf")
    .split(/[\\/]/)
    .pop()
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .trim();
  const withExtension = PDF_SUFFIX.test(leaf) ? leaf.replace(/[?#].*$/, "") : `${leaf || "paper"}.pdf`;
  const stem = withExtension.replace(/\.pdf$/i, "").replace(GENERATED_SUFFIX, "");
  return `${stem.slice(0, 110) || "paper"}.pdf`;
}

export function fileNameFromUrl(url, fallback = "paper.pdf") {
  try {
    const parsed = new URL(url);
    const dispositionName = parsed.searchParams.get("filename");
    const leaf = dispositionName || decodeURIComponent(parsed.pathname.split("/").pop() || "");
    return safeFileName(leaf || fallback);
  } catch {
    return safeFileName(fallback);
  }
}

export function permissionPatternForUrl(url) {
  const parsed = new URL(url);
  if (parsed.protocol === "file:") return "file:///*";
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("当前标签不是可读取的 HTTP、HTTPS 或本地 PDF。");
  }
  return `${parsed.protocol}//${parsed.host}/*`;
}

export function mergeInvokedTab(queriedTab, invokedTab) {
  if (!queriedTab) return invokedTab || null;
  if (queriedTab.url || queriedTab.id !== invokedTab?.id) return queriedTab;
  return {
    ...invokedTab,
    ...queriedTab,
    url: invokedTab.url || "",
    title: queriedTab.title || invokedTab.title || ""
  };
}

export function validatePdfBytes(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 4_000) {
    throw new Error("文件为空或过小，不像完整 PDF。");
  }
  if (buffer.byteLength > MAX_PDF_BYTES) {
    throw new Error("PDF 超过 100 MB，本地服务暂不处理。");
  }
  const bytes = new Uint8Array(buffer);
  const head = new TextDecoder("latin1").decode(bytes.slice(0, Math.min(1024, bytes.length)));
  const tail = new TextDecoder("latin1").decode(bytes.slice(Math.max(0, bytes.length - 65_536)));
  if (!head.includes("%PDF-")) throw new Error("当前地址返回的不是 PDF，可能是登录页或错误页面。");
  if (!tail.includes("%%EOF")) throw new Error("PDF 未下载完整，缺少 EOF 标记。");
  return true;
}

export function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)));
  }
  return btoa(binary);
}

export async function sha256Short(buffer, length = 20) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, length);
}

export function uploadFileName(originalName, hash) {
  const safe = safeFileName(originalName);
  const stem = safe.replace(/\.pdf$/i, "");
  return `chrome-${hash}-${stem}.pdf`;
}

export function taskIdFromPayload(payload) {
  const raw = payload?.taskId ?? payload?.task_id ?? "";
  return String(raw || "").trim();
}

export function buildTranslatePayload(fileName, base64) {
  return {
    fileName,
    fileContent: base64,
    asyncJob: true,
    engine: "pdf2zh_next",
    next_service: "deepseek",
    sourceLang: "en",
    targetLang: "zh-CN",
    qps: 10,
    poolSize: 0,
    mono: false,
    dual: false,
    compare: true,
    noMono: true,
    noDual: false,
    dualMode: "LR",
    transFirst: false,
    ocr: false,
    autoOcr: true,
    noWatermark: true,
    disableGlossary: true,
    useServerDeepSeekConfig: true
  };
}

export function buildGuidePayload(fileName, base64, title) {
  return {
    fileName,
    fileContent: base64,
    title: String(title || fileName).slice(0, 500),
    forceRegenerate: false,
    asyncJob: true
  };
}

export function isFailedTask(task) {
  const nested = task?.result && typeof task.result === "object" ? task.result : {};
  const status = String(task?.status || nested.status || "").toLowerCase();
  return status === "failed" || status === "失败" || nested.status === "error" || Boolean(task?.error);
}

export function isCompleteTask(task) {
  const nested = task?.result && typeof task.result === "object" ? task.result : {};
  const status = String(task?.status || nested.status || "").toLowerCase();
  return task?.finished === true || status === "success" || status === "完成" || nested.status === "success";
}

export function taskResult(task) {
  return task?.result && typeof task.result === "object" ? task.result : task;
}

export function pickTranslationFile(result) {
  const files = Array.isArray(result?.fileList) ? result.fileList.filter((item) => typeof item === "string") : [];
  return (
    files.find((name) => /\.compare\.pdf$/i.test(name)) ||
    files.find((name) => /(?:[._](?:lr|tb)_dual|\.dual)\.pdf$/i.test(name)) ||
    files.find((name) => /\.mono\.pdf$/i.test(name)) ||
    files.find((name) => /\.pdf$/i.test(name)) ||
    ""
  );
}

export function boundedProgress(task) {
  const raw = Number(task?.progress);
  return Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 0;
}
