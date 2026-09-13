import {
  POLL_INTERVAL_MS,
  SERVER_URL,
  TASK_TIMEOUT_MS,
  arrayBufferToBase64,
  boundedProgress,
  buildGuidePayload,
  buildTranslatePayload,
  displayTitleForTab,
  fileNameFromUrl,
  isCompleteTask,
  isFailedTask,
  jobMatchesTab,
  mergeInvokedTab,
  permissionPatternForUrl,
  pickTranslationFile,
  sha256Short,
  sleep,
  taskIdFromPayload,
  taskResult,
  uploadFileName,
  validatePdfBytes
} from "./lib.js";

const elements = {
  serverDot: document.querySelector("#serverDot"),
  sourceTitle: document.querySelector("#sourceTitle"),
  sourceMeta: document.querySelector("#sourceMeta"),
  translateCurrent: document.querySelector("#translateCurrent"),
  fileInput: document.querySelector("#fileInput"),
  progressCard: document.querySelector("#progressCard"),
  progressTitle: document.querySelector("#progressTitle"),
  overallPercent: document.querySelector("#overallPercent"),
  overallBar: document.querySelector("#overallBar"),
  stepRead: document.querySelector("#stepRead"),
  stepTranslate: document.querySelector("#stepTranslate"),
  stepGuide: document.querySelector("#stepGuide"),
  translationDetail: document.querySelector("#translationDetail"),
  guideDetail: document.querySelector("#guideDetail"),
  resultCard: document.querySelector("#resultCard"),
  resultMeta: document.querySelector("#resultMeta"),
  openReader: document.querySelector("#openReader"),
  notice: document.querySelector("#notice"),
  noticeTitle: document.querySelector("#noticeTitle"),
  noticeText: document.querySelector("#noticeText"),
  retryCurrent: document.querySelector("#retryCurrent"),
  retryServer: document.querySelector("#retryServer")
};

let activeTab = null;
let activeJob = null;
let running = false;

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  let { invokedTab } = await chrome.storage.session.get("invokedTab");
  if (!tab?.url && !invokedTab) {
    await sleep(100);
    ({ invokedTab } = await chrome.storage.session.get("invokedTab"));
  }
  return mergeInvokedTab(tab || null, invokedTab || null);
}

function sourceLabel(tab) {
  if (!tab?.url) return { title: "未找到当前标签", meta: "请先打开一个 PDF" };
  try {
    const parsed = new URL(tab.url);
    return {
      title: displayTitleForTab(tab),
      meta: parsed.protocol === "file:" ? "本地文件" : parsed.hostname
    };
  } catch {
    return { title: tab.title || "当前标签", meta: "无法识别地址" };
  }
}

function resetDocumentState() {
  activeJob = null;
  elements.resultCard.hidden = true;
  elements.progressCard.hidden = true;
  clearNotice();
  setStep(elements.stepRead, "");
  setStep(elements.stepTranslate, "");
  setStep(elements.stepGuide, "");
  setOverall(0, "正在准备论文");
}

async function refreshSource() {
  const nextTab = await getActiveTab();
  if (activeTab?.url && nextTab?.url && activeTab.url !== nextTab.url) resetDocumentState();
  activeTab = nextTab;
  const label = sourceLabel(activeTab);
  elements.sourceTitle.textContent = label.title;
  elements.sourceMeta.textContent = label.meta;
  elements.translateCurrent.disabled = !activeTab?.url || running;
}

async function checkServer() {
  try {
    const response = await fetch(`${SERVER_URL}/health`, { cache: "no-store" });
    const health = await response.json();
    if (!response.ok || health.status !== "ok") throw new Error("服务响应异常");
    elements.serverDot.className = "server-dot online";
    elements.serverDot.title = `本地服务在线 · ${health.version || "兼容服务"}`;
    return health;
  } catch {
    elements.serverDot.className = "server-dot offline";
    elements.serverDot.title = "本地服务未启动";
    return null;
  }
}

