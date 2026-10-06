import { config } from "../../package.json";
import { getLocaleID, getString } from "../utils/locale";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { originalPDFAttachment } from "./attachmentIdentity";
import { ChatRenderGate } from "./paperChatUtils";

interface ChatCitation {
    page: number;
    quote: string;
}

interface ChatMessage {
    role: "user" | "assistant";
    content: string;
    citations?: ChatCitation[];
}

interface ChatDocument {
    documentId: string;
    pageCount: number;
    textPageCount: number;
}

interface ChatPane {
    gate: ChatRenderGate;
    ticket: number;
    selection: string;
    source?: Zotero.Item;
    document?: ChatDocument;
    base: string;
    messages: ChatMessage[];
    busy: boolean;
    question: string;
    error: string;
    model: string;
    refresh?: () => Promise<void>;
}

const PANE_ID = "scholarsplit-paper-chat";

export class PaperChatFactory {
    private static registeredPaneID: string | null = null;
    private static panes = new Map<HTMLDivElement, ChatPane>();
    private static refreshers = new Map<HTMLDivElement, () => Promise<void>>();

    static registerPane(): void {
        if (this.registeredPaneID) return;
        const icon = `chrome://${config.addonRef}/content/icons/paper-chat.svg`;
        const registered = Zotero.ItemPaneManager.registerSection({
            paneID: PANE_ID,
            pluginID: config.addonID,
            header: { l10nID: getLocaleID("chat-pane-header"), icon },
            sidenav: { l10nID: getLocaleID("chat-pane-sidenav"), icon },
            onInit: ({ body, refresh }) => {
                this.refreshers.set(body, refresh);
                this.panes.set(body, this.newPane());
            },
            onDestroy: ({ body }) => {
                this.panes.get(body)?.gate.invalidate();
                this.panes.delete(body);
                this.refreshers.delete(body);
            },
            onItemChange: ({ body, item, setEnabled }) => {
                this.panes.get(body)?.gate.invalidate();
                setEnabled(this.supportsItem(item));
                body.replaceChildren();
            },
            onRender: () => {},
            onAsyncRender: async ({
                body,
                item,
                tabType,
                setEnabled,
                setSectionSummary,
            }) => {
                setEnabled(this.supportsItem(item));
                if (!this.supportsItem(item)) return;
                const previous = this.panes.get(body);
                previous?.gate.invalidate();
                const pane = this.newPane();
                pane.refresh = this.refreshers.get(body);
                pane.selection = `${item.libraryID}:${item.key}`;
                pane.ticket = pane.gate.begin(pane.selection);
                pane.busy = true;
                this.panes.set(body, pane);
                setSectionSummary("");
                this.render(body, pane);
                try {
                    pane.source = await this.resolveSource(item, tabType, body);
                    if (!this.isCurrent(body, pane)) return;
                    pane.selection = `${pane.source.libraryID}:${pane.source.key}`;
                    pane.ticket = pane.gate.begin(pane.selection);
                    setSectionSummary(pane.source.getField("title"));
                    this.render(body, pane);
                    const file = await PDF2zhHelperFactory.prepareFileData(
                        pane.source,
                    );
                    if (!this.isCurrent(body, pane)) return;
                    const document = await this.request<ChatDocument>(
                        pane.base,
                        "/documents",
                        {
                            method: "POST",
                            body: JSON.stringify({
                                fileName: file.fileName,
                                fileContent: file.base64,
                            }),
                        },
                    );
                    if (!this.isCurrent(body, pane)) return;
                    const history = await this.request<{
                        messages: ChatMessage[];
                        model?: string;
                    }>(
                        pane.base,
                        `/documents/${encodeURIComponent(document.documentId)}/messages`,
                    );
                    if (!this.isCurrent(body, pane)) return;
                    pane.document = document;
                    pane.messages = history.messages;
                    pane.model = history.model || "";
                } catch (error) {
                    if (!this.isCurrent(body, pane)) return;
                    pane.error =
                        error instanceof Error ? error.message : String(error);
                } finally {
                    if (this.isCurrent(body, pane)) {
                        pane.busy = false;
                        this.render(body, pane);
                    }
                }
            },
        });
        if (typeof registered === "string") this.registeredPaneID = registered;
    }

