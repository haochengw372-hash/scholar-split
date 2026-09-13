const API_ROOT = "/api/v1";

const state = {
  route: "library",
  papers: [],
  collections: [],
  projects: [],
  recommendations: [],
  jobs: [],
  query: "",
  collectionId: "",
  libraryScope: "scholarsplit",
  tag: "",
  selectedPaperId: "",
  selectedPaperIds: new Set(),
  inspectorTab: "original",
  selectedProjectId: "",
  reviewTab: "screening",
  reviewMode: "",
  settingsTab: "general",
  loading: true,
  failures: {},
  projectDetail: new Map(),
};

const workspace = document.querySelector("#workspace");
const modal = document.querySelector("#modal");
const modalForm = document.querySelector("#modal-form");
const scrim = document.querySelector("#scrim");

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function first(value, fallback = "") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function safeHref(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value), location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.items)) return value.items;
  if (Array.isArray(value?.results)) return value.results;
  return [];
}

function normalizeAuthors(authors) {
  if (Array.isArray(authors)) {
    return authors.map((author) => typeof author === "string" ? author : first(author?.name, [author?.given, author?.family].filter(Boolean).join(" "))).filter(Boolean);
  }
  if (typeof authors === "string") return authors.split(/;|,\s(?=[A-Z\u4e00-\u9fff])/).filter(Boolean);
  return [];
}

function paperTitle(paper) {
  return first(paper?.title, "未命名文献");
}

function formatDate(value, options = { month: "short", day: "numeric" }) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function relativeTime(value) {
  if (!value) return "时间未知";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "时间未知";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  return days < 30 ? `${days} 天前` : formatDate(value, { year: "numeric", month: "short", day: "numeric" });
}

function statusText(status) {
  return ({
    running: "进行中", processing: "进行中", queued: "排队中", pending: "待处理",
    completed: "已完成", complete: "已完成", done: "已完成", failed: "失败",
    active: "进行中", paused: "已暂停", accepted: "已收录", dismissed: "已忽略",
    include: "纳入", included: "纳入", exclude: "排除", excluded: "排除",
    open: "待处理", new: "新推荐", maybe: "待讨论", uncertain: "待讨论",
    unread: "未读", reading: "在读", read: "已读", acquired: "已获取",
  })[String(status || "").toLowerCase()] || first(status, "未开始");
}

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (["completed", "complete", "done", "accepted"].includes(value)) return "complete";
  if (["failed", "error", "dismissed"].includes(value)) return "failed";
  if (["queued", "pending"].includes(value)) return "queued";
  return "running";
}

async function api(path, options = {}) {
  const { envelope = false, ...requestOptions } = options;
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: { "Accept": "application/json", ...(requestOptions.body ? { "Content-Type": "application/json" } : {}), ...requestOptions.headers },
    ...requestOptions,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(payload?.error?.message || payload?.message || `请求失败（${response.status}）`);
    error.status = response.status;
    error.code = payload?.error?.code;
    throw error;
  }
  return envelope ? payload : payload?.data ?? payload ?? null;
}

async function loadPagedResource(key, path, pageSize = 200) {
  try {
    const separator = path.includes("?") ? "&" : "?";
    const firstPage = await api(`${path}${separator}page=1&pageSize=${pageSize}`, { envelope: true });
    const items = asArray(firstPage?.data);
    const totalPages = Number(firstPage?.pagination?.totalPages || 1);
    if (totalPages > 1) {
      const remaining = await Promise.all(
        Array.from({ length: totalPages - 1 }, (_, index) =>
          api(`${path}${separator}page=${index + 2}&pageSize=${pageSize}`)
        )
      );
      remaining.forEach((page) => items.push(...asArray(page)));
    }
    state.failures[key] = null;
    return items;
  } catch (error) {
    state.failures[key] = error.message;
    return null;
  }
}

async function loadResource(key, path) {
  try {
    const result = await api(path);
    state.failures[key] = null;
    return result;
  } catch (error) {
    state.failures[key] = error.message;
    return null;
  }
}

async function loadBaseData({ quiet = false } = {}) {
  if (!quiet) {
    state.loading = true;
    render();
  }
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.collectionId) params.set("collection", state.collectionId);
  else if (state.libraryScope) params.set("scope", state.libraryScope);
  if (state.tag) params.set("tag", state.tag);
  const [papers, collections, projects, recommendations, jobs] = await Promise.all([
    loadPagedResource("papers", `/papers?${params}`),
    loadPagedResource("collections", "/collections"),
    loadPagedResource("projects", "/review-projects"),
    loadPagedResource("recommendations", "/recommendations"),
    loadPagedResource("jobs", "/jobs"),
  ]);
  if (papers !== null) state.papers = asArray(papers);
  if (collections !== null) state.collections = asArray(collections);
  if (projects !== null) state.projects = asArray(projects);
  if (recommendations !== null) state.recommendations = asArray(recommendations);
  if (jobs !== null) state.jobs = asArray(jobs);
  if (!state.selectedPaperId && state.papers.length) state.selectedPaperId = String(state.papers[0].id);
  if (!state.selectedProjectId && state.projects.length) state.selectedProjectId = String(state.projects[0].id);
  state.loading = false;
  updateChrome();
  render();
}

async function checkHealth() {
  const node = document.querySelector("#service-status");
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error();
    node.className = "service-status online";
    node.querySelector("small").textContent = "已连接 · 数据仅存本机";
  } catch {
    node.className = "service-status offline";
    node.querySelector("small").textContent = "暂未连接";
  }
}

function updateChrome() {
  document.querySelectorAll("[data-route]").forEach((link) => link.classList.toggle("active", link.dataset.route === state.route));
  const activeJobs = state.jobs.filter((job) => ["running", "processing", "queued", "pending"].includes(String(job.status).toLowerCase())).length;
  const count = document.querySelector("#task-count");
  count.hidden = activeJobs === 0;
  count.textContent = activeJobs;
  renderSidebarContext();
}