function requireCompatibleServer(health) {
  if (!health?.capabilities?.includes("serverDeepSeekProfileV1")) {
    throw new Error("本地服务缺少 ScholarSplit 所需能力，请按仓库安装说明更新兼容服务。");
  }
  return health;
}

function showNotice(title, message) {
  elements.noticeTitle.textContent = title;
  elements.noticeText.textContent = message;
  elements.notice.hidden = false;
}

function clearNotice() {
  elements.notice.hidden = true;
}

function setStep(element, state) {
  element.classList.remove("active", "done");
  if (state) element.classList.add(state);
}

function setOverall(percent, title) {
  const value = Math.round(Math.max(0, Math.min(100, percent)));
  elements.overallPercent.textContent = `${value}%`;
  elements.overallBar.style.width = `${value}%`;
  if (title) elements.progressTitle.textContent = title;
}

async function ensurePermission(url) {
  if (url.startsWith("file:")) {
    const allowed = await chrome.extension.isAllowedFileSchemeAccess();
    if (!allowed) {
      throw new Error("请在 chrome://extensions 的扩展详情中打开“允许访问文件网址”，或使用下方文件选择入口。");
    }
  }
  const origin = permissionPatternForUrl(url);
  const hasPermission = await chrome.permissions.contains({ origins: [origin] });
  if (hasPermission) return;
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("未获得当前 PDF 所在网站的临时读取权限。");
}

async function fetchPdfFromTab(tab) {
  if (!tab?.url) throw new Error("当前标签没有可读取的网址。");
  await ensurePermission(tab.url);
  const response = await fetch(tab.url, { credentials: "include", cache: "force-cache" });
  if (!response.ok) throw new Error(`读取当前 PDF 失败（HTTP ${response.status}）。`);
  const buffer = await response.arrayBuffer();
  validatePdfBytes(buffer);
  const headerName = response.headers.get("content-disposition")?.match(/filename\*?=(?:UTF-8''|\")?([^";]+)/i)?.[1];
  const name = headerName ? decodeURIComponent(headerName.replace(/^"|"$/g, "")) : fileNameFromUrl(tab.url, tab.title);
  return { buffer, name, title: displayTitleForTab(tab), sourceUrl: tab.url, originalTabId: tab.id, sourceKind: tab.url.startsWith("file:") ? "local-file" : "active-tab" };
}

async function postJob(endpoint, payload) {
  const response = await fetch(`${SERVER_URL}/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.status === "error" || body.status === "failed") {
    throw new Error(body.message || `${endpoint} 请求失败（HTTP ${response.status}）`);
  }
  const taskId = taskIdFromPayload(body);
  if (!taskId) throw new Error(`${endpoint} 请求没有返回任务编号。`);
  return taskId;
}

async function fetchTask(taskId) {
  for (const endpoint of ["api/tasks", "api/history"]) {
    const response = await fetch(`${SERVER_URL}/${endpoint}`, { cache: "no-store" });
    if (!response.ok) continue;
    const payload = await response.json();
    const rows = payload.tasks || payload.history || [];
    const task = rows.find((row) => String(row.taskId ?? row.task_id ?? "") === taskId);
    if (task) return task;
  }
  return null;
}

async function waitForTask(taskId, kind, onUpdate) {
  const deadline = Date.now() + TASK_TIMEOUT_MS;
  let misses = 0;
  while (Date.now() < deadline) {
    const task = await fetchTask(taskId);
    if (!task) {
      misses += 1;
      if (misses > 20) throw new Error(`${kind}任务已从本地队列消失。`);
    } else {
      misses = 0;
      onUpdate(task);
      if (isFailedTask(task)) throw new Error(task.error || task.message || `${kind}失败。`);
      if (isCompleteTask(task)) return taskResult(task);
    }
    await sleep(POLL_INTERVAL_MS);
  }
  throw new Error(`${kind}超时，请稍后重新打开扩展恢复任务。`);
}

async function persistJob(job) {
  activeJob = job;
  await chrome.storage.local.set({ [`job:${job.id}`]: job, lastJobId: job.id });
}

async function syncWorkspaceLibrary() {
  const response = await fetch(`${SERVER_URL}/api/v1/library/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-ScholarSplit-Client": "chrome-extension" },
    body: JSON.stringify({ commit: true }),
    cache: "no-store"
  });
  if (!response.ok) throw new Error(`文献库同步失败（HTTP ${response.status}）`);
}