    static unregisterPane(): void {
        if (this.registeredPaneID)
            Zotero.ItemPaneManager.unregisterSection(this.registeredPaneID);
        this.registeredPaneID = null;
        for (const pane of this.panes.values()) pane.gate.invalidate();
        this.panes.clear();
        this.refreshers.clear();
    }

    private static newPane(): ChatPane {
        return {
            gate: new ChatRenderGate(),
            ticket: 0,
            selection: "",
            messages: [],
            base: PDF2zhHelperFactory.normalizeServerUrl(
                PDF2zhHelperFactory.getServerConfig().serverUrl,
            ),
            busy: false,
            question: "",
            error: "",
            model: "",
        };
    }

    private static supportsItem(item: Zotero.Item): boolean {
        return item.isRegularItem() || item.isPDFAttachment();
    }

    private static isCurrent(body: HTMLDivElement, pane: ChatPane): boolean {
        return (
            this.panes.get(body) === pane &&
            pane.gate.current(pane.ticket, pane.selection)
        );
    }

    private static async resolveSource(
        item: Zotero.Item,
        tabType: "library" | "reader",
        body: HTMLDivElement,
    ): Promise<Zotero.Item> {
        // Chat reads the selected PDF itself, including translated/dual PDFs.
        // Unlike translation and annotation workflows, it needs no original.
        if (item.isPDFAttachment()) return item;
        if (tabType === "reader") {
            // Hook props omit tabID, but Zotero assigns it to this section.
            const section = body.closest("item-pane-custom-section") as
                | (Element & { tabID?: string })
                | null;
            const reader = section?.tabID
                ? Zotero.Reader.getByTabID(section.tabID)
                : null;
            if (reader?.itemID) {
                const pdf = Zotero.Items.get(reader.itemID);
                if (
                    pdf?.isPDFAttachment() &&
                    pdf.libraryID === item.libraryID &&
                    pdf.parentItemID === item.id
                )
                    return pdf;
                throw new Error(getString("chat-select-pdf"));
            }
        }
        const source = await originalPDFAttachment(item);
        if (!source) throw new Error(getString("chat-select-pdf"));
        return source;
    }

