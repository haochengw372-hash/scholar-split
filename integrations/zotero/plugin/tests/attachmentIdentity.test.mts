import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture() {
    const items = new Map<number, any>();
    const parent = {
        id: 100,
        key: "PARENT01",
        isRegularItem: () => true,
        isAttachment: () => false,
        getAttachments: () =>
            [...items.values()]
                .filter((item) => item.parentItemID === 100)
                .map((item) => item.id),
        getNotes: () => [],
    };
    items.set(100, parent);
    function pdf(
        id: number,
        fileName: string,
        title: string,
        parentItemID: number | false = 100,
    ) {
        const relations = new Map<string, string[]>();
        const attachment = {
            id,
            key: `PDFKEY0${id}`,
            libraryID: 1,
            parentItemID,
            deleted: false,
            saved: 0,
            note: "",
            relations,
            isRegularItem: () => false,
            isAttachment: () => true,
            isPDFAttachment: () => true,
            getFilePathAsync: async () => `/fixture/${fileName}`,
            getField: () => title,
            loadDataType: async (_type: string) => {},
            getNote: () => attachment.note,
            setNote: (html: string) => {
                attachment.note = html;
            },
            getRelationsByPredicate: (predicate: string) =>
                relations.get(predicate) || [],
            hasRelation: (predicate: string, uri: string) =>
                (relations.get(predicate) || []).includes(uri),
            addRelation: (predicate: string, uri: string) => {
                relations.set(predicate, [
                    ...new Set([...(relations.get(predicate) || []), uri]),
                ]);
            },
            saveTx: async () => {
                attachment.saved++;
            },
        };
        items.set(id, attachment);
        return attachment;
    }
    const Zotero = {
        Items: {
            get: (id: number) => items.get(id),
            getByLibraryAndKey: (libraryID: number, key: string) =>
                [...items.values()].find(
                    (item) => item.libraryID === libraryID && item.key === key,
                ) || false,
        },
        URI: {
            getItemURI: (item: any) =>
                `http://zotero.org/users/1/items/${item.key}`,
            getURIItemLibraryKey: (uri: string) => ({
                libraryID: 1,
                key: uri.split("/").at(-1),
            }),
        },
    };
    const cache = new Map<string, any>();
    function load(name: string): any {
        if (cache.has(name)) return cache.get(name);
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
            require: (relative: string) => load(relative.replace(/^\.\//, "")),
            Zotero,
            PathUtils: { filename: (path: string) => path.split("/").at(-1) },
        });
        cache.set(name, module.exports);
        return module.exports;
    }
    return { pdf, items, parent, identity: load("attachmentIdentity") };
}

test("recorded original identity survives renamed files and display titles in the actual Zotero adapter", async () => {
    const { pdf, identity } = fixture();
    // The source now has a generated-looking name; the output no longer has one.
    const source = pdf(1, "renamed.mono.pdf", "renamed-deepseek-compare");
    const display = pdf(2, "new-reader-name.pdf", "My reading PDF");
    display.note = "<p>My own note.</p>";
    display.relations.set("dc:relation", [
        "http://zotero.org/users/1/items/OTHERDOC",
    ]);
    await identity.rememberPDFSource(display, source);
    assert.equal(await identity.originalPDFAttachment(display), source);
    assert.equal(await identity.originalPDFAttachment(source), source);
    const candidates = await identity.pdfIdentityAttachments(display);
    assert.equal(
        candidates.find((candidate: any) => candidate.id === source.id)
            .translationRank,
        0,
    );
    assert.ok(
        candidates.find((candidate: any) => candidate.id === display.id)
            .translationRank > 0,
    );
    assert.ok(display.note.includes("My own note."));
    assert.ok(
        display.relations
            .get("dc:relation")!
            .includes("http://zotero.org/users/1/items/OTHERDOC"),
    );
    assert.equal(display.saved, 1);
    await identity.rememberPDFSource(display, source);
    assert.equal(display.saved, 1);
});

test("unique legacy sibling works after renaming but multiple originals require an explicit choice", async () => {
    const { pdf, identity, parent } = fixture();
    const source = pdf(1, "new-original-name.pdf", "Original");
    const display = pdf(2, "old-name.dual.pdf", "deepseek-dual");
    assert.equal(await identity.originalPDFAttachment(display), source);
    assert.equal(await identity.originalPDFAttachment(parent), source);
    pdf(3, "supplement.pdf", "Supplement");
    assert.equal(await identity.originalPDFAttachment(display), null);
    assert.equal(await identity.originalPDFAttachment(parent), null);
});

test("exact standalone source lookup works without browsing unrelated library PDFs", async () => {
    const { pdf, identity } = fixture();
    const source = pdf(1, "original.pdf", "Original", false);
    const display = pdf(2, "renamed.pdf", "Renamed translation", false);
    pdf(3, "unrelated.pdf", "Unrelated", false);
    await identity.rememberPDFSource(display, source);
    assert.equal(await identity.originalPDFAttachment(display), source);
    source.deleted = true;
    assert.equal(await identity.originalPDFAttachment(display), null);
});

test("association cannot silently cross Zotero parents or libraries", async () => {
    const { pdf, identity } = fixture();
    const source = pdf(1, "original.pdf", "Original");
    const display = pdf(2, "translation.pdf", "Translation", 200);
    await assert.rejects(
        identity.rememberPDFSource(display, source),
        /same Zotero paper/,
    );
    assert.equal(display.saved, 0);
    assert.equal(display.note, "");
});

test("an unrelated user relation is not treated as provenance and stale metadata does not fall back", async () => {
    const { pdf, identity } = fixture();
    const source = pdf(1, "original.pdf", "Original");
    const display = pdf(2, "old.dual.pdf", "deepseek-dual");
    display.relations.set("dc:relation", [
        "http://zotero.org/users/1/items/UNRELATED",
    ]);
    assert.equal(await identity.originalPDFAttachment(display), source);
    display.note =
        "<p>ScholarSplit-PDF-Source-v1: http%3A%2F%2Fzotero.org%2Fusers%2F1%2Fitems%2FDELETED1</p>";
    assert.equal(await identity.originalPDFAttachment(display), null);
});