function renderSidebarContext() {
  const node = document.querySelector("#sidebar-context");
  if (!node) return;
  const tagCounts = new Map();
  state.papers.forEach((paper) => asArray(paper.tags).forEach((tag) => {
    const name = typeof tag === "string" ? tag : tag?.name;
    if (name) tagCounts.set(name, (tagCounts.get(name) || 0) + 1);
  }));
  const zoteroCollections = state.collections.filter((collection) => collection.metadata?.source === "zotero");
  const localCollections = state.collections.filter((collection) => collection.metadata?.source !== "zotero");
  const collectionRows = (items, scope) => items.slice(0,18).map((collection) => `<button class="context-row ${String(collection.id) === String(state.collectionId) ? "active" : ""}" data-action="select-collection" data-scope="${scope}" data-id="${escapeHtml(collection.id)}"><span>${escapeHtml(first(collection.name, "未命名分类"))}</span><small>${escapeHtml(first(collection.count, collection.paper_count || ""))}</small></button>`).join("");
  node.innerHTML = `<section><header><span>ScholarSplit 文献库</span><button type="button" data-action="new-collection" aria-label="新建 ScholarSplit 分类">＋</button></header><button class="context-row ${!state.collectionId && state.libraryScope === "scholarsplit" ? "active" : ""}" data-action="select-collection" data-scope="scholarsplit" data-id=""><span>插件阅读记录</span><small>${state.libraryScope === "scholarsplit" && !state.collectionId ? state.papers.length : ""}</small></button>${collectionRows(localCollections, "scholarsplit") || `<p class="context-empty">Chrome 翻译完成后自动进入这里</p>`}</section><section><header><span>Zotero 文献库</span><button type="button" data-action="sync" aria-label="同步 Zotero">↻</button></header><button class="context-row ${!state.collectionId && state.libraryScope === "zotero" ? "active" : ""}" data-action="select-collection" data-scope="zotero" data-id=""><span>全部 Zotero 条目</span></button>${collectionRows(zoteroCollections, "") || `<p class="context-empty">连接 Zotero 后显示文件夹</p>`}</section><section><header><span>旧翻译归档</span></header><button class="context-row ${!state.collectionId && state.libraryScope === "legacy" ? "active" : ""}" data-action="select-collection" data-scope="legacy" data-id=""><span>历史迁移记录</span></button></section><section><header><span>ScholarSplit 标签</span></header>${[...tagCounts.entries()].sort((a,b) => b[1] - a[1]).slice(0,10).map(([name,count]) => `<button class="context-row" data-action="filter-tag" data-tag="${escapeHtml(name)}"><span>${escapeHtml(name)}</span><small>${count}</small></button>`).join("") || `<p class="context-empty">暂无标签</p>`}</section>`;
}

function pageHead(kicker, title, lede = "", action = "") {
  const today = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date());
  return `<header class="page-head"><div><p class="eyebrow">${escapeHtml(kicker)}</p><h1>${escapeHtml(title)}</h1>${lede ? `<p class="lede">${escapeHtml(lede)}</p>` : ""}</div>${action || `<time class="date-stamp">${escapeHtml(today)}</time>`}</header>`;
}

function emptyState(title, body, action = "") {
  return `<div class="empty-state"><div class="empty-mark" aria-hidden="true">∅</div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(body)}</p>${action}</div>`;
}

function errorState(message, resource = "数据") {
  return `<div class="error-state"><div class="empty-mark" aria-hidden="true">!</div><h2>${escapeHtml(resource)}暂时无法读取</h2><p>${escapeHtml(message || "请确认本地服务正在运行，然后重试。")}</p><button class="secondary-button" type="button" data-action="reload">重新加载</button></div>`;
}

function loadingState() {
  return `<div class="initial-loader" aria-label="加载中"><span></span><span></span><span></span><p>正在整理数据…</p></div>`;
}

function renderHome() {
  const latestPaper = [...state.papers].sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))[0];
  const activeProjects = state.projects.filter((project) => !["completed", "archived"].includes(String(project.status).toLowerCase()));
  const activeJobs = state.jobs.filter((job) => ["running", "processing", "queued", "pending"].includes(String(job.status).toLowerCase()));
  const unreadRecs = state.recommendations.filter((item) => !["accepted", "dismissed", "acquired"].includes(String(item.status).toLowerCase()));
  return `<section class="view">
    ${pageHead("RESEARCH DESK / 研究桌面", "把分散的阅读，变成可追溯的论证。", "从文献进入问题，从证据抵达综合；所有过程留在本机。")}
    <div class="hero-grid">
      ${latestPaper ? `<article class="hero-panel"><p class="eyebrow">CONTINUE READING</p><h2>${escapeHtml(paperTitle(latestPaper))}</h2><p>${escapeHtml(normalizeAuthors(latestPaper.authors).join("、") || first(latestPaper.venue, "作者信息待补充"))}</p><footer><button class="primary-button" data-action="open-paper" data-id="${escapeHtml(latestPaper.id)}">继续阅读</button><span class="reading-meta">${escapeHtml(relativeTime(latestPaper.updated_at || latestPaper.created_at))}</span></footer></article>` : `<article class="hero-panel"><p class="eyebrow">BEGIN A READING TRAIL</p><h2>你的下一条证据链，从第一篇文献开始。</h2><p>添加本地文献，ScholarSplit 会在阅读、筛选与综合之间保留上下文。</p><footer><button class="primary-button" data-action="add-paper">添加首篇文献</button></footer></article>`}
      <section class="metric-rail" aria-label="研究概览">
        <div class="metric"><span>文献总数</span><strong>${state.papers.length}</strong><small>本地收录</small></div>
        <div class="metric"><span>活跃综述</span><strong>${activeProjects.length}</strong><small>项目</small></div>
        <div class="metric"><span>待处理任务</span><strong>${activeJobs.length}</strong><small>队列</small></div>
        <div class="metric"><span>新推荐</span><strong>${unreadRecs.length}</strong><small>可审阅</small></div>
      </section>
    </div>
    <section class="section"><header class="section-head"><h2>正在推进的综述</h2><button class="text-button" data-route-go="reviews">查看全部</button></header>
      ${state.failures.projects ? errorState(state.failures.projects, "综述项目") : activeProjects.length ? `<div class="card-grid">${activeProjects.slice(0,3).map(projectCard).join("")}</div>` : emptyState("还没有综述项目", "建立探索式或系统式综述，把筛选、证据矩阵与综合放在同一条工作流里。", `<button class="secondary-button" data-action="new-project">新建综述项目</button>`) }
    </section>
    <section class="section"><header class="section-head"><h2>推荐阅读</h2><p>每条推荐都附有进入视野的理由</p></header>
      ${state.failures.recommendations ? errorState(state.failures.recommendations, "推荐") : unreadRecs.length ? `<div class="card-grid">${unreadRecs.slice(0,3).map(recommendationCard).join("")}</div>` : emptyState("暂时没有新推荐", "当综述项目积累问题、成员或证据后，相关文献会出现在这里。")}
    </section>
  </section>`;
}

function projectCard(project) {
  const mode = project.protocol?.mode || project.mode || "exploratory";
  const counts = project.counts || project.stats || {};
  const included = Number(counts.included ?? project.included_count ?? 0);
  const total = Number(counts.total ?? project.paper_count ?? 0);
  const progress = total ? Math.round((included / total) * 100) : Number(project.progress || 0);
  return `<article class="project-card"><div class="card-kicker"><span>${mode === "systematic" ? "系统式综述" : "探索式综述"}</span><span class="status-pill ${statusClass(project.status)}">${escapeHtml(statusText(project.status))}</span></div><h3>${escapeHtml(first(project.name, project.title || "未命名综述"))}</h3><p>${escapeHtml(first(project.description, "尚未添加项目说明。"))}</p><div class="tag-row">${asArray(project.gaps || project.gap_labels).slice(0,3).map((gap) => `<span class="tag gap">${escapeHtml(typeof gap === "string" ? gap : gap.label || gap.title)}</span>`).join("")}</div><footer class="card-footer"><div class="progress-track"><i style="width:${Math.max(0, Math.min(100, progress))}%"></i></div><button class="text-button" data-action="open-project" data-id="${escapeHtml(project.id)}">打开</button></footer></article>`;
}