async function openReader(job = activeJob) {
  if (!job?.id) return;
  await chrome.tabs.create({ url: chrome.runtime.getURL(`viewer.html?job=${encodeURIComponent(job.id)}`) });
}

function showResult(job) {
  elements.resultCard.hidden = false;
  elements.resultMeta.textContent = `${job.guideResult?.modelName || "本机模型"} · ${job.pageCount ? `${job.pageCount} 页 · ` : ""}本机处理${job.librarySyncWarning ? " · 文献库待重试" : " · 已收录到 ScholarSplit"}`;
  setOverall(100, "处理完成");
  setStep(elements.stepRead, "done");
  setStep(elements.stepTranslate, "done");
  setStep(elements.stepGuide, "done");
}

async function finishRunningJob(job, openWhenDone) {
  setStep(elements.stepRead, "done");
  setStep(elements.stepTranslate, "active");
  setStep(elements.stepGuide, "active");
  setOverall(10, "正在翻译与阅读论文");
  let translationProgress = 0;
  let guideProgress = 0;
  const [translationResult, guideResult] = await Promise.all([
    waitForTask(job.translationTaskId, "翻译", (task) => {
      translationProgress = boundedProgress(task);
      elements.translationDetail.textContent = task.message || task.status || "正在翻译";
      setOverall(10 + translationProgress * 0.58 + guideProgress * 0.26, "正在翻译与阅读论文");
    }),
    waitForTask(job.guideTaskId, "导读", (task) => {
      guideProgress = boundedProgress(task);
      elements.guideDetail.textContent = task.message || task.status || "正在生成导读";
      setOverall(10 + translationProgress * 0.58 + guideProgress * 0.26, "正在翻译与阅读论文");
    })
  ]);
  const translationFile = pickTranslationFile(translationResult);
  if (!translationFile) throw new Error("翻译完成，但本地服务没有返回可显示的译文 PDF。");
  if (guideResult?.kind !== "guide" || !guideResult.guide) throw new Error("导读完成，但结果格式无效。");
  const completed = {
    ...job,
    status: "complete",
    translationFile,
    guideResult,
    pageCount: guideResult.extraction?.pageCount,
    completedAt: new Date().toISOString()
  };
  await persistJob(completed);
  try {
    await syncWorkspaceLibrary();
  } catch (error) {
    completed.librarySyncWarning = String(error?.message || error);
    await persistJob(completed);
  }
  showResult(completed);
  if (openWhenDone) await openReader(completed);
  return completed;
}