    private static async request<T>(
        base: string,
        path: string,
        options: RequestInit = {},
    ): Promise<T> {
        const response = await fetch(`${base}/api/v1/paper-chat${path}`, {
            ...options,
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        const payload = (await response.json()) as unknown as {
            status: string;
            data: T;
            message?: string;
            error?: string;
        };
        if (!response.ok || payload.status !== "success") {
            throw new Error(
                payload.message ||
                    payload.error ||
                    getString("chat-request-failed"),
            );
        }
        return payload.data;
    }

    private static async ask(
        body: HTMLDivElement,
        pane: ChatPane,
    ): Promise<void> {
        const question = pane.question.trim();
        if (
            !question ||
            pane.busy ||
            !pane.document ||
            !this.isCurrent(body, pane)
        )
            return;
        pane.busy = true;
        pane.error = "";
        this.render(body, pane);
        try {
            const result = await this.request<{
                answer: string;
                citations: ChatCitation[];
                model: string;
            }>(
                pane.base,
                `/documents/${encodeURIComponent(pane.document.documentId)}/ask`,
                { method: "POST", body: JSON.stringify({ question }) },
            );
            if (!this.isCurrent(body, pane)) return;
            pane.messages.push(
                { role: "user", content: question },
                {
                    role: "assistant",
                    content: result.answer,
                    citations: result.citations,
                },
            );
            pane.model = result.model;
            pane.question = "";
        } catch (error) {
            if (!this.isCurrent(body, pane)) return;
            pane.error = error instanceof Error ? error.message : String(error);
        } finally {
            if (this.isCurrent(body, pane)) {
                pane.busy = false;
                this.render(body, pane);
                body.querySelector("textarea")?.focus();
            }
        }
    }

    private static async clear(
        body: HTMLDivElement,
        pane: ChatPane,
    ): Promise<void> {
        if (!pane.document || pane.busy || !this.isCurrent(body, pane)) return;
        pane.busy = true;
        pane.error = "";
        this.render(body, pane);
        try {
            await this.request(
                pane.base,
                `/documents/${encodeURIComponent(pane.document.documentId)}/messages`,
                { method: "DELETE" },
            );
            if (!this.isCurrent(body, pane)) return;
            pane.messages = [];
        } catch (error) {
            if (!this.isCurrent(body, pane)) return;
            pane.error = error instanceof Error ? error.message : String(error);
        } finally {
            if (this.isCurrent(body, pane)) {
                pane.busy = false;
                this.render(body, pane);
            }
        }
    }

    private static render(body: HTMLDivElement, pane: ChatPane): void {
        const doc = body.ownerDocument;
        const make = <K extends keyof HTMLElementTagNameMap>(
            tag: K,
            className = "",
            text = "",
        ): HTMLElementTagNameMap[K] => {
            const element = doc.createElement(tag);
            element.className = className;
            element.textContent = text;
            return element;
        };
        body.replaceChildren();
        const style = make("style");
        style.textContent = `
            .ss-chat { padding: 8px 10px 14px; color: var(--fill-primary, CanvasText); font: 13px/1.5 system-ui, sans-serif; }
            .ss-chat-title { margin: 0 0 8px; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
            .ss-chat-meta, .ss-chat-role { color: var(--fill-secondary, GrayText); font-size: 11px; }
            .ss-chat-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
            .ss-chat button { font: inherit; color: inherit; background: var(--material-background, Canvas); border: 1px solid var(--fill-quinary, #ccc); border-radius: 4px; padding: 4px 8px; cursor: pointer; }
            .ss-chat button:disabled { opacity: .5; cursor: default; }
            .ss-chat button:focus-visible, .ss-chat textarea:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
            .ss-chat-transcript { max-height: 420px; overflow-y: auto; scrollbar-gutter: stable; margin: 8px 0 12px; }
            .ss-chat-message { padding: 10px 0; border-bottom: 1px solid var(--fill-quinary, #ddd); }
            .ss-chat-content { margin: 4px 0 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
            .ss-chat-citations, .ss-chat-suggestions { display: flex; flex-wrap: wrap; gap: 5px; }
            .ss-chat-citations button { font-size: 11px; }
            .ss-chat-evidence { font-size: 11px; margin-top: 6px; }
            .ss-chat-evidence summary { cursor: pointer; }
            .ss-chat-evidence p { white-space: pre-wrap; overflow-wrap: anywhere; border-left: 2px solid var(--fill-quinary, #ccc); padding-left: 8px; }
            .ss-chat-suggestions { margin: 10px 0; }
            .ss-chat textarea { box-sizing: border-box; width: 100%; min-height: 76px; resize: vertical; padding: 8px; border: 1px solid var(--fill-quinary, #bbb); border-radius: 4px; color: inherit; background: var(--material-background, Canvas); font: inherit; }
            .ss-chat-compose-actions { display: flex; justify-content: space-between; gap: 8px; margin-top: 6px; align-items: center; }
            .ss-chat-error { margin: 10px 0; padding: 8px; border-left: 2px solid currentColor; background: var(--fill-senary, #eee); white-space: pre-wrap; overflow-wrap: anywhere; }
        `;
        const root = make("div", "ss-chat");
        body.append(style, root);
        root.append(
            make(
                "p",
                "ss-chat-title",
                pane.source?.getField("title") ||
                    getString("chat-current-paper"),
            ),
        );
        const toolbar = make("div", "ss-chat-toolbar");
        const meta = pane.document
            ? `${pane.document.pageCount} ${getString("chat-pages")}${pane.model ? ` · ${pane.model}` : ""}`
            : getString(pane.busy ? "chat-preparing" : "chat-not-ready");
        toolbar.append(make("span", "ss-chat-meta", meta));
        const clear = make("button", "", getString("chat-clear"));
        clear.disabled = pane.busy || !pane.messages.length;
        clear.addEventListener("click", () => {
            void this.clear(body, pane);
        });
        toolbar.append(clear);
        root.append(toolbar);
        const transcript = make("div", "ss-chat-transcript");
        transcript.setAttribute("role", "log");
        transcript.setAttribute("aria-live", "polite");
        for (const message of pane.messages) {
            const block = make("div", "ss-chat-message");
            block.append(
                make(
                    "div",
                    "ss-chat-role",
                    getString(
                        message.role === "user" ? "chat-you" : "chat-assistant",
                    ),
                ),
                make("p", "ss-chat-content", message.content),
            );
            if (message.citations?.length) {
                const citations = make("div", "ss-chat-citations");
                const evidence = make("details", "ss-chat-evidence");
                evidence.append(
                    make("summary", "", getString("chat-show-evidence")),
                );
                for (const [index, citation] of message.citations.entries()) {
                    const button = make(
                        "button",
                        "",
                        `[${index + 1}] ${getString("chat-citation-page", {
                            args: { page: citation.page },
                        })}`,
                    );
                    button.title = citation.quote;
                    button.addEventListener("click", () => {
                        if (!pane.source || !this.isCurrent(body, pane)) return;
                        void Zotero.Reader.open(pane.source.id, {
                            pageIndex: citation.page - 1,
                        });
                    });
                    citations.append(button);
                    evidence.append(
                        make("p", "", `[${index + 1}] ${citation.quote}`),
                    );
                }
                block.append(citations, evidence);
            }
            transcript.append(block);
        }
        if (!pane.messages.length)
            transcript.append(
                make("p", "ss-chat-meta", getString("chat-empty")),
            );
        root.append(transcript);
        transcript.scrollTop = transcript.scrollHeight;
        if (!pane.messages.length && pane.document) {
            const suggestions = make("div", "ss-chat-suggestions");
            for (const key of [
                "chat-suggest-rq",
                "chat-suggest-method",
                "chat-suggest-findings",
            ]) {
                const button = make("button", "", getString(key));
                button.disabled = pane.busy;
                button.addEventListener("click", () => {
                    pane.question = getString(key);
                    void this.ask(body, pane);
                });
                suggestions.append(button);
            }
            root.append(suggestions);
        }
        if (pane.error) {
            const error = make("div", "ss-chat-error", pane.error);
            error.setAttribute("role", "alert");
            root.append(error);
        }
        if (!pane.document) {
            if (pane.busy)
                root.append(
                    make("p", "ss-chat-meta", getString("chat-preparing")),
                );
            else if (pane.error && pane.refresh) {
                const retry = make("button", "", getString("chat-retry"));
                retry.addEventListener("click", () => {
                    if (this.isCurrent(body, pane)) void pane.refresh!();
                });
                root.append(retry);
            }
            return;
        }
        const textarea = make("textarea");
        textarea.value = pane.question;
        textarea.placeholder = getString("chat-placeholder");
        textarea.setAttribute("aria-label", getString("chat-placeholder"));
        textarea.disabled = pane.busy;
        const actions = make("div", "ss-chat-compose-actions");
        const send = make(
            "button",
            "",
            getString(pane.busy ? "chat-answering" : "chat-send"),
        );
        send.disabled = pane.busy || !pane.question.trim();
        textarea.addEventListener("input", () => {
            pane.question = textarea.value;
            send.disabled = pane.busy || !pane.question.trim();
        });
        let composing = false;
        textarea.addEventListener("compositionstart", () => {
            composing = true;
        });
        textarea.addEventListener("compositionend", () => {
            composing = false;
        });
        textarea.addEventListener("keydown", (event) => {
            if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.isComposing &&
                !composing &&
                event.keyCode !== 229
            ) {
                event.preventDefault();
                pane.question = textarea.value;
                void this.ask(body, pane);
            }
        });
        send.addEventListener("click", () => {
            pane.question = textarea.value;
            void this.ask(body, pane);
        });
        actions.append(
            make("span", "ss-chat-meta", getString("chat-keyboard-hint")),
            send,
        );
        root.append(textarea, actions);
    }
}
