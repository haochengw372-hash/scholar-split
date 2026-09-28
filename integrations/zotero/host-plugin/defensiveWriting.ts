import { getString } from "../utils/locale";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";

const COMMENT_PREFIX = "ScholarSplit 写作审阅：";
const REVIEW_TIMEOUT_MS = 30 * 60 * 1000;

interface ReviewFlag {
    quote: string;
    reason: string;
    suggestion: string;
    pageIndex: number;
    pageLabel: string;
    sortIndex: string;
    position: { pageIndex: number; rects: number[][] };
}

interface ReviewResult {
    flags: ReviewFlag[];
    pageCount: number;
    unlocatedCount: number;
}

export class DefensiveWritingFactory {
    private static inFlight = new Set<number>();

    static async runFromSelection(): Promise<void> {
        const pane = ztoolkit.getGlobal("ZoteroPane");
        const selected = pane.getSelectedItems();
        if (selected.length !== 1) {
            ztoolkit.getGlobal("alert")(getString("defensive-select-one"));
            return;
        }
        await this.runForItem(selected[0]);
    }

    static async runForItem(item: Zotero.Item): Promise<void> {
        const attachment = this.pdfForItem(item);
        if (!attachment) {
            ztoolkit.getGlobal("alert")(getString("defensive-select-pdf"));
            return;
        }
        if (this.inFlight.has(attachment.id)) return;
        this.inFlight.add(attachment.id);
        const progress = new ztoolkit.ProgressWindow(getString("defensive-title"), {
            closeOnClick: false,
            closeTime: -1,
            closeOtherProgressWindows: false,
        }).createLine({ text: getString("defensive-preparing"), type: "default", progress: 5 });
        progress.show();

        try {
            const fileData = await PDF2zhHelperFactory.prepareFileData(attachment);
            const base = PDF2zhHelperFactory.normalizeServerUrl(
                PDF2zhHelperFactory.getServerConfig().serverUrl,
            );
            const accepted = await fetch(`${base}/api/v1/writing/defensive`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ fileName: fileData.fileName, fileContent: fileData.base64 }),
            });
            const response = (await accepted.json()) as {
                message?: string;
                data?: { id?: string };
            };
            if (!accepted.ok) throw new Error(response.message || getString("defensive-failed"));
            const jobID = String(response.data?.id || "");
            if (!jobID) throw new Error(getString("defensive-failed"));
            const result = await this.waitForResult(base, jobID, progress);
            const count = await this.addHighlights(attachment, result.flags);
            progress.changeLine({
                idx: 0,
                text: getString("defensive-complete", {
                    args: { count, skipped: result.unlocatedCount },
                }),
                type: "success",
                progress: 100,
            });
            progress.startCloseTimer(6000);
            await Zotero.Reader.open(attachment.id);
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            progress.changeLine({ idx: 0, text: message, type: "error", progress: 100 });
            progress.startCloseTimer(8000);
            ztoolkit.getGlobal("alert")(`${getString("defensive-failed")}：${message}`);
        } finally {
            this.inFlight.delete(attachment.id);
        }
    }

    private static pdfForItem(item: Zotero.Item): Zotero.Item | null {
        if (item.isPDFAttachment()) return item;
        if (!item.isRegularItem()) return null;
        const attachments = item.getAttachments()
            .map((id) => Zotero.Items.get(id))
            .filter((candidate): candidate is Zotero.Item => Boolean(candidate && candidate.isPDFAttachment()));
        return attachments.length === 1 ? attachments[0] : null;
    }

    private static async waitForResult(base: string, jobID: string, progress: any): Promise<ReviewResult> {
        const deadline = Date.now() + REVIEW_TIMEOUT_MS;
        while (Date.now() < deadline) {
            const response = await fetch(`${base}/api/v1/jobs/${encodeURIComponent(jobID)}`, { cache: "no-store" });
            if (!response.ok) throw new Error(getString("defensive-failed"));
            const job = ((await response.json()) as unknown as {
                data: { status: string; error?: string; result: ReviewResult; progress: number };
            }).data;
            if (job.status === "failed") throw new Error(job.error || getString("defensive-failed"));
            if (job.status === "completed") return job.result as ReviewResult;
            progress.changeLine({
                idx: 0,
                text: getString("defensive-reviewing"),
                type: "default",
                progress: Math.max(5, Math.min(95, Number(job.progress) || 5)),
            });
            await Zotero.Promise.delay(1200);
        }
        throw new Error(getString("defensive-timeout"));
    }

    private static async addHighlights(attachment: Zotero.Item, flags: ReviewFlag[]): Promise<number> {
        const existing = new Set(
            attachment.getAnnotations()
                .filter((annotation) => annotation.annotationComment?.startsWith(COMMENT_PREFIX))
                .map((annotation) => `${JSON.parse(annotation.annotationPosition).pageIndex}:${annotation.annotationText}`),
        );
        let count = 0;
        for (const flag of flags) {
            const identity = `${flag.pageIndex}:${flag.quote}`;
            if (existing.has(identity)) continue;
            const key = (Zotero as unknown as {
                DataObjectUtilities: { generateKey(): string };
            }).DataObjectUtilities.generateKey();
            const annotation = {
                key,
                type: "highlight",
                text: flag.quote,
                comment: `${COMMENT_PREFIX}${flag.reason}\n建议：${flag.suggestion}`,
                color: "#e24b4b",
                pageLabel: flag.pageLabel,
                sortIndex: flag.sortIndex,
                position: flag.position,
                tags: [{ name: "scholarsplit-defensive-review", color: "#e24b4b" }],
            } as unknown as Parameters<typeof Zotero.Annotations.saveFromJSON>[1];
            await Zotero.Annotations.saveFromJSON(attachment, annotation);
            count += 1;
            existing.add(identity);
        }
        return count;
    }
}
