import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture() {
    const items = new Map<number, any>();
    const readers = new Map<string, { itemID: number }>();
    const opened: Array<{ id: number; pageIndex: number }> = [];
    let hooks: any;
    let originalCalls = 0;
    let original: any = null;
    let readFile = async (item: any) => ({
        fileName: `${item.key}.pdf`,
        base64: item.key,
    });
    const getString = (key: string, options?: any) =>
        `${key}${options?.args?.page || ""}`;
    function compile(name: string, dependencies: Record<string, any>) {
        const code = readFileSync(
            new URL(`../src/modules/${name}.ts`, import.meta.url),
            "utf8",
        );
        const compiled = ts.transpileModule(code, {
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
            Zotero: {
                Items: { get: (id: number) => items.get(id) },
                Reader: {
                    getByTabID: (tabID: string) => readers.get(tabID),
                    open: (id: number, location: { pageIndex: number }) => {
                        opened.push({ id, pageIndex: location.pageIndex });
                    },
                },
                ItemPaneManager: {
                    registerSection: (options: any) => {
                        hooks = options;
                        return "test-pane";
                    },
                },
            },
        });
        return module.exports as any;
    }
    const utils = compile("paperChatUtils", {});
    const { PaperChatFactory: factory } = compile("paperChat", {
        "../../package.json": {
            config: { addonRef: "scholarsplit", addonID: "test-addon" },
        },
        "../utils/locale": { getLocaleID: (key: string) => key, getString },
        "./paperChatUtils": utils,
        "./attachmentIdentity": {
            originalPDFAttachment: async () => {
                originalCalls++;
                return original;
            },
        },
        "./pdf2zhHelper": {
            PDF2zhHelperFactory: {
                normalizeServerUrl: (url: string) => url,
                getServerConfig: () => ({ serverUrl: "http://127.0.0.1:8890" }),
                prepareFileData: (item: any) => readFile(item),
            },
        },
    });
    class Element {
        children: Element[] = [];
        listeners = new Map<string, () => void>();
        textContent = "";
        className = "";
        tag: string;
        constructor(tag: string) {
            this.tag = tag;
        }
        append(...children: Element[]) {
            this.children.push(...children);
        }
        replaceChildren(...children: Element[]) {
            this.children = children;
        }
        setAttribute(_name: string, _value: string) {}
        addEventListener(event: string, callback: () => void) {
            this.listeners.set(event, callback);
        }
        all(): Element[] {
            return [this, ...this.children.flatMap((child) => child.all())];
        }
    }
    const body = Object.assign(new Element("body"), {
        ownerDocument: { createElement: (tag: string) => new Element(tag) },
        closest: () => ({ tabID: "this-reader" }),
    });
    function pdf(id: number, parentItemID: number | false = 100) {
        const item = {
            id,
            key: `PDFKEY0${id}`,
            libraryID: 1,
            parentItemID,
            isPDFAttachment: () => true,
            isRegularItem: () => false,
            getField: () => `Current PDF ${id}`,
        };
        items.set(id, item);
        return item;
    }
    const parent = {
        id: 100,
        key: "PARENT01",
        libraryID: 1,
        isPDFAttachment: () => false,
        isRegularItem: () => true,
    };
    items.set(parent.id, parent);
    return {
        factory,
        body,
        pdf,
        parent,
        readers,
        opened,
        get originalCalls() {
            return originalCalls;
        },
        setOriginal: (item: any) => {
            original = item;
        },
        setReadFile: (callback: typeof readFile) => {
            readFile = callback;
        },
        register: () => {
            factory.registerPane();
            return hooks;
        },
    };
}

test("chat reads the selected translated or standalone PDF directly without resolving originals", async () => {
    const f = fixture();
    const translated = f.pdf(1);
    const standalone = f.pdf(2, false);
    assert.equal(
        await f.factory.resolveSource(translated, "reader", f.body),
        translated,
    );
    assert.equal(
        await f.factory.resolveSource(standalone, "library", f.body),
        standalone,
    );
    assert.equal(f.originalCalls, 0);
});