function recommendationCard(item) {
  const reason = item.reason || item.body || item.rationale;
  return `<article class="recommendation-card ${item.status === "dismissed" ? "dismissed" : ""}"><div class="card-kicker"><span>${escapeHtml(first(item.kind, "相关文献"))}</span><span>${escapeHtml(first(item.priority, ""))}</span></div><h3>${escapeHtml(first(item.title, item.paper?.title || "未命名推荐"))}</h3>${item.authors || item.paper?.authors ? `<p>${escapeHtml(normalizeAuthors(item.authors || item.paper.authors).join("、"))}</p>` : ""}${reason ? `<p class="reason"><strong>推荐理由：</strong>${escapeHtml(reason)}</p>` : ""}<footer class="card-footer"><span class="status-pill ${statusClass(item.status)}">${escapeHtml(statusText(item.status))}</span><div class="table-actions"><button data-action="dismiss-recommendation" data-id="${escapeHtml(item.id)}">忽略</button><button data-action="acquire-recommendation" data-id="${escapeHtml(item.id)}">获取全文</button></div></footer></article>`;
}

function artifactUrl(artifact, preview = true) {
  const filename = String(artifact?.path || "").split(/[\\/]/).pop();
  return filename ? `/translatedFile/${encodeURIComponent(filename)}${preview ? "?preview=true" : ""}` : "";
}

function preferredArtifact(paper, kind) {
  const artifacts = asArray(paper?.artifacts);
  if (kind === "original") return artifacts.find((item) => item.kind === "original_pdf");
  if (kind === "translation") return artifacts.find((item) => item.kind === "translated_pdf" && /compare/i.test(item.path)) || artifacts.find((item) => item.kind === "translated_pdf");
  return artifacts.find((item) => item.kind === "reading_guide");
}

function renderLibrary() {
  const visiblePapers = state.papers;
  const selected = visiblePapers.find((paper) => String(paper.id) === String(state.selectedPaperId)) || visiblePapers[0];
  return `<section class="view library-view">
    ${state.failures.papers && !state.papers.length ? errorState(state.failures.papers, "文献库") : `<div class="library-workspace">
      <section class="paper-table-pane"><header class="table-toolbar"><div><strong>${state.query ? `“${escapeHtml(state.query)}”` : "全部文献"}</strong><small>${visiblePapers.length} 篇文献${state.selectedPaperIds.size ? ` · 已选 ${state.selectedPaperIds.size} 篇` : ""}</small></div><div><select class="compact-select" id="library-sort" aria-label="排序"><option value="updated">按添加时间</option><option value="year">按出版年份</option><option value="title">按标题</option></select></div></header>${visiblePapers.length ? `<div class="paper-table-scroll"><table class="paper-table"><thead><tr><th class="select-column"><input type="checkbox" data-select-all aria-label="选择全部文献" /></th><th>标题</th><th>作者</th><th>年份</th><th>阅读状态</th><th>文件</th></tr></thead><tbody>${visiblePapers.map((paper) => { const artifacts = asArray(paper.artifacts); return `<tr class="${String(paper.id) === String(selected?.id) ? "selected" : ""}" data-action="select-paper" data-id="${escapeHtml(paper.id)}" tabindex="0"><td class="select-column"><input type="checkbox" data-paper-select="${escapeHtml(paper.id)}" aria-label="选择 ${escapeHtml(paperTitle(paper))}" ${state.selectedPaperIds.has(String(paper.id)) ? "checked" : ""} /></td><td><strong>${escapeHtml(paperTitle(paper))}</strong><small>${escapeHtml(first(paper.venue, paper.doi || ""))}</small></td><td>${escapeHtml(normalizeAuthors(paper.authors).join("、") || "—")}</td><td>${escapeHtml(first(paper.year, "—"))}</td><td>${escapeHtml(statusText(paper.status))}</td><td><span class="file-state">${artifacts.some((item) => item.kind === "original_pdf") ? "原" : "—"}</span><span class="file-state">${artifacts.some((item) => item.kind === "translated_pdf") ? "译" : "—"}</span><span class="file-state">${artifacts.some((item) => item.kind === "reading_guide") ? "导" : "—"}</span></td></tr>`; }).join("")}</tbody></table></div>` : emptyState("没有匹配的文献", state.query ? "换一个关键词，或清除搜索条件。" : "导入文献后，它会出现在这里。")}</section>
      <aside class="paper-inspector">${selected ? paperDetail(selected) : emptyState("选择一篇文献", "原文、译文、导读和笔记会显示在这里。")}</aside>
    </div>`}
  </section>`;
}

function paperDetail(paper) {
  const authors = normalizeAuthors(paper.authors);
  const tags = asArray(paper.tags || paper.metadata?.tags);
  const original = preferredArtifact(paper, "original");
  const translation = preferredArtifact(paper, "translation");
  const guide = preferredArtifact(paper, "guide");
  const activeKind = state.inspectorTab || (original ? "original" : translation ? "translation" : "guide");
  const activeArtifact = activeKind === "original" ? original : activeKind === "translation" ? translation : null;
  const preview = artifactUrl(activeArtifact);
  return `<article class="inspector-content"><header><h2>${escapeHtml(paperTitle(paper))}</h2><div class="inspector-actions"><button class="secondary-button compact-button" data-action="send-to-zotero" data-id="${escapeHtml(paper.id)}">放入 Zotero 文件夹</button><button class="icon-button" data-action="edit-paper" data-id="${escapeHtml(paper.id)}" aria-label="编辑元数据">•••</button></div></header><p class="authors">${escapeHtml(authors.join("、") || "作者信息待补充")}</p><nav class="inspector-tabs" aria-label="文献内容"><button data-action="inspector-tab" data-tab="original" class="${activeKind === "original" ? "active" : ""}" ${original ? "" : "disabled"}>原文</button><button data-action="inspector-tab" data-tab="translation" class="${activeKind === "translation" ? "active" : ""}" ${translation ? "" : "disabled"}>译文</button><button data-action="inspector-tab" data-tab="guide" class="${activeKind === "guide" ? "active" : ""}" ${guide ? "" : "disabled"}>导读</button><button data-action="inspector-tab" data-tab="notes" class="${activeKind === "notes" ? "active" : ""}">笔记</button></nav>${preview ? `<iframe class="pdf-preview" src="${escapeHtml(preview)}" title="${activeKind === "original" ? "原文 PDF" : "译文 PDF"}"></iframe>` : activeKind === "guide" && guide ? `<section class="inspector-message"><strong>导读已关联</strong><p>这篇文献已有结构化导读，可加入综述项目后进入证据矩阵。</p></section>` : activeKind === "notes" ? `<section class="inspector-message"><strong>个人笔记</strong><p>尚未添加笔记。</p><button class="secondary-button" data-action="add-note" data-id="${escapeHtml(paper.id)}">添加笔记</button></section>` : `<section class="inspector-message"><strong>暂无可预览文件</strong></section>`}<dl class="inspector-meta"><div><dt>年份</dt><dd>${escapeHtml(first(paper.year, "—"))}</dd></div><div><dt>来源</dt><dd>${escapeHtml(first(paper.venue, "—"))}</dd></div><div><dt>DOI</dt><dd>${escapeHtml(first(paper.doi, "—"))}</dd></div><div><dt>状态</dt><dd>${escapeHtml(statusText(paper.status))}</dd></div></dl><section class="inspector-section"><header><strong>摘要</strong></header><p>${escapeHtml(first(paper.abstract, "暂无摘要。"))}</p></section><section class="inspector-section"><header><strong>标签</strong></header><div class="tag-row">${tags.length ? tags.map((tag) => `<span class="tag">${escapeHtml(typeof tag === "string" ? tag : tag.name)}</span>`).join("") : `<span class="muted">暂无标签</span>`}</div></section></article>`;
}

