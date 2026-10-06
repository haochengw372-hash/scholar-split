import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture() {
    const items = new Map<number, any>();
    const readers = new Map<string, { itemID: number }>();
    const submitted: any[] = [];
    const opened: number[] = [];
    const alerts: string[] = [];
    let hooks: any;
    let original: any = null;
    let originalCalls = 0;
    let nextNoteID = 1000;
    class Note {
        id = nextNoteID++;
        key = `NOTE${this.id}`;
        parentItemID = 0;
        libraryID = 1;
        html = "";
        tags: string[] = [];
        isNote() {
            return true;
        }
        hasTag(tag: string) {
            return this.tags.includes(tag);
        }
        addTag(tag: string) {
            this.tags.push(tag);
        }
        getNote() {
            return this.html;
        }
        setNote(html: string) {
            this.html = html;
        }
        async saveTx() {
            items.set(this.id, this);
        }
    }
    const parent = {
        id: 100,
        key: "PARENT01",
        libraryID: 1,
        isRegularItem: () => true,
        isAttachment: () => false,
        isPDFAttachment: () => false,
        getField: () => "Parent paper title",
        getNotes: () =>
            [...items.values()]
                .filter(
                    (item) => item instanceof Note && item.parentItemID === 100,
                )
                .map((item) => item.id),
    };
    items.set(100, parent);
    function pdf(
        id: number,
        fileName: string,
        parentItemID: number | false = 100,
    ) {
        const item = {
            id,
            key: `PDFKEY0${id}`,
            libraryID: 1,
            parentItemID,
            note: "",
            saved: 0,
            isRegularItem: () => false,
            isAttachment: () => true,
            isPDFAttachment: () => true,
            getField: () => fileName,
            getFilePathAsync: async () => `/fixture/${fileName}`,
            loadDataType: async (_type: string) => {},
            getNote: () => item.note,
            setNote: (html: string) => {
                item.note = html;
            },
            saveTx: async () => {
                item.saved++;
            },
        };
        items.set(id, item);
        return item;
    }
    const Zotero = {
        Item: Note,
        Items: { get: (id: number) => items.get(id) },
        Reader: { getByTabID: (tabID: string) => readers.get(tabID) },
        ItemPaneManager: {
            registerSection: (options: any) => {
                hooks = options;
                return "guide-pane";
            },
        },
    };
    function compile(name: string, dependencies: Record<string, any>) {
        const source = readFileSync(
            new URL(`../src/modules/${name}.ts`, import.meta.url),
            "utf8",
        );
        const compiled = ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText;
        const module = { exports: {} };
        vm.runInNewContext(compiled, {
            module,
            exports: module.exports,
            require: (name: string) => dependencies[name],
            Zotero,
            PathUtils: { filename: (path: string) => path.split("/").at(-1) },
            ztoolkit: {
                log: () => {},
                getGlobal: () => (message: string) => {
                    alerts.push(message);
                },
                ProgressWindow: class {
                    createLine() {
                        return this;
                    }
                    show() {}
                    changeLine() {}
                    startCloseTimer() {}
                },
            },
        });
        return module.exports as any;
    }
    const utils = compile("readingGuideUtils", {});
    const { ReadingGuideFactory: factory } = compile("readingGuide", {
        "../../package.json": {
            config: { addonRef: "scholarsplit", addonID: "test-addon" },
        },
        "../utils/locale": {
            getString: (key: string) => key,
            getLocaleID: (key: string) => key,
        },
        "./readingGuideUtils": utils,
        "./defensiveWriting": { DefensiveWritingFactory: {} },
        "./attachmentIdentity": {
            originalPDFAttachment: async () => {
                originalCalls++;
                return original;
            },
        },
        "./pdf2zhHelper": {
            PDF2zhHelperFactory: {
                getServerConfig: () => ({ serverUrl: "http://127.0.0.1:8890" }),
                prepareFileData: async (item: any) => ({
                    fileName: item.getField(),
                    base64: item.key,
                }),
            },
        },
    });
    const result = {
        status: "success",
        kind: "guide",
        guideId: "INPUT_HASH",
        guide: {
            title: "Guide",
            oneSentence: "Summary",
            questions: [{ text: "RQ", page: "p. 3" }],
        },
    };
    factory.requireGuideCapability = async () => {};
    factory.submitGuide = async (_base: string, file: any) => {
        submitted.push(file);
        return result;
    };
    factory.openReaderAndPane = async (id: number) => {
        opened.push(id);
    };
    factory.refreshAllPanes = async () => {};
    class Element {
        children: Element[] = [];
        textContent = "";
        className = "";
        listeners = new Map<string, () => void>();
        ownerDocument: any;
        append(...children: Element[]) {
            this.children.push(...children);
        }
        replaceChildren(...children: Element[]) {
            this.children = children;
        }
        addEventListener(event: string, callback: () => void) {
            this.listeners.set(event, callback);
        }
        all(): Element[] {
            return [this, ...this.children.flatMap((child) => child.all())];
        }
    }
    const doc = {
        createElement: (_tag: string) => {
            const element = new Element();
            element.ownerDocument = doc;
            return element;
        },
    };
    const body = Object.assign(new Element(), {
        ownerDocument: doc,
        closest: () => ({ tabID: "this-reader" }),
    });
    return {
        factory,
        utils,
        pdf,
        parent,
        body,
        readers,
        items,
        submitted,
        opened,
        alerts,
        result,
        get originalCalls() {
            return originalCalls;
        },
        setOriginal: (pdf: any) => {
            original = pdf;
        },
        register: () => {
            factory.registerPane();
            return hooks;
        },
        addGuideNote: async (stored: any) => {
            const note = new Note();
            note.parentItemID = parent.id;
            note.addTag("pdf2zh-reading-guide");
            note.setNote(utils.storedGuideToNoteHTML(stored));
            await note.saveTx();
            return note;
        },
    };
}

