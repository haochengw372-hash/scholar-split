export interface PDFIdentityCandidate {
    id: number;
    key: string;
    uri: string;
    original: boolean;
    translationRank: number;
    sourceURIs: string[];
    guideSourceKeys: string[];
    matchingSourceKeys: string[];
}

const SOURCE_MARKER = "ScholarSplit-PDF-Source-v1:";

export function pdfSourcesFromNote(html: string): string[] {
    return [...html.matchAll(/ScholarSplit-PDF-Source-v1:\s*([^\s<>]*)/g)].map(
        (match) => {
            try {
                return decodeURIComponent(match[1]) || "invalid-source-marker";
            } catch {
                return "invalid-source-marker";
            }
        },
    );
}

export function noteWithPDFSource(html: string, uri: string): string {
    const clean = html.replace(
        /<p\b[^>]*>\s*ScholarSplit-PDF-Source-v1:\s*[^<>]*<\/p>/g,
        "",
    );
    return `${clean}<p>${SOURCE_MARKER} ${encodeURIComponent(uri)}</p>`;
}

/** Stored attachment identities take precedence over legacy filename hints. */
export function selectOriginalPDF(
    candidates: PDFIdentityCandidate[],
    selectedID?: number,
): PDFIdentityCandidate | null {
    const selected = candidates.find(
        (candidate) => candidate.id === selectedID,
    );
    const originals = candidates.filter((candidate) => candidate.original);
    if (selectedID !== undefined && !selected) return null;
    if (selected?.sourceURIs.length) {
        const sources = [...new Set(selected.sourceURIs)];
        if (sources.length !== 1) return null;
        const source = candidates.find(
            (candidate) => candidate.uri === sources[0],
        );
        // A source link may outlive a deleted/moved original. Do not replace it
        // with a different sibling, or follow a cycle between generated PDFs.
        return source &&
            source.id !== selected.id &&
            !source.sourceURIs.length &&
            !source.guideSourceKeys.length
            ? source
            : null;
    }
    if (selected?.guideSourceKeys.length) {
        const keys = [...new Set(selected.guideSourceKeys)];
        return keys.length === 1
            ? candidates.find(
                  (candidate) =>
                      candidate.key === keys[0] &&
                      candidate.id !== selected.id &&
                      !candidate.sourceURIs.length &&
                      !candidate.guideSourceKeys.length,
              ) || null
            : null;
    }
    if (selected?.original) return selected;
    if (selected) {
        const matching = originals.filter((candidate) =>
            selected.matchingSourceKeys.includes(candidate.key),
        );
        if (matching.length === 1) return matching[0];
        if (matching.length > 1) return null;
    }
    // Old translations have no provenance. A single original belonging to
    // the same Zotero parent is unambiguous even after Zotero renames its PDF.
    return originals.length === 1 ? originals[0] : null;
}

export function selectGuidePDF(
    candidates: PDFIdentityCandidate[],
    selectedID?: number,
): { source: PDFIdentityCandidate; display: PDFIdentityCandidate } | null {
    const selected = candidates.find(
        (candidate) => candidate.id === selectedID,
    );
    if (selectedID !== undefined && !selected) return null;
    const source = selectOriginalPDF(candidates, selectedID);
    if (selectedID !== undefined && !source) return null;
    const translations = candidates
        .filter((candidate) => candidate.translationRank > 0)
        .sort((a, b) => b.translationRank - a.translationRank);
    // Do not fall back to a different translation when the selected one has
    // an unresolved source link.
    if (selected && selected.translationRank > 0) {
        return source ? { source, display: selected } : null;
    }
    for (const display of translations) {
        const linked = selectOriginalPDF(candidates, display.id);
        if (linked && (!source || linked.id === source.id)) {
            return { source: linked, display };
        }
    }
    return null;
}