async function loadProjectDetail(projectId) {
  if (!projectId) return;
  const paths = ["members", "screening", "evidence", "syntheses", "gaps"];
  const results = await Promise.all(paths.map((name) => loadResource(`project-${projectId}-${name}`, `/review-projects/${encodeURIComponent(projectId)}/${name}?pageSize=100`)));
  state.projectDetail.set(String(projectId), Object.fromEntries(paths.map((name, index) => [name, asArray(results[index])])));
  render();
}

function renderReviews() {
  if (state.failures.projects && !state.projects.length) return `<section class="view">${pageHead("REVIEW PROJECTS", "综述项目")}${errorState(state.failures.projects, "综述项目")}</section>`;
  if (!state.projects.length) return `<section class="view">${pageHead("REVIEW PROJECTS", "综述项目", "让检索、筛选、证据抽取和综合保持同一套上下文。", `<button class="primary-button" data-action="new-project">新建项目</button>`)}${emptyState("建立第一个综述项目", "探索式模式适合快速摸清领域；系统式模式适合预先锁定标准并记录每一步决定。", `<button class="primary-button" data-action="new-project">新建综述项目</button>`)}</section>`;
  const project = state.projects.find((item) => String(item.id) === String(state.selectedProjectId)) || state.projects[0];
  const details = state.projectDetail.get(String(project.id));
  const mode = state.reviewMode || project.protocol?.mode || project.mode || "exploratory";
  return `<section class="view">
    ${pageHead("REVIEW PROJECTS", "综述项目", "筛选决定、证据来源与综合结论在项目内保持可追溯。", `<div class="table-actions"><button class="secondary-button" data-action="discover-recommendations" data-id="${escapeHtml(project.id)}">发现相关文献</button><button class="primary-button" data-action="analyze-project" data-id="${escapeHtml(project.id)}">生成综述</button></div>`)}
    <div class="review-shell">
      <aside class="project-index panel"><header><h2>项目索引</h2><small>${state.projects.length} 个项目</small></header>${state.projects.map((item) => `<button class="project-row ${String(item.id) === String(project.id) ? "active" : ""}" data-action="select-project" data-id="${escapeHtml(item.id)}"><strong>${escapeHtml(first(item.name, item.title || "未命名综述"))}</strong><small>${escapeHtml(statusText(item.status))} · ${escapeHtml(item.protocol?.mode === "systematic" || item.mode === "systematic" ? "系统式" : "探索式")}</small></button>`).join("")}</aside>
      <div class="review-main">
        <section class="review-header panel"><div class="review-title-row"><div><p class="eyebrow">${escapeHtml(String(project.id).slice(0,12))}</p><h1>${escapeHtml(first(project.name, project.title || "未命名综述"))}</h1><p class="muted">${escapeHtml(first(project.description, "尚未添加项目说明。"))}</p></div><div class="mode-switch" aria-label="综述模式"><button data-action="review-mode" data-mode="exploratory" class="${mode === "exploratory" ? "active" : ""}">探索式</button><button data-action="review-mode" data-mode="systematic" class="${mode === "systematic" ? "active" : ""}">系统式</button></div></div><div class="stage-strip"><div class="stage done"><b>01 · COLLECT</b><small>收集文献</small></div><div class="stage active"><b>02 · SCREEN</b><small>筛选判断</small></div><div class="stage"><b>03 · EXTRACT</b><small>提取证据</small></div><div class="stage"><b>04 · SYNTHESIZE</b><small>形成综合</small></div></div></section>
        <nav class="review-tabs" aria-label="项目工作区"><button data-review-tab="screening" class="${state.reviewTab === "screening" ? "active" : ""}">筛选</button><button data-review-tab="evidence" class="${state.reviewTab === "evidence" ? "active" : ""}">证据矩阵</button><button data-review-tab="synthesis" class="${state.reviewTab === "synthesis" ? "active" : ""}">综合</button><button data-review-tab="gaps" class="${state.reviewTab === "gaps" ? "active" : ""}">研究空白</button><button data-review-tab="protocol" class="${state.reviewTab === "protocol" ? "active" : ""}">方案</button></nav>
        <div class="review-content">${details ? renderReviewTab(project, details, mode) : loadingState()}</div>
      </div>
    </div>
  </section>`;
}

