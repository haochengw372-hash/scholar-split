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