async function runWorkflow(source) {
  if (running) return;
  running = true;
  clearNotice();
  elements.resultCard.hidden = true;
  elements.progressCard.hidden = false;
  elements.translateCurrent.disabled = true;
  setStep(elements.stepRead, "active");
  setStep(elements.stepTranslate, "");
  setStep(elements.stepGuide, "");
  setOverall(4, "正在校验 PDF");

  try {
    const health = await checkServer();
    if (!health) throw new Error("ScholarSplit 兼容本地服务未启动，请先按仓库安装说明启动服务。");
    requireCompatibleServer(health);
    validatePdfBytes(source.buffer);
    const hash = await sha256Short(source.buffer);
    const jobId = hash;
    const cached = (await chrome.storage.local.get(`job:${jobId}`))[`job:${jobId}`];
    if (cached?.status === "complete" && cached.translationFile && cached.guideResult?.guide) {
      await persistJob({ ...cached, sourceUrl: source.sourceUrl || cached.sourceUrl, originalTabId: source.originalTabId ?? cached.originalTabId });
      showResult(activeJob);
      running = false;
      elements.translateCurrent.disabled = false;
      return;
    }

    setStep(elements.stepRead, "done");
    setStep(elements.stepTranslate, "active");
    setStep(elements.stepGuide, "active");
    setOverall(10, "正在提交本地任务");
    const uploadName = uploadFileName(source.name, hash);
    let base64 = arrayBufferToBase64(source.buffer);
    let translationTaskId;
    let guideTaskId;
    try {
      [translationTaskId, guideTaskId] = await Promise.all([
        postJob("translate", buildTranslatePayload(uploadName, base64)),
        postJob("guide", buildGuidePayload(uploadName, base64, source.title))
      ]);
    } finally {
      base64 = "";
    }
    let job = {
      id: jobId,
      status: "running",
      sourceName: source.name,
      sourceTitle: source.title,
      sourceKind: source.sourceKind,
      sourceUrl: source.sourceUrl,
      originalTabId: source.originalTabId,
      uploadName,
      translationTaskId,
      guideTaskId,
      startedAt: new Date().toISOString()
    };
    await persistJob(job);
    await finishRunningJob(job, true);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    showNotice("暂时无法完成", message);
    elements.progressTitle.textContent = "任务未完成";
  } finally {
    running = false;
    elements.translateCurrent.disabled = !activeTab?.url;
  }
}

async function translateCurrent() {
  clearNotice();
  activeTab = await getActiveTab();
  if (!activeTab?.url) return showNotice("没有可读取的标签", "请先在 Chrome 中打开论文 PDF。");
  try {
    const source = await fetchPdfFromTab(activeTab);
    await runWorkflow(source);
  } catch (error) {
    showNotice("无法读取当前 PDF", `${error instanceof Error ? error.message : String(error)} 可以改用“选择已下载的 PDF”。`);
  }
}

async function translateFile(file) {
  clearNotice();
  try {
    const buffer = await file.arrayBuffer();
    await runWorkflow({ buffer, name: file.name, title: file.name.replace(/\.pdf$/i, ""), sourceUrl: `file-picker:${file.name}:${file.size}:${file.lastModified}`, sourceKind: "file-picker" });
  } catch (error) {
    showNotice("无法读取所选文件", error instanceof Error ? error.message : String(error));
  }
}

async function restoreLastJob() {
  const { lastJobId } = await chrome.storage.local.get("lastJobId");
  if (!lastJobId) return;
  const saved = (await chrome.storage.local.get(`job:${lastJobId}`))[`job:${lastJobId}`];
  if (!saved) return;
  if (!jobMatchesTab(saved, activeTab)) return;
  activeJob = saved;
  if (saved.status === "complete") {
    showResult(saved);
    return;
  }
  if (saved.status !== "running" || !saved.translationTaskId || !saved.guideTaskId) return;
  running = true;
  elements.progressCard.hidden = false;
  elements.translateCurrent.disabled = true;
  try {
    const health = await checkServer();
    if (!health) throw new Error("本地服务暂时离线，任务记录已经保留。");
    requireCompatibleServer(health);
    await finishRunningJob(saved, false);
  } catch (error) {
    showNotice("已保留未完成任务", error instanceof Error ? error.message : String(error));
    elements.progressTitle.textContent = "等待本地任务";
  } finally {
    running = false;
    elements.translateCurrent.disabled = !activeTab?.url;
  }
}

elements.translateCurrent.addEventListener("click", () => void translateCurrent());
elements.fileInput.addEventListener("change", () => {
  const [file] = elements.fileInput.files || [];
  if (file) void translateFile(file);
});
elements.openReader.addEventListener("click", () => void openReader());
elements.retryCurrent.addEventListener("click", () => void translateCurrent());
elements.retryServer.addEventListener("click", () => void checkServer());
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "session" && changes.invokedTab) void refreshSource();
});

await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
await refreshSource();
await Promise.all([checkServer(), restoreLastJob()]);