test("guide reads original, mono and dual selected PDFs without requiring an original/translation pair", async () => {
    const f = fixture();
    for (const [id, name] of [
        [1, "paper.pdf"],
        [2, "paper.mono.pdf"],
        [3, "paper.dual.pdf"],
    ] as const) {
        const pdf = f.pdf(id, name);
        const selected = await f.factory.resolveSelection(pdf);
        assert.equal(selected.source, pdf);
        assert.equal(selected.display, pdf);
        await f.factory.generateForItem(pdf);
        assert.equal(f.submitted.at(-1).base64, pdf.key);
        assert.equal(f.opened.at(-1), pdf.id);
    }
    assert.equal(f.originalCalls, 0);
    assert.equal(f.alerts.length, 0);
    assert.equal(f.parent.getNotes().length, 3);
});

test("reader-parent guide uses the exact current attachment and rejects a different paper's reader", async () => {
    const f = fixture();
    const current = f.pdf(2, "renamed.dual.pdf");
    f.readers.set("this-reader", { itemID: current.id });
    const context = { tabType: "reader", body: f.body };
    const selected = await f.factory.resolveSelection(f.parent, context);
    assert.equal(selected.source, current);
    await f.factory.generateForItem(f.parent, false, context);
    assert.equal(f.submitted[0].base64, current.key);
    assert.equal(f.opened[0], current.id);
    f.readers.set("this-reader", { itemID: f.pdf(3, "other.pdf", 200).id });
    await assert.rejects(
        f.factory.resolveSelection(f.parent, context),
        /guide-select-pdf/,
    );
    assert.equal(f.originalCalls, 0);
});

test("library parent ignores unrelated readers and requires an unambiguous PDF", async () => {
    const f = fixture();
    f.readers.set("this-reader", { itemID: f.pdf(3, "other.pdf", 200).id });
    const context = { tabType: "library", body: f.body };
    await assert.rejects(
        f.factory.resolveSelection(f.parent, context),
        /guide-select-pdf/,
    );
    const original = f.pdf(1, "original.pdf");
    f.setOriginal(original);
    assert.equal(
        (await f.factory.resolveSelection(f.parent, context)).source,
        original,
    );
});

