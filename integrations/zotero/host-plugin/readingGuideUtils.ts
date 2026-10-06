export interface GuideAnchor {
    text?: string;
    page?: string;
    name?: string;
    role?: string;
    claim?: string;
    evidence?: string;
    pages?: string;
    focus?: string;
    term?: string;
    translation?: string;
    meaning?: string;
}

export interface AdvancedStatisticMethod {
    name: string;
    role?: string;
    modelStructure?: string;
    inputsAndVariables?: string;
    estimation?: string;
    assumptions?: string;
    diagnostics?: string;
    interpretation?: string;
    limitations?: string;
    page?: string;
}

export interface AdvancedStatistics {
    summary: string;
    methods: AdvancedStatisticMethod[];
}

export interface ReadingGuide {
    title: string;
    originalTitle?: string;
    oneSentence: string;
    questions?: GuideAnchor[];
    theory?: GuideAnchor[];
    method?: {
        design?: string;
        sample?: string;
        data?: string;
        analysis?: string;
        page?: string;
    };
    advancedStatistics?: AdvancedStatistics;
    findings?: GuideAnchor[];
    contributions?: GuideAnchor[];
    limitations?: GuideAnchor[];
    concepts?: GuideAnchor[];
    readingPath?: GuideAnchor[];
    cautions?: string[];
    takeaways?: string[];
}

export interface GuideResult {
    status: "success";
    kind: "guide";
    guideId: string;
    guide: ReadingGuide;
    modelName?: string;
    promptVersion?: string;
    extraction?: Record<string, unknown>;
    cached?: boolean;
}

export interface StoredGuide {
    version: 1;
    parentItemKey: string;
    sourceAttachmentKey: string;
    displayAttachmentKey: string;
    sourceKind?: "current-pdf";
    inputFileName?: string;
    generatedAt: string;
    result: GuideResult;
}

const TRANSLATED_PATTERNS: Array<[RegExp, number]> = [
    [/\.mono(?:-cut)?\.pdf$/i, 500],
    [/\.dual(?:-cut)?\.pdf$/i, 400],
    [/[._](?:lr|tb)_dual\.pdf$/i, 400],
    [/\.crop-compare\.pdf$/i, 320],
    [/\.compare\.pdf$/i, 300],
];

const GENERATED_SUFFIX =
    /(?:\.no_watermark)?(?:\.[a-z]{2}(?:-[a-z]{2})?)?(?:[._](?:lr|tb)_dual|\.mono(?:-cut)?|\.dual(?:-cut)?|\.crop-compare|\.compare)$/i;

const GENERATED_TITLE_SUFFIX =
    /(?:^|-)(?:siliconflowfree|siliconflow|silicon|bing|google|openailiked|openaicompatible|openai|aliyundashscope|qwen-mt|deepseek|gemini|zhipu|modelscope|deepl|tencent|grok|groq|ollama|xinference|anythingllm|azure-openai|azure)-(?:mono|mono-cut|dual|dual-cut|compare|crop-compare)\s*$/i;

export function canonicalPDFStem(fileName: string): string {
    return String(fileName || "")
        .replace(/\.pdf$/i, "")
        .replace(GENERATED_SUFFIX, "")
        .normalize("NFKC")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .trim();
}

export function translatedAttachmentRank(
    fileName: string,
    title: string,
    id: number,
    selectedID?: number,
): number {
    const combined = `${fileName} ${title}`;
    let rank = 0;
    for (const [pattern, value] of TRANSLATED_PATTERNS) {
        if (pattern.test(fileName)) {
            rank = Math.max(rank, value);
        }
    }
    if (GENERATED_TITLE_SUFFIX.test(title)) {
        rank = Math.max(rank, 250);
    }
    if (rank > 0 && /deepseek/i.test(combined)) {
        rank += 40;
    }
    if (selectedID === id && rank > 0) {
        rank += 10_000;
    }
    return rank > 0 ? rank * 1_000_000 + id : 0;
}

export function isOriginalPDF(fileName: string): boolean {
    return (
        fileName.toLowerCase().endsWith(".pdf") &&
        TRANSLATED_PATTERNS.every(([pattern]) => !pattern.test(fileName)) &&
        !/\.origin-cut\.pdf$/i.test(fileName)
    );
}

export function sourceMatchesTranslation(
    sourceFileName: string,
    translatedFileName: string,
): boolean {
    return (
        isOriginalPDF(sourceFileName) &&
        canonicalPDFStem(sourceFileName) ===
            canonicalPDFStem(translatedFileName)
    );
}

