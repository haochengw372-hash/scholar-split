export interface ChatSourceCandidate {
    id: number;
    key: string;
    original: boolean;
    matchesSelectedTranslation: boolean;
}

/** Only identifiers belonging to the selected PDF may disambiguate siblings. */
export function selectChatSource(
    candidates: ChatSourceCandidate[],
    selectedID?: number,
    storedSourceKey?: string,
): ChatSourceCandidate | null {
    const originals = candidates.filter((candidate) => candidate.original);
    const selected = originals.find((candidate) => candidate.id === selectedID);
    if (selected) return selected;
    if (selectedID !== undefined) {
        const mapped = originals.find(
            (candidate) => candidate.key === storedSourceKey,
        );
        if (mapped) return mapped;
        const matching = originals.filter(
            (candidate) => candidate.matchesSelectedTranslation,
        );
        if (matching.length === 1) return matching[0];
        // A translation of a different document must never fall back to the
        // parent's unrelated original, even if that original is the only one.
        return null;
    }
    return originals.length === 1 ? originals[0] : null;
}

/** Each asynchronous render owns a ticket, invalidated at item change. */
export class ChatRenderGate {
    private generation = 0;
    private selection = "";

    begin(selection: string): number {
        this.selection = selection;
        return ++this.generation;
    }

    invalidate(): void {
        ++this.generation;
        this.selection = "";
    }

    current(ticket: number, selection: string): boolean {
        return ticket === this.generation && selection === this.selection;
    }
}