test("guide notes cache by actual input attachment and preserve guides for other PDFs", async () => {
    const f = fixture();
    const first = f.pdf(1, "original.pdf");
    const second = f.pdf(2, "translated.mono.pdf");
    await f.factory.generateForItem(first);
    await f.factory.generateForItem(second);
    const stored = f.factory.loadStoredGuide(f.parent, second.key);
    assert.equal(stored.sourceAttachmentKey, second.key);
    assert.equal(stored.displayAttachmentKey, second.key);
    assert.equal(stored.sourceKind, "current-pdf");
    assert.equal(stored.inputFileName, "translated.mono.pdf");
    assert.equal(
        f.factory.storedGuideMatchesSelection(
            stored,
            await f.factory.resolveSelection(first),
        ),
        false,
    );
    await f.factory.generateForItem(first);
    assert.equal(f.submitted.length, 2);
    assert.equal(f.parent.getNotes().length, 2);
    assert.equal(f.opened.at(-1), first.id);
});

test("standalone guides preserve attachment notes and round-trip same-key current-PDF metadata", async () => {
    const f = fixture();
    const pdf = f.pdf(1, "standalone.pdf", false);
    pdf.note = "<p>User annotation note</p>";
    await f.factory.generateForItem(pdf);
    const stored = f.utils.storedGuideFromNoteHTML(pdf.note);
    assert.equal(stored.parentItemKey, pdf.key);
    assert.equal(stored.sourceAttachmentKey, pdf.key);
    assert.equal(stored.displayAttachmentKey, pdf.key);
    assert.ok(pdf.note.includes("User annotation note"));
    assert.ok(pdf.note.includes("以下页码均对应当前 PDF"));
    await f.factory.generateForItem(pdf, true);
    assert.ok(pdf.note.includes("User annotation note"));
    assert.equal(
        (pdf.note.match(/data-pdf2zh-reading-guide="1"/g) || []).length,
        1,
    );
    assert.equal(pdf.saved, 2);
    assert.equal(f.alerts.length, 0);
});

test("regenerating a current-PDF guide preserves legacy distinct source/display guide provenance", async () => {
    const f = fixture();
    const original = f.pdf(1, "original.pdf");
    const translated = f.pdf(2, "translated.dual.pdf");
    const old = await f.addGuideNote({
        version: 1,
        parentItemKey: f.parent.key,
        sourceAttachmentKey: original.key,
        displayAttachmentKey: translated.key,
        generatedAt: "previous",
        result: f.result,
    });
    const oldHTML = old.html;
    await f.factory.generateForItem(original, true);
    assert.equal(old.html, oldHTML);
    assert.equal(f.parent.getNotes().length, 2);
    assert.equal(
        f.factory.loadStoredGuide(f.parent, original.key).sourceKind,
        "current-pdf",
    );
});

test("empty-state generation captures the resolved reader PDF, and regeneration retains exact reader context", async () => {
    const f = fixture();
    const current = f.pdf(2, "current.dual.pdf");
    f.readers.set("this-reader", { itemID: current.id });
    const hooks = f.register();
    await hooks.onAsyncRender({
        body: f.body,
        item: f.parent,
        tabType: "reader",
        setEnabled: () => {},
        setSectionSummary: () => {},
    });
    const called: any[] = [];
    f.factory.generateForItem = (...args: any[]) => {
        called.push(args);
    };
    f.body
        .all()
        .find((element) => element.textContent === "guide-generate")!
        .listeners.get("click")!();
    assert.equal(called[0][0], current);
    hooks.sectionButtons[0].onClick({
        item: f.parent,
        body: f.body,
        tabType: "reader",
    });
    assert.equal(called[1][2].body, f.body);
    assert.equal(called[1][2].tabType, "reader");
    f.readers.set("this-reader", { itemID: f.pdf(3, "unrelated.pdf", 200).id });
    await hooks.onAsyncRender({
        body: f.body,
        item: f.parent,
        tabType: "reader",
        setEnabled: () => {},
        setSectionSummary: () => {},
    });
    assert.equal(
        f.body
            .all()
            .some((element) => element.textContent === "guide-generate"),
        false,
    );
});
