import { config } from "../../package.json";
import { getString, getLocaleID } from "../utils/locale";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { DefensiveWritingFactory } from "./defensiveWriting";
import {
    AdvancedStatisticMethod,
    GuideAnchor,
    GuideResult,
    ReadingGuide,
    StoredGuide,
    guideResultFromTask,
    storedGuideFromNoteHTML,
    storedGuideToNoteHTML,
} from "./readingGuideUtils";
import {
    pdfIdentityAttachments,
    rememberPDFSource,
} from "./attachmentIdentity";
import { selectGuidePDF } from "./attachmentIdentityUtils";

const GUIDE_TAG = "pdf2zh-reading-guide";
const PANE_ID = "pdf2zh-reading-guide";
const GUIDE_TIMEOUT_MS = 30 * 60 * 1000;

interface GuideSelection {
    parent: Zotero.Item;
    source: Zotero.Item;
    display: Zotero.Item;
    sourcePath: string;
    displayPath: string;
}

export class ReadingGuideFactory {
    private static registeredPaneID: string | null = null;
    private static paneRefreshers = new Map<
        HTMLDivElement,
        () => Promise<void>
    >();
    private static inFlight = new Set<string>();

    static registerPane(): void {
        if (this.registeredPaneID) return;
        const icon = `chrome://${config.addonRef}/content/icons/favicon.svg`;
        const defensiveIcon = `chrome://${config.addonRef}/content/icons/defensive-writing.svg`;
        const registered = Zotero.ItemPaneManager.registerSection({
            paneID: PANE_ID,
            pluginID: config.addonID,
            header: {
                l10nID: getLocaleID("guide-pane-header"),
                icon,
            },
            sidenav: {
                l10nID: getLocaleID("guide-pane-sidenav"),
                icon,
            },
            sectionButtons: [
                {
                    type: "regenerate",
                    icon: "chrome://zotero/skin/16/universal/sync.svg",
                    l10nID: getLocaleID("guide-regenerate"),
                    onClick: ({ item }) => {
                        void this.generateForItem(item, true);
                    },
                },
                {
                    type: "defensive-writing",
                    icon: defensiveIcon,
                    l10nID: getLocaleID("defensive-menu"),
                    onClick: ({ item }) => {
                        void DefensiveWritingFactory.runForItem(item);
                    },
                },
            ],
            onInit: ({ body, refresh }) => {
                this.paneRefreshers.set(body, refresh);
            },
            onDestroy: ({ body }) => {
                this.paneRefreshers.delete(body);
            },
            onItemChange: ({ item, setEnabled }) => {
                setEnabled(Boolean(this.parentFor(item)));
            },
            // Zotero 9.0.6 validates this hook as mandatory even when all
            // rendering work is performed by onAsyncRender.
            onRender: () => {},
            onAsyncRender: async ({
                body,
                item,
                setEnabled,
                setSectionSummary,
            }) => {
                const parent = this.parentFor(item);
                setEnabled(Boolean(parent));
                if (!parent) {
                    this.renderEmpty(body, getString("guide-no-parent"));
                    return;
                }
                let selection: GuideSelection | null = null;
                try {
                    selection = await this.resolveSelection(item);
                } catch (_error) {
                    // The empty state below gives the user the actionable path.
                }
                const stored = this.loadStoredGuide(parent);
                if (
                    !stored ||
                    !selection ||
                    !this.storedGuideMatchesSelection(stored, selection)
                ) {
                    setSectionSummary(getString("guide-not-generated"));
                    this.renderEmpty(body, getString("guide-empty"), item);
                    return;
                }
                setSectionSummary(stored.result.guide.oneSentence);
                this.renderGuide(body, stored);
            },
        });
        if (typeof registered === "string") {
            this.registeredPaneID = registered;
        }
    }

    static unregisterPane(): void {
        if (!this.registeredPaneID) return;
        Zotero.ItemPaneManager.unregisterSection(this.registeredPaneID);
        this.registeredPaneID = null;
        this.paneRefreshers.clear();
        this.inFlight.clear();
    }

    static async generateFromSelection(forceRegenerate = false): Promise<void> {
        const pane = ztoolkit.getGlobal("ZoteroPane");
        const selected = pane.getSelectedItems();
        if (selected.length !== 1) {
            ztoolkit.getGlobal("alert")(getString("guide-single-selection"));
            return;
        }
        await this.generateForItem(selected[0], forceRegenerate);
    }