function renderReviewTab(project, details, mode) {
  if (state.reviewTab === "screening") {
    const records = details.screening;
    const counts = records.reduce((acc, item) => { acc[item.decision || item.status || "pending"] = (acc[item.decision || item.status || "pending"] || 0) + 1; return acc; }, {});
    return `<div class="screening-grid"><div class="screen-stat panel"><strong>${records.length}</strong><span>筛选记录</span></div><div class="screen-stat panel"><strong>${counts.include || counts.included || 0}</strong><span>纳入</span></div><div class="screen-stat panel"><strong>${counts.exclude || counts.excluded || 0}</strong><span>排除</span></div><div class="screen-stat panel"><strong>${counts.maybe || counts.uncertain || 0}</strong><span>待讨论</span></div></div>${records.length ? `<div class="screening-list panel"><table class="data-table"><thead><tr><th>文献</th><th>阶段</th><th>决定</th><th>理由</th><th>操作</th></tr></thead><tbody>${records.map((item) => `<tr><td>${escapeHtml(first(item.title, item.paper?.title || state.papers.find((paper) => String(paper.id) === String(item.paper_id))?.title || item.paper_id || "未命名文献"))}</td><td>${escapeHtml(first(item.stage, mode === "systematic" ? "title_abstract" : "discovery"))}</td><td><span class="status-pill ${statusClass(item.decision || item.status)}">${escapeHtml(statusText(item.decision || item.status))}</span></td><td>${escapeHtml(first(item.reason, item.note || "—"))}</td><td><div class="table-actions"><button data-action="screen" data-id="${escapeHtml(item.id)}" data-project="${escapeHtml(project.id)}" data-decision="include">纳入</button><button data-action="screen" data-id="${escapeHtml(item.id)}" data-project="${escapeHtml(project.id)}" data-decision="exclude">排除</button></div></td></tr>`).join("")}</tbody></table></div>` : emptyState("筛选队列为空", "把候选文献加入项目后，可在这里记录纳入、排除及理由。")}`;
  }
  if (state.reviewTab === "evidence") {
    const evidence = details.evidence;
    return evidence.length ? `<div class="panel screening-list"><table class="data-table"><thead><tr><th>文献</th><th>研究问题 / 构念</th><th>方法与样本</th><th>关键发现</th><th>证据位置</th></tr></thead><tbody>${evidence.map((item) => `<tr><td>${escapeHtml(first(item.title, item.paper?.title || state.papers.find((paper) => String(paper.id) === String(item.paper_id))?.title || item.paper_id || "未命名文献"))}</td><td class="matrix-cell">${escapeHtml(first(item.constructs, item.research_question || item.question || item.theme || "—"))}</td><td class="matrix-cell">${escapeHtml(first(item.methods, item.method || item.sample || "—"))}</td><td class="matrix-cell">${escapeHtml(first(item.finding, item.findings || item.claim || "—"))}</td><td class="matrix-cell">${escapeHtml(first(item.location, item.locator || item.page || item.quote || "—"))}</td></tr>`).join("")}</tbody></table></div>` : emptyState("证据矩阵还没有条目", "从已纳入文献中提取研究问题、方法、发现与精确位置。");
  }
  if (state.reviewTab === "synthesis") {
    const syntheses = details.syntheses;
    const gaps = details.gaps;
    return `<div class="synthesis-grid"><section class="synthesis-block panel"><p class="eyebrow">EVIDENCE SYNTHESIS</p><h3>综合笔记</h3>${syntheses.length ? `<ul>${syntheses.map((item) => `<li><strong>${escapeHtml(first(item.title, item.theme || "综合条目"))}</strong><br><span class="muted">${escapeHtml(first(item.body, item.summary || item.claim || ""))}</span></li>`).join("")}</ul>` : `<p class="muted">证据条目积累后，在这里汇总一致结论、分歧与边界条件。</p>`}</section><section class="synthesis-block panel"><p class="eyebrow">OPEN QUESTIONS</p><h3>尚未闭合的论证</h3>${gaps.length ? `<div class="gap-list">${gaps.slice(0,5).map(gapItem).join("")}</div>` : `<p class="muted">目前没有标记开放问题。</p>`}</section></div>`;
  }
  if (state.reviewTab === "gaps") {
    return details.gaps.length ? `<div class="gap-list">${details.gaps.map(gapItem).join("")}</div>` : emptyState("尚未标记研究空白", "研究空白应落在明确的对象、机制、情境、测量或方法上，而不只是“研究较少”。") ;
  }
  const protocol = project.protocol || {};
  return `<section class="settings-panel panel"><p class="eyebrow">${mode.toUpperCase()} PROTOCOL</p><h2>${mode === "systematic" ? "系统式综述方案" : "探索式综述路线"}</h2><p>${mode === "systematic" ? "方案字段帮助你在筛选前锁定范围与判断规则。" : "探索模式允许问题随阅读迭代，但每次调整仍保留理由。"}</p><div class="form-row"><label><strong>研究问题</strong><small>项目试图回答什么</small></label><div>${escapeHtml(first(protocol.question, protocol.research_question || "尚未填写"))}</div></div><div class="form-row"><label><strong>纳入标准</strong><small>什么样的证据进入综合</small></label><div>${escapeHtml(first(protocol.inclusion_criteria, protocol.inclusion || "尚未填写"))}</div></div><div class="form-row"><label><strong>排除标准</strong><small>记录边界与理由</small></label><div>${escapeHtml(first(protocol.exclusion_criteria, protocol.exclusion || "尚未填写"))}</div></div></section>`;
}

function gapItem(item) {
  const label = typeof item === "object" ? first(item.label, item.metadata?.label || item.title || item.kind) : item;
  const description = typeof item === "object" ? first(item.body, item.description || item.metadata?.description || item.evidence || "") : "";
  const level = typeof item === "object" ? first(item.classification, item.metadata?.classification || "未验证") : "";
  return `<article class="gap-item"><div><strong>${escapeHtml(first(label, "待命名空白"))}</strong>${level ? `<em>${escapeHtml(level)}</em>` : ""}</div>${description ? `<span>${escapeHtml(description)}</span>` : ""}</article>`;
}

function renderReading() {
  const papers = [...state.papers].sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
  const recommendations = state.recommendations.filter((item) => !["dismissed", "acquired"].includes(String(item.status).toLowerCase()));
  const recent = papers.length ? `<div class="card-grid">${papers.slice(0,8).map((paper) => `<article class="paper-card"><div class="card-kicker"><span>${escapeHtml(first(paper.venue, "LIBRARY"))}</span><span>${escapeHtml(first(paper.year, ""))}</span></div><h3>${escapeHtml(paperTitle(paper))}</h3><p>${escapeHtml(normalizeAuthors(paper.authors).join("、") || "作者信息待补充")}</p><footer class="card-footer"><span class="muted">${escapeHtml(relativeTime(paper.updated_at || paper.created_at))}</span><button class="text-button" data-action="open-paper" data-id="${escapeHtml(paper.id)}">继续</button></footer></article>`).join("")}</div>` : emptyState("没有阅读记录", "从文献库打开一篇文献后，最近阅读会汇集在这里。", `<button class="secondary-button" data-route-go="library">前往文献库</button>`);
  const suggestions = recommendations.length ? `<div class="card-grid">${recommendations.slice(0,12).map(recommendationCard).join("")}</div>` : emptyState("暂无阅读建议", "进入综述项目，围绕研究问题发现相关文献。");
  return `<section class="view">${pageHead("READING TRAILS", "继续阅读", "返回最近阅读，也查看由项目问题、证据缺口与本地遗漏文献产生的建议。")}<section class="reading-section"><div class="section-heading"><div><p class="eyebrow">RECENTLY READ</p><h2>最近阅读</h2></div></div>${recent}</section><section class="reading-section"><div class="section-heading"><div><p class="eyebrow">RECOMMENDATIONS</p><h2>继续阅读建议</h2></div><span class="muted">${recommendations.length} 条</span></div>${suggestions}</section></section>`;
}

