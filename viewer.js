import { SERVER_URL, validatePdfBytes } from "./lib.js";

const params = new URLSearchParams(location.search);
const jobId = params.get("job");
const fatal = document.querySelector("#fatal");
const fatalText = document.querySelector("#fatalText");
const root = document.querySelector("#guideRoot");
let pdfObjectUrl = "";

function showFatal(message) {
  fatalText.textContent = message;
  fatal.hidden = false;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function joined(item, fields, separator = " — ") {
  return fields.map((field) => item?.[field]).filter(Boolean).join(separator);
}

function appendListSection(container, index, title, values) {
  const items = values.filter(Boolean);
  if (!items.length) return index;
  const section = element("section", "guide-section");
  const heading = element("h2", "", title);
  heading.dataset.index = String(index).padStart(2, "0");
  const list = element("ol", "guide-list");
  for (const value of items) list.append(element("li", "", value));
  section.append(heading, list);
  container.append(section);
  return index + 1;
}

function appendMethod(container, index, method) {
  const fields = [
    ["设计", method?.design],
    ["样本", method?.sample],
    ["数据材料", method?.data],
    ["分析策略", method?.analysis]
  ].filter(([, value]) => value);
  if (!fields.length) return index;
  const section = element("section", "guide-section");
  const heading = element("h2", "", "研究方法");
  heading.dataset.index = String(index).padStart(2, "0");
  const grid = element("div", "method-grid");
  for (const [label, value] of fields) {
    const card = element("div", "method-card");
    card.append(element("b", "", label), element("p", "", value));
    grid.append(card);
  }
  section.append(heading, grid);
  container.append(section);
  return index + 1;
}

function appendAdvancedStats(container, index, advanced) {
  if (!advanced?.summary) return index;
  const section = element("section", "guide-section");
  const heading = element("h2", "", "高级统计拆解");
  heading.dataset.index = String(index).padStart(2, "0");
  section.append(heading, element("p", "thesis", advanced.summary));
  for (const method of advanced.methods || []) {
    const card = element("article", "stats-card");
    card.append(element("h3", "", method.name || "统计方法"));
    for (const [label, key] of [
      ["作用", "role"], ["模型结构", "modelStructure"], ["变量", "inputsAndVariables"],
      ["估计", "estimation"], ["假设", "assumptions"], ["诊断", "diagnostics"],
      ["解释", "interpretation"], ["局限", "limitations"]
    ]) {
      if (method[key]) card.append(element("p", "", `${label}：${method[key]}`));
    }
    section.append(card);
  }
  container.append(section);
  return index + 1;
}

function appendConcepts(container, index, concepts) {
  if (!concepts?.length) return index;
  const section = element("section", "guide-section");
  const heading = element("h2", "", "概念表");
  heading.dataset.index = String(index).padStart(2, "0");
  const grid = element("div", "concepts");
  for (const item of concepts) {
    const card = element("div", "concept");
    const line = element("div");
    line.append(element("b", "", item.term || ""), document.createTextNode(" "), element("span", "", item.translation || ""));
    card.append(line, element("p", "", item.meaning || ""));
    grid.append(card);
  }
  section.append(heading, grid);
  container.append(section);
  return index + 1;
}

function renderGuide(result) {
  const guide = result.guide;
  root.replaceChildren();
  root.append(
    element("p", "guide-kicker", `${String(result.modelName || "DEEPSEEK").toUpperCase()} · ${result.cached ? "缓存导读" : "新生成"}`),
    element("h1", "guide-title", guide.title),
    element("p", "original-title", guide.originalTitle || ""),
    element("p", "thesis", guide.oneSentence)
  );
  let index = 1;
  index = appendListSection(root, index, "研究问题", (guide.questions || []).map((item) => joined(item, ["text", "page"], " · ")));
  index = appendListSection(root, index, "理论与核心构念", (guide.theory || []).map((item) => joined(item, ["name", "role", "page"])));
  index = appendMethod(root, index, guide.method);
  index = appendAdvancedStats(root, index, guide.advancedStatistics);
  index = appendListSection(root, index, "关键发现", (guide.findings || []).map((item) => joined(item, ["claim", "evidence", "page"])));
  index = appendListSection(root, index, "理论与方法贡献", (guide.contributions || []).map((item) => joined(item, ["text", "page"], " · ")));
  index = appendConcepts(root, index, guide.concepts || []);
  index = appendListSection(root, index, "阅读路径", (guide.readingPath || []).map((item) => joined(item, ["pages", "focus"])));
  index = appendListSection(root, index, "局限与提醒", [
    ...(guide.limitations || []).map((item) => joined(item, ["text", "page"], " · ")),
    ...(guide.cautions || [])
  ]);
  appendListSection(root, index, "可带走的结论", guide.takeaways || []);
}

async function init() {
  if (!jobId) throw new Error("阅读页缺少任务编号。");
  const key = `job:${jobId}`;
  const job = (await chrome.storage.local.get(key))[key];
  if (!job || job.status !== "complete") throw new Error("没有找到已完成的翻译任务。");
  const translatedUrl = `${SERVER_URL}/translatedFile/${encodeURIComponent(job.translationFile)}?preview=true`;
  const response = await fetch(translatedUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`无法读取本机译文（HTTP ${response.status}）。`);
  const pdfBuffer = await response.arrayBuffer();
  validatePdfBytes(pdfBuffer);
  pdfObjectUrl = URL.createObjectURL(new Blob([pdfBuffer], { type: "application/pdf" }));
  document.querySelector("#documentTitle").textContent = job.sourceTitle || job.sourceName || "论文译文与导读";
  document.querySelector("#pdfMeta").textContent = job.pageCount ? `${job.pageCount} 页` : "本机译文";
  document.querySelector("#pdfFrame").src = pdfObjectUrl;
  const download = document.querySelector("#downloadPdf");
  download.href = pdfObjectUrl;
  download.download = job.translationFile;
  document.querySelector("#openPdf").addEventListener("click", () => void chrome.tabs.create({ url: translatedUrl }));
  const back = document.querySelector("#backToSource");
  if (Number.isInteger(job.originalTabId)) {
    back.addEventListener("click", async () => {
      try { await chrome.tabs.update(job.originalTabId, { active: true }); } catch { back.disabled = true; }
    });
  } else {
    back.disabled = true;
  }
  renderGuide(job.guideResult);
}

window.addEventListener("unload", () => {
  if (pdfObjectUrl) URL.revokeObjectURL(pdfObjectUrl);
});

init().catch((error) => showFatal(error instanceof Error ? error.message : String(error)));