    static async generateForItem(
        item: Zotero.Item,
        forceRegenerate = false,
    ): Promise<void> {
        let progress: any;
        let acquiredGuard = false;
        let guardKey = "";
        try {
            const selection = await this.resolveSelection(item);
            guardKey = `${selection.parent.libraryID}:${selection.parent.key}`;
            if (this.inFlight.has(guardKey)) {
                ztoolkit.getGlobal("alert")(getString("guide-already-running"));
                return;
            }
            this.inFlight.add(guardKey);
            acquiredGuard = true;
            const existing = this.loadStoredGuide(selection.parent);
            if (
                existing &&
                this.storedGuideMatchesSelection(existing, selection) &&
                !forceRegenerate
            ) {
                await this.openReaderAndPane(selection.display.id);
                await this.refreshAllPanes();
                return;
            }

            progress = new ztoolkit.ProgressWindow(
                getString("guide-progress-title"),
                {
                    closeOnClick: false,
                    closeTime: -1,
                    closeOtherProgressWindows: false,
                },
            ).createLine({
                text: getString("guide-progress-preparing"),
                type: "default",
                progress: 5,
            });
            progress.show();

            const serverConfig = PDF2zhHelperFactory.getServerConfig();
            await this.requireGuideCapability(serverConfig.serverUrl);
            const fileData = await PDF2zhHelperFactory.prepareFileData(
                selection.source,
            );
            const result = await this.submitGuide(
                serverConfig.serverUrl,
                fileData,
                selection.parent.getField("title") || fileData.fileName,
                forceRegenerate,
                (percent, message) => {
                    progress.changeLine({
                        idx: 0,
                        text: message || getString("guide-progress-generating"),
                        type: "default",
                        progress: Math.max(5, Math.min(95, percent)),
                    });
                },
            );

            await this.saveGuideNote(selection, result);
            progress.changeLine({
                idx: 0,
                text: result.cached
                    ? getString("guide-progress-cached")
                    : getString("guide-progress-complete"),
                type: "success",
                progress: 100,
            });
            progress.startCloseTimer(2500);
            await this.openReaderAndPane(selection.display.id);
            await this.refreshAllPanes();
        } catch (error) {
            const message =
                error instanceof Error ? error.message : String(error);
            ztoolkit.log("PDF2zh reading guide failed", error);
            if (progress) {
                progress.changeLine({
                    idx: 0,
                    text: message,
                    type: "error",
                    progress: 100,
                });
                progress.startCloseTimer(7000);
            }
            ztoolkit.getGlobal("alert")(
                getString("guide-error", { args: { message } }),
            );
        } finally {
            if (acquiredGuard) this.inFlight.delete(guardKey);
        }
    }

    private static parentFor(item?: Zotero.Item): Zotero.Item | null {
        if (!item) return null;
        if (item.isRegularItem()) return item;
        if (item.isAttachment() && item.parentItemID) {
            return Zotero.Items.get(item.parentItemID) || null;
        }
        return null;
    }

    private static async resolveSelection(
        item: Zotero.Item,
    ): Promise<GuideSelection> {
        const parent = this.parentFor(item);
        if (!parent) throw new Error(getString("guide-no-parent"));
        const selectedID = item.isAttachment() ? item.id : undefined;
        const candidates = await pdfIdentityAttachments(item);
        if (!candidates.some((candidate) => candidate.translationRank > 0))
            throw new Error(getString("guide-translation-missing"));
        const resolved = selectGuidePDF(candidates, selectedID);
        if (!resolved) throw new Error(getString("guide-original-missing"));
        const source = candidates.find(
            (candidate) => candidate.id === resolved.source.id,
        )!;
        const translated = candidates.find(
            (candidate) => candidate.id === resolved.display.id,
        )!;

        return {
            parent,
            source: source.item,
            display: translated.item,
            sourcePath: source.path,
            displayPath: translated.path,
        };
    }