function renderTasks() {
  return `<section class="view">${pageHead("LOCAL TASK QUEUE", "任务", "翻译、导读、导入和全文获取都在本地任务队列中留下状态。", `<button class="quiet-button" data-action="reload">刷新状态</button>`)}${state.failures.jobs && !state.jobs.length ? errorState(state.failures.jobs, "任务") : state.jobs.length ? `<div class="task-list">${state.jobs.map((job) => `<article class="task-row panel"><div class="task-symbol" aria-hidden="true">${job.status === "failed" ? "!" : job.status === "completed" ? "✓" : "↻"}</div><div><h3>${escapeHtml(first(job.title, job.kind || "后台任务"))}</h3><p>${escapeHtml(first(job.message, job.error || job.payload?.filename || job.payload?.title || "正在处理本地数据"))}</p>${Number.isFinite(Number(job.progress)) ? `<div class="progress-track ${job.status === "completed" ? "sage" : ""}" aria-label="${Math.round(Number(job.progress))}%"><i style="width:${Math.max(0, Math.min(100, Number(job.progress)))}%"></i></div>` : ""}</div><div><span class="status-pill ${statusClass(job.status)}">${escapeHtml(statusText(job.status))}</span><p class="task-time">${escapeHtml(relativeTime(job.updated_at || job.created_at))}</p></div></article>`).join("")}</div>` : emptyState("任务队列是空的", "没有正在运行或已记录的后台任务。")}</section>`;
}

function renderSettings() {
  const tabs = [{id:"general",label:"常规"},{id:"sources",label:"数据来源"},{id:"privacy",label:"隐私与存储"}];
  return `<section class="view">${pageHead("PREFERENCES", "设置", "配置这台设备上的研究工作台。敏感凭据不会在网页中显示。")}<div class="settings-grid"><nav class="settings-nav panel" aria-label="设置分类">${tabs.map((tab) => `<button class="${state.settingsTab === tab.id ? "active" : ""}" data-settings-tab="${tab.id}">${tab.label}</button>`).join("")}</nav><section class="settings-panel panel">${renderSettingsPanel()}</section></div></section>`;
}

function renderSettingsPanel() {
  if (state.settingsTab === "sources") return `<p class="eyebrow">DATA SOURCES</p><h2>数据来源</h2><p>管理本地文件与 Zotero 的连接状态。</p><div class="form-row"><label><strong>本地资料夹</strong><small>扫描前会先生成预览，不会静默导入</small></label><button class="secondary-button" data-action="scan-folder">扫描资料夹</button></div><div class="form-row"><label><strong>Zotero</strong><small>通过受控命令队列同步条目</small></label><button class="secondary-button" data-action="check-zotero">检查连接</button></div>`;
  if (state.settingsTab === "privacy") return `<p class="eyebrow">LOCAL FIRST</p><h2>隐私与存储</h2><p>ScholarSplit 默认只连接回环地址，研究资料保存在本机。</p><div class="form-row"><label><strong>匿名使用统计</strong><small>当前版本不包含遥测</small></label><span class="status-pill complete">已关闭</span></div><div class="form-row"><label><strong>模型凭据</strong><small>仅由本地服务保存，网页不会读取或显示</small></label><span class="status-pill complete">本机隔离</span></div>`;
  return `<p class="eyebrow">WORKBENCH</p><h2>常规</h2><p>调整研究桌面的默认行为。</p><div class="form-row"><label for="default-mode"><strong>默认综述模式</strong><small>新建项目时预先选择</small></label><select id="default-mode" class="form-control"><option value="exploratory">探索式综述</option><option value="systematic">系统式综述</option></select></div><div class="form-row"><label for="auto-refresh"><strong>自动刷新任务</strong><small>工作台打开时同步本地状态</small></label><label class="switch"><input id="auto-refresh" type="checkbox" checked /><span>开启</span></label></div>`;
}

function renderSearchResults() {
  return renderLibrary();
}

function render() {
  updateChrome();
  workspace.classList.toggle("library-mode", state.route === "library");
  if (state.loading) {
    workspace.innerHTML = loadingState();
    return;
  }
  const views = { library: renderLibrary, reviews: renderReviews, reading: renderReading, tasks: renderTasks, settings: renderSettings };
  workspace.innerHTML = (views[state.route] || renderLibrary)();
  if (state.route === "reviews" && state.selectedProjectId && !state.projectDetail.has(String(state.selectedProjectId))) loadProjectDetail(state.selectedProjectId);
}