export function guideResultFromTask(
    task: Record<string, unknown> | undefined,
    taskId: string,
): GuideResult | null {
    if (!task || String(task.taskId ?? task.task_id ?? "") !== taskId) {
        return null;
    }
    const nested =
        task.result && typeof task.result === "object"
            ? (task.result as Record<string, unknown>)
            : task;
    const status = String(task.status || nested.status || "");
    if (status === "失败" || status === "failed" || nested.status === "error") {
        throw new Error(
            String(
                task.error || task.message || nested.message || "导读生成失败",
            ),
        );
    }
    const complete =
        task.finished === true ||
        status === "完成" ||
        status === "success" ||
        nested.status === "success";
    if (
        !complete ||
        nested.kind !== "guide" ||
        !nested.guide ||
        typeof nested.guide !== "object"
    ) {
        return null;
    }
    return nested as unknown as GuideResult;
}

export function escapeHTML(value: unknown): string {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function decodeHTML(value: string): string {
    return value
        .replace(/&#39;/g, "'")
        .replace(/&quot;/g, '"')
        .replace(/&gt;/g, ">")
        .replace(/&lt;/g, "<")
        .replace(/&amp;/g, "&");
}

function noteList(items: string[]): string {
    if (!items.length) return "";
    return `<ul>${items.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>`;
}

export function storedGuideToNoteHTML(stored: StoredGuide): string {
    const guide = stored.result.guide;
    const findings = (guide.findings || []).map((item) =>
        [item.claim, item.evidence, item.page].filter(Boolean).join(" — "),
    );
    const questions = (guide.questions || []).map((item) =>
        [item.text, item.page].filter(Boolean).join(" · "),
    );
    const limitations = (guide.limitations || []).map((item) =>
        [item.text, item.page].filter(Boolean).join(" · "),
    );
    const advancedStatistics = [
        guide.advancedStatistics?.summary || "",
        ...(guide.advancedStatistics?.methods || []).map((item) =>
            [
                item.name,
                item.role && `用途：${item.role}`,
                item.modelStructure && `模型结构：${item.modelStructure}`,
                item.inputsAndVariables &&
                    `变量与输入：${item.inputsAndVariables}`,
                item.estimation && `估计：${item.estimation}`,
                item.assumptions && `关键假设：${item.assumptions}`,
                item.diagnostics && `拟合/诊断：${item.diagnostics}`,
                item.interpretation && `结果解读：${item.interpretation}`,
                item.limitations && `方法局限：${item.limitations}`,
                item.page,
            ]
                .filter(Boolean)
                .join(" — "),
        ),
    ].filter(Boolean);
    const encoded = escapeHTML(JSON.stringify(stored));
    return [
        '<div data-pdf2zh-reading-guide="1">',
        `<h1>PDF2zh 导读｜${escapeHTML(guide.title)}</h1>`,
        `<p><strong>一句话：</strong>${escapeHTML(guide.oneSentence)}</p>`,
        ...(stored.sourceKind === "current-pdf"
            ? [
                  `<p>依据当前 PDF${stored.inputFileName ? `：${escapeHTML(stored.inputFileName)}` : ""}。以下页码均对应当前 PDF。</p>`,
              ]
            : []),
        "<h2>研究问题</h2>",
        noteList(questions),
        "<h2>关键发现</h2>",
        noteList(findings),
        "<h2>高级统计方法拆解</h2>",
        noteList(advancedStatistics),
        "<h2>局限与提醒</h2>",
        noteList(limitations.concat(guide.cautions || [])),
        `<p><small>${escapeHTML(stored.result.modelName || "DeepSeek")} · ${escapeHTML(stored.generatedAt)}</small></p>`,
        `<pre data-pdf2zh-guide-json="1" style="display:none">${encoded}</pre>`,
        "</div>",
    ].join("");
}

export function storedGuideFromNoteHTML(html: string): StoredGuide | null {
    const match = String(html || "").match(
        /<pre[^>]*data-pdf2zh-guide-json=["']1["'][^>]*>([\s\S]*?)<\/pre>/i,
    );
    if (!match) return null;
    try {
        const value = JSON.parse(decodeHTML(match[1]));
        if (
            value?.version === 1 &&
            value?.result?.kind === "guide" &&
            value?.result?.guide
        ) {
            return value as StoredGuide;
        }
    } catch (_error) {
        return null;
    }
    return null;
}
