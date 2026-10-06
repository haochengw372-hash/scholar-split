import {
    isOriginalPDF,
    sourceMatchesTranslation,
    storedGuideFromNoteHTML,
    translatedAttachmentRank,
} from "./readingGuideUtils";
import {
    PDFIdentityCandidate,
    noteWithPDFSource,
    pdfSourcesFromNote,
    selectOriginalPDF,
} from "./attachmentIdentityUtils";

// Use a supported Zotero relation plus scoped metadata in the attachment note.
// Arbitrary relation predicates work locally but can break Zotero cloud sync.
const SOURCE_RELATION = "dc:relation";

export interface PDFIdentityAttachment extends PDFIdentityCandidate {
    item: Zotero.Item;
    path: string;
    fileName: string;
}

export function pdfParent(item: Zotero.Item): Zotero.Item | null {
    return item.isRegularItem()
        ? item
        : item.parentItemID
          ? Zotero.Items.get(item.parentItemID)
          : null;
}

export async function pdfIdentityAttachments(
    item: Zotero.Item,
): Promise<PDFIdentityAttachment[]> {
    const parent = pdfParent(item);
    const attachments: PDFIdentityAttachment[] = [];
    const storedGuides = parent
        ? parent
              .getNotes()
              .map((noteID) => {
                  const note = Zotero.Items.get(noteID);
                  return note?.isNote()
                      ? storedGuideFromNoteHTML(note.getNote())
                      : null;
              })
              .filter((stored) => stored?.parentItemKey === parent.key)
        : [];
    const ids = parent ? parent.getAttachments() : [item.id];
    if (!parent && item.isPDFAttachment()) {
        await item.loadDataType("note");
        for (const uri of pdfSourcesFromNote(item.getNote())) {
            if (!uri.startsWith("http://zotero.org/")) continue;
            const identity = Zotero.URI.getURIItemLibraryKey(uri);
            if (
                !identity ||
                identity.libraryID !== item.libraryID ||
                !identity.key
            )
                continue;
            const source = Zotero.Items.getByLibraryAndKey(
                identity.libraryID,
                identity.key,
            );
            if (
                source &&
                source.isPDFAttachment() &&
                !source.parentItemID &&
                !source.deleted
            )
                ids.push(source.id);
        }
    }
    for (const id of [...new Set(ids)]) {
        const attachment = Zotero.Items.get(id);
        if (!attachment?.isPDFAttachment() || attachment.deleted) continue;
        const path = await attachment.getFilePathAsync();
        if (!path) continue;
        const fileName = PathUtils.filename(path);
        await attachment.loadDataType("note");
        const sourceURIs = pdfSourcesFromNote(attachment.getNote());
        const guideSourceKeys = storedGuides
            .filter((stored) => stored!.displayAttachmentKey === attachment.key)
            .map((stored) => stored!.sourceAttachmentKey);
        const rank = translatedAttachmentRank(
            fileName,
            attachment.getField("title"),
            id,
        );
        attachments.push({
            id,
            key: attachment.key,
            uri: Zotero.URI.getItemURI(attachment),
            item: attachment,
            path,
            fileName,
            original:
                !sourceURIs.length &&
                !guideSourceKeys.length &&
                rank === 0 &&
                isOriginalPDF(fileName),
            translationRank:
                rank ||
                (sourceURIs.length || guideSourceKeys.length
                    ? 250_000_000 + id
                    : 0),
            sourceURIs,
            guideSourceKeys,
            matchingSourceKeys: [],
        });
    }
    // A recorded source remains an original if either its file or display
    // title is later renamed to something that resembles a generated PDF.
    const recordedURIs = new Set(
        attachments.flatMap((candidate) => candidate.sourceURIs),
    );
    const recordedKeys = new Set(
        attachments.flatMap((candidate) => candidate.guideSourceKeys),
    );
    for (const attachment of attachments) {
        if (
            !attachment.sourceURIs.length &&
            !attachment.guideSourceKeys.length &&
            (recordedURIs.has(attachment.uri) ||
                recordedKeys.has(attachment.key))
        ) {
            attachment.original = true;
            attachment.translationRank = 0;
        }
        attachment.matchingSourceKeys = attachments
            .filter(
                (candidate) =>
                    candidate.original &&
                    sourceMatchesTranslation(
                        candidate.fileName,
                        attachment.fileName,
                    ),
            )
            .map((candidate) => candidate.key);
    }
    return attachments;
}

export async function originalPDFAttachment(
    item: Zotero.Item,
): Promise<Zotero.Item | null> {
    const attachments = await pdfIdentityAttachments(item);
    const source = selectOriginalPDF(
        attachments,
        item.isAttachment() ? item.id : undefined,
    );
    return source
        ? attachments.find((candidate) => candidate.id === source.id)!.item
        : null;
}

export async function rememberPDFSource(
    display: Zotero.Item,
    source: Zotero.Item,
): Promise<void> {
    if (
        display.id === source.id ||
        display.libraryID !== source.libraryID ||
        display.parentItemID !== source.parentItemID
    ) {
        throw new Error(
            "PDF source attachment does not belong to the same Zotero paper",
        );
    }
    const uri = Zotero.URI.getItemURI(source);
    await display.loadDataType("note");
    await display.loadDataType("relations");
    const existing = pdfSourcesFromNote(display.getNote());
    if (
        existing.length === 1 &&
        existing[0] === uri &&
        display.hasRelation(SOURCE_RELATION, uri)
    )
        return;
    // Preserve any unrelated user note and relations; provenance is scoped to
    // the explicit marker rather than treating every related PDF as a source.
    display.setNote(noteWithPDFSource(display.getNote(), uri));
    display.addRelation(SOURCE_RELATION, uri);
    await display.saveTx();
}