function navigate(route) {
  state.route = route || "library";
  if (location.hash !== `#${state.route}`) history.pushState(null, "", `#${state.route}`);
  closeNav();
  render();
  workspace.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function toast(message, kind = "success") {
  const region = document.querySelector("#toast-region");
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  region.append(node);
  setTimeout(() => node.remove(), 4200);
}

function openModal({ eyebrow = "SCHOLARSPLIT", title, content, submitLabel = "保存", onSubmit }) {
  document.querySelector("#modal-eyebrow").textContent = eyebrow;
  document.querySelector("#modal-title").textContent = title;
  document.querySelector("#modal-content").innerHTML = content;
  document.querySelector("#modal-actions").innerHTML = `<button class="quiet-button" value="cancel">取消</button><button class="primary-button" value="submit" id="modal-submit">${escapeHtml(submitLabel)}</button>`;
  modalForm.onsubmit = async (event) => {
    event.preventDefault();
    const submitter = event.submitter;
    if (!submitter || submitter.value === "cancel") { modal.close(); return; }
    submitter.disabled = true;
    try { await onSubmit(new FormData(modalForm)); modal.close(); }
    catch (error) { toast(error.message, "error"); submitter.disabled = false; }
  };
  modal.showModal();
}

async function addPaperDialog() {
  try {
    const scan = await api("/library/scan", { method: "POST", body: JSON.stringify({ commit: false }) });
    const matches = asArray(scan?.matches);
    const groups = asArray(scan?.groups);
    const ambiguous = asArray(scan?.ambiguous);
    const unmatched = asArray(scan?.unmatched);
    openModal({
      title: "导入现有研究资料",
      eyebrow: "LIBRARY INTAKE PREVIEW",
      submitLabel: groups.length ? `确认索引 ${groups.length} 篇` : "没有可索引项目",
      content: `<p class="muted">工作台只建立本地索引，不移动或改写任何原文、译文和导读文件。</p><div class="meta-grid"><div><small>文献组</small>${groups.length}</div><div><small>已关联导读</small>${matches.length}</div><div><small>待关联导读</small>${unmatched.length}</div><div><small>歧义</small>${ambiguous.length}</div></div>${groups.length ? `<div class="modal-field"><label>扫描样例</label>${groups.slice(0,8).map((item) => `<small>${escapeHtml(first(item.canonical_name, "本地文献"))}</small>`).join("")}</div>` : `<p>当前目录没有可索引的 PDF。</p>`}`,
      onSubmit: async () => {
        if (!groups.length) return;
        const result = await api("/library/scan", { method: "POST", body: JSON.stringify({ commit: true }) });
        toast(`已索引 ${first(result?.importedCount, groups.length)} 篇本地文献`);
        await loadBaseData({ quiet: true });
      },
    });
    document.querySelector("#modal-submit").disabled = groups.length === 0;
  } catch (error) {
    toast(error.message, "error");
  }
}

function newProjectDialog() {
  openModal({ title: "新建综述项目", eyebrow: "REVIEW PROTOCOL", submitLabel: "创建项目", content: `<div class="modal-field"><label for="project-name">项目名称</label><input id="project-name" name="name" required autofocus /></div><div class="modal-field"><label for="project-description">研究目标</label><textarea id="project-description" name="description" placeholder="这个综述试图解释或厘清什么？"></textarea></div><div class="modal-field"><label for="project-mode">综述模式</label><select id="project-mode" name="mode"><option value="exploratory">探索式 · 允许问题随阅读迭代</option><option value="systematic">系统式 · 预先锁定方案与标准</option></select></div>`, onSubmit: async (form) => {
    const body = { name: form.get("name").trim(), description: form.get("description").trim(), status: "active", protocol: { mode: form.get("mode") } };
    const created = await api("/review-projects", { method: "POST", body: JSON.stringify(body) });
    state.selectedProjectId = String(created?.id || "");
    if (state.selectedProjectId && state.selectedPaperIds.size) {
      await Promise.all([...state.selectedPaperIds].map((paperId) =>
        api(`/review-projects/${encodeURIComponent(state.selectedProjectId)}/members`, { method: "POST", body: JSON.stringify({ paper_id: paperId, stage: "imported", status: "pending" }) })
      ));
      state.selectedPaperIds.clear();
    }
    state.projectDetail.delete(state.selectedProjectId);
    toast("综述项目已创建");
    await loadBaseData({ quiet: true });
    navigate("reviews");
  }});
}

function newCollectionDialog() {
  openModal({ title: "新建分类", eyebrow: "LIBRARY COLLECTION", submitLabel: "创建分类", content: `<div class="modal-field"><label for="collection-name">分类名称</label><input id="collection-name" name="name" required autofocus /></div><div class="modal-field"><label for="collection-description">说明</label><textarea id="collection-description" name="description"></textarea></div>`, onSubmit: async (form) => {
    const created = await api("/collections", { method: "POST", body: JSON.stringify({ name: form.get("name").trim(), description: form.get("description").trim(), metadata: { source: "scholarsplit" } }) });
    state.collectionId = String(created?.id || "");
    state.libraryScope = "scholarsplit";
    toast("分类已创建");
    await loadBaseData({ quiet: true });
  }});
}

function editPaperDialog(paper) {
  openModal({ title: "编辑文献元数据", eyebrow: "LIBRARY RECORD", submitLabel: "保存更改", content: `<div class="modal-field"><label for="paper-title">标题</label><input id="paper-title" name="title" value="${escapeHtml(paper.title)}" required autofocus /></div><div class="modal-field"><label for="paper-authors">作者</label><input id="paper-authors" name="authors" value="${escapeHtml(normalizeAuthors(paper.authors).join("; "))}" /></div><div class="modal-field"><label for="paper-abstract">摘要</label><textarea id="paper-abstract" name="abstract">${escapeHtml(paper.abstract)}</textarea></div>`, onSubmit: async (form) => {
    await api(`/papers/${encodeURIComponent(paper.id)}`, { method: "PATCH", body: JSON.stringify({ title: form.get("title").trim(), authors: form.get("authors").split(";").map((value) => value.trim()).filter(Boolean), abstract: form.get("abstract").trim() }) });
    toast("元数据已更新");
    await loadBaseData({ quiet: true });
  }});
}

function addNoteDialog(paperId) {
  openModal({ title: "添加笔记", eyebrow: "RESEARCH NOTE", submitLabel: "保存笔记", content: `<div class="modal-field"><label for="note-title">标题</label><input id="note-title" name="title" /></div><div class="modal-field"><label for="note-body">内容</label><textarea id="note-body" name="body" required autofocus></textarea></div>`, onSubmit: async (form) => {
    await api("/notes", { method: "POST", body: JSON.stringify({ paper_id: paperId, title: form.get("title").trim(), body: form.get("body").trim(), kind: "note" }) });
    toast("笔记已保存");
  }});
}

function zoteroFolderDialog(paperId) {
  const folders = state.collections.filter((collection) => collection.metadata?.source === "zotero");
  if (!folders.length) {
    toast("还没有同步到 Zotero 文件夹，请先点击顶部“同步”", "error");
    return;
  }
  openModal({
    title: "放入 Zotero 文件夹",
    eyebrow: "ZOTERO DESTINATION",
    submitLabel: "加入同步队列",
    content: `<p class="muted">已有 Zotero 条目会直接归入文件夹；仅在 ScholarSplit 中的 PDF 会先作为新条目导入 Zotero。</p><div class="modal-field"><label for="zotero-collection">目标文件夹</label><select id="zotero-collection" name="collectionId" required autofocus>${folders.map((folder) => `<option value="${escapeHtml(folder.id)}">${escapeHtml(first(folder.name, "未命名文件夹"))}</option>`).join("")}</select></div>`,
    onSubmit: async (form) => {
      await api(`/papers/${encodeURIComponent(paperId)}/zotero-collections`, {
        method: "POST",
        body: JSON.stringify({ collectionId: form.get("collectionId") }),
      });
      toast("已加入 Zotero 同步队列");
      await loadBaseData({ quiet: true });
    },
  });
}

async function handleAction(target) {
  const action = target.dataset.action;
  if (!action) return;
  if (action === "reload") return loadBaseData();
  if (action === "add-paper") return addPaperDialog();
  if (action === "new-project") return newProjectDialog();
  if (action === "new-collection") return newCollectionDialog();
  if (action === "select-paper") { state.selectedPaperId = target.dataset.id; state.inspectorTab = "original"; return render(); }
  if (action === "open-paper") { state.selectedPaperId = target.dataset.id; return navigate("library"); }
  if (action === "edit-paper") return editPaperDialog(state.papers.find((paper) => String(paper.id) === target.dataset.id));
  if (action === "select-collection") { state.collectionId = target.dataset.id; state.libraryScope = target.dataset.scope ?? ""; state.tag = ""; state.selectedPaperId = ""; return loadBaseData(); }
  if (action === "filter-tag") { state.tag = target.dataset.tag; state.collectionId = ""; state.libraryScope = "scholarsplit"; state.selectedPaperId = ""; return loadBaseData(); }
  if (action === "inspector-tab") { state.inspectorTab = target.dataset.tab; return render(); }
  if (action === "add-note") return addNoteDialog(target.dataset.id);
  if (action === "send-to-zotero") return zoteroFolderDialog(target.dataset.id);
  if (action === "open-project" || action === "select-project") {
    state.selectedProjectId = target.dataset.id;
    state.reviewMode = "";
    state.projectDetail.delete(String(target.dataset.id));
    if (state.route !== "reviews") navigate("reviews"); else render();
    return;
  }
  if (action === "review-mode") {
    state.reviewMode = target.dataset.mode;
    const project = state.projects.find((item) => String(item.id) === String(state.selectedProjectId));
    if (project) {
      try {
        await api(`/review-projects/${encodeURIComponent(project.id)}`, { method: "PATCH", body: JSON.stringify({ protocol: { ...(project.protocol || {}), mode: state.reviewMode } }) });
        project.protocol = { ...(project.protocol || {}), mode: state.reviewMode };
      }
      catch (error) { toast(`模式仅在当前界面切换：${error.message}`, "error"); }
    }
    return render();
  }
  if (action === "analyze-project") {
    target.disabled = true;
    try {
      await api(`/review-projects/${encodeURIComponent(target.dataset.id)}/analyze`, { method: "POST", body: "{}" });
      toast("综述任务已开始，可在任务页查看进度");
      await loadBaseData({ quiet: true });
    } catch (error) { toast(error.message, "error"); target.disabled = false; }
    return;
  }
  if (action === "discover-recommendations") {
    const projectId = target.dataset.id;
    openModal({ title: "发现相关文献", eyebrow: "OPEN METADATA SEARCH", submitLabel: "开始检索", content: `<div class="modal-field"><label for="discovery-query">检索主题或研究空白</label><textarea id="discovery-query" name="query" required autofocus></textarea><small>先检索开放元数据；获取全文仍需单独确认。</small></div>`, onSubmit: async (form) => {
      const rows = await api(`/review-projects/${encodeURIComponent(projectId)}/recommendations/discover`, { method: "POST", body: JSON.stringify({ query: form.get("query").trim(), limit: 12 }) });
      toast(`已找到 ${asArray(rows).length} 条候选文献`);
      await loadBaseData({ quiet: true });
      navigate("reading");
    }});
    return;
  }
  if (action === "screen") {
    try {
      await api(`/review-projects/${encodeURIComponent(target.dataset.project)}/screening/${encodeURIComponent(target.dataset.id)}`, { method: "PATCH", body: JSON.stringify({ decision: target.dataset.decision }) });
      state.projectDetail.delete(String(target.dataset.project));
      toast(target.dataset.decision === "include" ? "已纳入文献" : "已记录排除决定");
      render();
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (action === "acquire-recommendation") {
    target.disabled = true;
    try {
      const preview = await api("/acquisitions/preview", { method: "POST", body: JSON.stringify({ recommendationId: target.dataset.id }) });
      openModal({ title: "确认全文获取", eyebrow: "ACQUISITION PREVIEW", submitLabel: "确认并加入队列", content: `<p>系统将根据这条推荐尝试获取可合法访问的全文。确认前不会执行获取。</p><div class="meta-grid"><div><small>推荐编号</small>${escapeHtml(target.dataset.id)}</div><div><small>需要确认</small>${preview?.requiresConfirmation === false ? "否" : "是"}</div></div>`, onSubmit: async () => {
        await api("/acquisitions/confirm", { method: "POST", body: JSON.stringify({ recommendationId: target.dataset.id, confirmed: true }) });
        toast("已加入全文获取队列");
        await loadBaseData({ quiet: true });
      }});
    }
    catch (error) { toast(error.message, "error"); target.disabled = false; }
    return;
  }
  if (action === "dismiss-recommendation") {
    target.disabled = true;
    try { await api(`/recommendations/${encodeURIComponent(target.dataset.id)}`, { method: "PATCH", body: JSON.stringify({ status: "dismissed" }) }); toast("推荐已忽略"); await loadBaseData({ quiet: true }); }
    catch (error) { toast(error.message, "error"); target.disabled = false; }
    return;
  }
  if (action === "sync" || action === "check-zotero") {
    await loadBaseData({ quiet: true });
    toast("Zotero 由本地配对桥接器连接；工作台不会读取或显示配对凭据。", "success");
    return;
  }
  if (action === "scan-folder") {
    return addPaperDialog();
  }
}

function openNav() {
  document.body.classList.add("nav-open");
  scrim.hidden = false;
  document.querySelector("#menu-toggle").setAttribute("aria-expanded", "true");
}
function closeNav() {
  document.body.classList.remove("nav-open");
  scrim.hidden = true;
  document.querySelector("#menu-toggle").setAttribute("aria-expanded", "false");
}

document.addEventListener("change", (event) => {
  const paperSelector = event.target.closest("[data-paper-select]");
  if (paperSelector) {
    const id = String(paperSelector.dataset.paperSelect);
    if (paperSelector.checked) state.selectedPaperIds.add(id);
    else state.selectedPaperIds.delete(id);
    render();
    return;
  }
  if (event.target.matches("[data-select-all]")) {
    state.selectedPaperIds.clear();
    if (event.target.checked) state.papers.forEach((paper) => state.selectedPaperIds.add(String(paper.id)));
    render();
  }
});

document.addEventListener("click", (event) => {
  const routeLink = event.target.closest("[data-route]");
  if (routeLink) { event.preventDefault(); navigate(routeLink.dataset.route); return; }
  const routeButton = event.target.closest("[data-route-go]");
  if (routeButton) { navigate(routeButton.dataset.routeGo); return; }
  const actionTarget = event.target.closest("[data-action]");
  if (actionTarget) { handleAction(actionTarget); return; }
  const reviewTab = event.target.closest("[data-review-tab]");
  if (reviewTab) { state.reviewTab = reviewTab.dataset.reviewTab; render(); return; }
  const settingsTab = event.target.closest("[data-settings-tab]");
  if (settingsTab) { state.settingsTab = settingsTab.dataset.settingsTab; render(); }
});

document.querySelector("#global-search").addEventListener("submit", (event) => {
  event.preventDefault();
  state.query = new FormData(event.currentTarget).get("q").trim();
  state.collectionId = "";
  state.selectedPaperId = "";
  state.route = "library";
  history.pushState(null, "", "#library");
  loadBaseData();
});

document.querySelector("#search-input").addEventListener("search", (event) => {
  if (!event.target.value && state.query) {
    state.query = "";
    state.selectedPaperId = "";
    loadBaseData();
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    document.querySelector("#search-input").focus();
  }
  if (event.key === "Escape") closeNav();
});

document.querySelector("#add-paper-button").addEventListener("click", addPaperDialog);
document.querySelector("#menu-toggle").addEventListener("click", () => document.body.classList.contains("nav-open") ? closeNav() : openNav());
scrim.addEventListener("click", closeNav);
window.addEventListener("hashchange", () => { state.route = location.hash.slice(1) || "library"; render(); });

state.route = location.hash.slice(1) || "library";
checkHealth();
loadBaseData();
setInterval(() => {
  if (!document.hidden && document.querySelector("#auto-refresh")?.checked !== false) loadBaseData({ quiet: true });
}, 30000);