    private static async requireGuideCapability(
        serverUrl: string,
    ): Promise<void> {
        const base = PDF2zhHelperFactory.normalizeServerUrl(serverUrl);
        const response = await fetch(`${base}/health`, { cache: "no-store" });
        if (!response.ok)
            throw new Error(getString("guide-server-unavailable"));
        const health = (await response.json()) as { capabilities?: string[] };
        if (!health.capabilities?.includes("readingGuideV1")) {
            throw new Error(getString("guide-server-upgrade"));
        }
    }

    private static async submitGuide(
        serverUrl: string,
        fileData: { fileName: string; base64: string },
        title: string,
        forceRegenerate: boolean,
        onProgress: (percent: number, message: string) => void,
    ): Promise<GuideResult> {
        const base = PDF2zhHelperFactory.normalizeServerUrl(serverUrl);
        const response = await fetch(`${base}/guide`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-PDF2zh-Protocol": "accepted",
            },
            body: JSON.stringify({
                fileName: fileData.fileName,
                fileContent: fileData.base64,
                title,
                forceRegenerate,
                asyncJob: true,
            }),
            cache: "no-store",
        });
        const payload = (await response.json()) as unknown as Record<
            string,
            unknown
        >;
        if (!response.ok || payload.status === "error") {
            throw new Error(String(payload.message || "导读请求失败"));
        }
        const taskId = PDF2zhHelperFactory.taskIdFromPayload(payload);
        if (!taskId) {
            const direct = guideResultFromTask(payload, "");
            if (direct) return direct;
            throw new Error(getString("guide-task-missing"));
        }

        const configForPolling = {
            ...PDF2zhHelperFactory.getServerConfig(),
            serverUrl: base,
        };
        const deadline = Date.now() + GUIDE_TIMEOUT_MS;
        let consecutiveMisses = 0;
        while (Date.now() < deadline) {
            const task = await PDF2zhHelperFactory.fetchTaskRecord(
                configForPolling,
                taskId,
            );
            const result = guideResultFromTask(task, taskId);
            if (result) return result;
            if (task) {
                consecutiveMisses = 0;
                const raw = Number(task.progress);
                onProgress(
                    Number.isFinite(raw) ? raw : 10,
                    String(task.message || task.status || ""),
                );
            } else if (++consecutiveMisses >= 15) {
                throw new Error(getString("guide-task-lost"));
            }
            await Zotero.Promise.delay(1000);
        }
        throw new Error(getString("guide-timeout"));
    }

    private static findGuideNote(parent: Zotero.Item): Zotero.Item | null {
        for (const id of parent.getNotes()) {
            const note = Zotero.Items.get(id);
            if (note?.isNote() && note.hasTag(GUIDE_TAG)) return note;
        }
        return null;
    }

    private static loadStoredGuide(parent: Zotero.Item): StoredGuide | null {
        const note = this.findGuideNote(parent);
        return note ? storedGuideFromNoteHTML(note.getNote()) : null;
    }

    private static storedGuideMatchesSelection(
        stored: StoredGuide,
        selection: GuideSelection,
    ): boolean {
        return (
            stored.parentItemKey === selection.parent.key &&
            stored.sourceAttachmentKey === selection.source.key &&
            stored.displayAttachmentKey === selection.display.key
        );
    }

    private static async refreshAllPanes(): Promise<void> {
        await Promise.all(
            [...this.paneRefreshers.values()].map(async (refresh) => {
                try {
                    await refresh();
                } catch (error) {
                    ztoolkit.log("Could not refresh reading guide pane", error);
                }
            }),
        );
    }

    private static async saveGuideNote(
        selection: GuideSelection,
        result: GuideResult,
    ): Promise<void> {
        const stored: StoredGuide = {
            version: 1,
            parentItemKey: selection.parent.key,
            sourceAttachmentKey: selection.source.key,
            displayAttachmentKey: selection.display.key,
            generatedAt: new Date().toISOString(),
            result,
        };
        let note = this.findGuideNote(selection.parent);
        if (!note) {
            note = new Zotero.Item("note");
            note.libraryID = selection.parent.libraryID;
            note.parentItemID = selection.parent.id;
            note.addTag(GUIDE_TAG);
        }
        note.setNote(storedGuideToNoteHTML(stored));
        await note.saveTx();
        await rememberPDFSource(selection.display, selection.source);
    }

    private static async openReaderAndPane(
        attachmentID: number,
    ): Promise<void> {
        await Zotero.Reader.open(attachmentID);
        await Zotero.Promise.delay(350);
        const win = Zotero.getMainWindow() as any;
        try {
            win.ZoteroContextPane?.focus?.();
            const nav = win.ZoteroContextPane?.sidenav;
            const expected = this.registeredPaneID || PANE_ID;
            const buttons =
                nav?.querySelectorAll?.("[data-pane], [data-pane-id]") || [];
            const target = [...buttons].find(
                (element: any) =>
                    element.dataset?.pane === expected ||
                    element.dataset?.paneId === expected,
            ) as any;
            if (target) {
                const event = new target.ownerDocument.defaultView.MouseEvent(
                    "click",
                    {
                        bubbles: true,
                        cancelable: true,
                        detail: 1,
                    },
                );
                target.dispatchEvent(event);
            }
        } catch (error) {
            ztoolkit.log("Could not auto-focus reading guide pane", error);
        }
    }

    private static renderEmpty(
        body: HTMLDivElement,
        message: string,
        item?: Zotero.Item,
    ): void {
        const doc = body.ownerDocument;
        body.replaceChildren();
        this.appendStyle(doc, body);
        const empty = this.element(doc, "div", "pdf2zh-guide-empty");
        empty.append(
            this.element(doc, "div", "pdf2zh-guide-empty-mark", "导"),
            this.element(doc, "p", "", message),
        );
        if (item) {
            const button = this.element(
                doc,
                "button",
                "pdf2zh-guide-action",
                getString("guide-generate"),
            ) as HTMLButtonElement;
            button.type = "button";
            button.addEventListener(
                "click",
                () => void this.generateForItem(item),
            );
            empty.append(button);
        }
        body.append(empty);
    }

    private static renderGuide(
        body: HTMLDivElement,
        stored: StoredGuide,
    ): void {
        const doc = body.ownerDocument;
        const guide = stored.result.guide;
        body.replaceChildren();
        this.appendStyle(doc, body);
        const root = this.element(doc, "article", "pdf2zh-guide-root");
        const eyebrow = this.element(
            doc,
            "div",
            "pdf2zh-guide-eyebrow",
            `${stored.result.modelName || "DeepSeek"} · ${stored.result.cached ? "缓存" : "新生成"}`,
        );
        const title = this.element(
            doc,
            "h2",
            "pdf2zh-guide-title",
            guide.title,
        );
        const thesis = this.element(
            doc,
            "p",
            "pdf2zh-guide-thesis",
            guide.oneSentence,
        );
        root.append(eyebrow, title, thesis);

        this.appendAnchorSection(
            root,
            getString("guide-section-questions"),
            guide.questions,
            "text",
        );
        this.appendTheory(root, guide.theory || []);
        this.appendMethod(root, guide);
        this.appendAdvancedStatistics(root, guide.advancedStatistics);
        this.appendAnchorSection(
            root,
            getString("guide-section-findings"),
            guide.findings,
            "claim",
        );
        this.appendAnchorSection(
            root,
            getString("guide-section-contributions"),
            guide.contributions,
            "text",
        );
        this.appendConcepts(root, guide.concepts || []);
        this.appendReadingPath(root, guide.readingPath || []);
        this.appendStringSection(root, getString("guide-section-limitations"), [
            ...(guide.limitations || []).map((item) =>
                [item.text, item.page].filter(Boolean).join(" · "),
            ),
            ...(guide.cautions || []),
        ]);
        this.appendStringSection(
            root,
            getString("guide-section-takeaways"),
            guide.takeaways || [],
        );
        body.append(root);
    }

    private static appendTheory(root: HTMLElement, items: GuideAnchor[]): void {
        const values = items.map((item) =>
            [item.name, item.role, item.page].filter(Boolean).join(" — "),
        );
        this.appendStringSection(
            root,
            getString("guide-section-theory"),
            values,
        );
    }

    private static appendMethod(root: HTMLElement, guide: ReadingGuide): void {
        const method = guide.method;
        if (!method) return;
        const values = [
            [getString("guide-method-design"), method.design],
            [getString("guide-method-sample"), method.sample],
            [getString("guide-method-data"), method.data],
            [getString("guide-method-analysis"), method.analysis],
        ].filter((entry) => entry[1]);
        if (!values.length) return;
        const section = this.section(root, getString("guide-section-method"));
        const grid = this.element(
            root.ownerDocument,
            "div",
            "pdf2zh-guide-method-grid",
        );
        for (const [label, value] of values) {
            const card = this.element(
                root.ownerDocument,
                "div",
                "pdf2zh-guide-method-card",
            );
            card.append(
                this.element(
                    root.ownerDocument,
                    "span",
                    "pdf2zh-guide-label",
                    label,
                ),
                this.element(root.ownerDocument, "p", "", String(value)),
            );
            grid.append(card);
        }
        section.append(grid);
    }

    private static appendAdvancedStatistics(
        root: HTMLElement,
        advanced: ReadingGuide["advancedStatistics"],
    ): void {
        if (!advanced) return;
        const section = this.section(
            root,
            getString("guide-section-advanced-statistics"),
        );
        section.append(
            this.element(
                root.ownerDocument,
                "p",
                "pdf2zh-guide-stats-summary",
                advanced.summary,
            ),
        );
        for (const method of advanced.methods || []) {
            section.append(this.advancedStatisticCard(root, method));
        }
    }

    private static advancedStatisticCard(
        root: HTMLElement,
        method: AdvancedStatisticMethod,
    ): HTMLElement {
        const doc = root.ownerDocument;
        const card = this.element(doc, "article", "pdf2zh-guide-stats-card");
        const heading = this.element(doc, "h4", "", method.name);
        if (method.page) {
            heading.append(
                this.element(
                    doc,
                    "span",
                    "pdf2zh-guide-stats-page",
                    method.page,
                ),
            );
        }
        card.append(heading);
        const rows: Array<[string, string | undefined]> = [
            [getString("guide-stats-role"), method.role],
            [getString("guide-stats-structure"), method.modelStructure],
            [getString("guide-stats-inputs"), method.inputsAndVariables],
            [getString("guide-stats-estimation"), method.estimation],
            [getString("guide-stats-assumptions"), method.assumptions],
            [getString("guide-stats-diagnostics"), method.diagnostics],
            [getString("guide-stats-interpretation"), method.interpretation],
            [getString("guide-stats-limitations"), method.limitations],
        ];
        for (const [label, value] of rows) {
            if (!value) continue;
            const row = this.element(doc, "div", "pdf2zh-guide-stats-row");
            row.append(
                this.element(doc, "span", "pdf2zh-guide-label", label),
                this.element(doc, "p", "", value),
            );
            card.append(row);
        }
        return card;
    }

    private static appendAnchorSection(
        root: HTMLElement,
        title: string,
        items: GuideAnchor[] | undefined,
        field: keyof GuideAnchor,
    ): void {
        const values = (items || []).map((item) =>
            [item[field], item.evidence, item.page].filter(Boolean).join(" — "),
        );
        this.appendStringSection(root, title, values);
    }

    private static appendConcepts(
        root: HTMLElement,
        items: GuideAnchor[],
    ): void {
        const values = items.map((item) =>
            [item.term, item.translation, item.meaning]
                .filter(Boolean)
                .join("｜"),
        );
        this.appendStringSection(
            root,
            getString("guide-section-concepts"),
            values,
        );
    }

    private static appendReadingPath(
        root: HTMLElement,
        items: GuideAnchor[],
    ): void {
        const values = items.map((item) =>
            [item.pages, item.focus].filter(Boolean).join(" — "),
        );
        this.appendStringSection(
            root,
            getString("guide-section-reading-path"),
            values,
        );
    }

    private static appendStringSection(
        root: HTMLElement,
        title: string,
        items: string[],
    ): void {
        const values = items.filter(Boolean);
        if (!values.length) return;
        const section = this.section(root, title);
        const list = this.element(
            root.ownerDocument,
            "ol",
            "pdf2zh-guide-list",
        );
        for (const value of values) {
            list.append(this.element(root.ownerDocument, "li", "", value));
        }
        section.append(list);
    }

    private static section(root: HTMLElement, title: string): HTMLElement {
        const section = this.element(
            root.ownerDocument,
            "section",
            "pdf2zh-guide-section",
        );
        section.append(this.element(root.ownerDocument, "h3", "", title));
        root.append(section);
        return section;
    }

    private static element(
        doc: Document,
        tag: string,
        className = "",
        text = "",
    ): HTMLElement {
        const element = doc.createElement(tag);
        if (className) element.className = className;
        if (text) element.textContent = text;
        return element;
    }

    private static appendStyle(doc: Document, body: HTMLElement): void {
        const style = doc.createElement("style");
        style.textContent = `
            .pdf2zh-guide-root { color: var(--fill-primary, #202124); padding: 6px 10px 24px; font-family: Georgia, "Songti SC", serif; line-height: 1.65; }
            .pdf2zh-guide-eyebrow { color: #3157a4; font: 600 10px/1.3 ui-sans-serif, sans-serif; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 8px; }
            .pdf2zh-guide-title { font-size: 21px; line-height: 1.28; margin: 0 0 12px; text-wrap: balance; }
            .pdf2zh-guide-thesis { margin: 0 0 20px; padding: 13px 14px; border-left: 3px solid #3157a4; background: color-mix(in srgb, #3157a4 8%, transparent); font-size: 14px; }
            .pdf2zh-guide-section { border-top: 1px solid color-mix(in srgb, currentColor 14%, transparent); padding-top: 13px; margin-top: 16px; }
            .pdf2zh-guide-section h3 { margin: 0 0 8px; font: 700 12px/1.3 ui-sans-serif, sans-serif; letter-spacing: .06em; color: #3157a4; }
            .pdf2zh-guide-list { margin: 0; padding-left: 20px; }
            .pdf2zh-guide-list li { margin: 0 0 8px; padding-left: 3px; }
            .pdf2zh-guide-method-grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
            .pdf2zh-guide-method-card { border: 1px solid color-mix(in srgb, currentColor 12%, transparent); border-radius: 8px; padding: 10px 11px; background: color-mix(in srgb, Canvas 94%, #3157a4); }
            .pdf2zh-guide-method-card p { margin: 4px 0 0; }
            .pdf2zh-guide-stats-summary { margin: 0 0 10px; color: color-mix(in srgb, currentColor 78%, transparent); }
            .pdf2zh-guide-stats-card { margin: 10px 0; padding: 12px 13px; border: 1px solid color-mix(in srgb, #3157a4 24%, transparent); border-radius: 9px; background: color-mix(in srgb, Canvas 96%, #3157a4); }
            .pdf2zh-guide-stats-card h4 { display: flex; justify-content: space-between; gap: 10px; margin: 0 0 10px; color: #3157a4; font: 700 14px/1.35 ui-sans-serif, sans-serif; }
            .pdf2zh-guide-stats-page { flex: 0 0 auto; color: color-mix(in srgb, currentColor 60%, transparent); font-size: 10px; font-weight: 600; }
            .pdf2zh-guide-stats-row { display: grid; grid-template-columns: minmax(68px, 82px) 1fr; gap: 9px; padding: 7px 0; border-top: 1px solid color-mix(in srgb, currentColor 8%, transparent); }
            .pdf2zh-guide-stats-row:first-of-type { border-top: 0; }
            .pdf2zh-guide-stats-row p { margin: 0; }
            .pdf2zh-guide-label { font: 650 10px/1.2 ui-sans-serif, sans-serif; color: color-mix(in srgb, currentColor 62%, transparent); }
            .pdf2zh-guide-empty { min-height: 180px; display: grid; place-items: center; align-content: center; gap: 10px; padding: 28px 18px; text-align: center; color: color-mix(in srgb, currentColor 65%, transparent); }
            .pdf2zh-guide-empty-mark { width: 42px; height: 42px; display: grid; place-items: center; border: 1px solid #3157a4; border-radius: 50%; color: #3157a4; font: 700 18px/1 Georgia, serif; }
            .pdf2zh-guide-empty p { margin: 0; max-width: 30ch; }
            .pdf2zh-guide-action { border: 0; border-radius: 999px; padding: 7px 13px; background: #3157a4; color: white; cursor: pointer; }
            @media (prefers-color-scheme: dark) { .pdf2zh-guide-root { color: #e8e8e8; } .pdf2zh-guide-title { color: #fff; } .pdf2zh-guide-eyebrow, .pdf2zh-guide-section h3 { color: #9bb7ff; } }
        `;
        body.append(style);
    }
}