test("parent reader context reads its exact open PDF even when originals are absent or ambiguous", async () => {
    const f = fixture();
    const pdf = f.pdf(2);
    f.readers.set("this-reader", { itemID: pdf.id });
    assert.equal(
        await f.factory.resolveSource(f.parent, "reader", f.body),
        pdf,
    );
    assert.equal(f.originalCalls, 0);
});

test("a reader belonging to another paper cannot replace the context paper", async () => {
    const f = fixture();
    const unrelated = f.pdf(3, 200);
    f.readers.set("this-reader", { itemID: unrelated.id });
    await assert.rejects(
        f.factory.resolveSource(f.parent, "reader", f.body),
        /chat-select-pdf/,
    );
    assert.equal(f.originalCalls, 0);
});

test("library parent context ignores an open reader and still requires unambiguous selection", async () => {
    const f = fixture();
    f.readers.set("this-reader", { itemID: f.pdf(3, 200).id });
    await assert.rejects(
        f.factory.resolveSource(f.parent, "library", f.body),
        /chat-select-pdf/,
    );
    const original = f.pdf(1);
    f.setOriginal(original);
    assert.equal(
        await f.factory.resolveSource(f.parent, "library", f.body),
        original,
    );
});

test("late PDF reads cannot register a document after the user switches papers", async () => {
    const f = fixture();
    const hooks = f.register();
    f.factory.render = () => {};
    let finish: (file: { fileName: string; base64: string }) => void = () => {};
    let reading: () => void = () => {};
    const started = new Promise<void>((resolve) => {
        reading = resolve;
    });
    f.setReadFile(
        () =>
            new Promise((resolve) => {
                finish = resolve;
                reading();
            }),
    );
    let requests = 0;
    f.factory.request = async () => {
        requests++;
    };
    const first = f.pdf(1);
    const render = hooks.onAsyncRender({
        body: f.body,
        item: first,
        tabType: "reader",
        setEnabled: () => {},
        setSectionSummary: () => {},
    });
    await started;
    hooks.onItemChange({ body: f.body, item: f.pdf(2), setEnabled: () => {} });
    finish({ fileName: "first.pdf", base64: "FIRST" });
    await render;
    assert.equal(requests, 0);
});

test("document upload, visible provenance and citation navigation use the same current PDF", async () => {
    const f = fixture();
    const pdf = f.pdf(2);
    f.readers.set("this-reader", { itemID: pdf.id });
    const hooks = f.register();
    const requests: any[] = [];
    f.factory.request = async (_base: string, path: string, options?: any) => {
        requests.push({ path, body: options?.body });
        return path === "/documents"
            ? {
                  documentId: "hash-of-current-pdf",
                  pageCount: 12,
                  textPageCount: 12,
              }
            : {
                  messages: [
                      {
                          role: "assistant",
                          content: "Answer",
                          citations: [{ page: 4, quote: "Current PDF quote" }],
                      },
                  ],
              };
    };
    await hooks.onAsyncRender({
        body: f.body,
        item: f.parent,
        tabType: "reader",
        setEnabled: () => {},
        setSectionSummary: () => {},
    });
    assert.equal(JSON.parse(requests[0].body).fileContent, pdf.key);
    assert.ok(requests[1].path.includes("hash-of-current-pdf"));
    assert.ok(
        f.body.all().some((node) => node.textContent === "Current PDF 2"),
    );
    const citation = f.body
        .all()
        .find(
            (node) =>
                node.tag === "button" &&
                node.textContent.includes("chat-citation-page4"),
        )!;
    citation.listeners.get("click")!();
    assert.deepEqual(f.opened, [{ id: pdf.id, pageIndex: 3 }]);
    hooks.onItemChange({ body: f.body, item: f.pdf(3), setEnabled: () => {} });
    citation.listeners.get("click")!();
    assert.equal(f.opened.length, 1);
});
