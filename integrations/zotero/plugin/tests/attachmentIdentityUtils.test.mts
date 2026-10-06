import assert from "node:assert/strict";
import test from "node:test";
import {
    selectGuidePDF,
    selectOriginalPDF,
    pdfSourcesFromNote,
    noteWithPDFSource,
    type PDFIdentityCandidate,
} from "../src/modules/attachmentIdentityUtils.ts";

const original = (id: number): PDFIdentityCandidate => ({
    id,
    key: `SOURCE${id}`,
    uri: `http://zotero.org/users/1/items/SOURCE${id}`,
    original: true,
    translationRank: 0,
    sourceURIs: [],
    guideSourceKeys: [],
    matchingSourceKeys: [],
});
const translation = (id = 9): PDFIdentityCandidate => ({
    ...original(id),
    key: `TRANSLATION${id}`,
    uri: `http://zotero.org/users/1/items/TRANSLATION${id}`,
    original: false,
    translationRank: 500,
});

test("renamed original and translation resolve by stable attachment URI, not filenames", () => {
    const source = original(1);
    const display = { ...translation(), sourceURIs: [source.uri] };
    const candidates = [source, original(2), display];
    assert.equal(selectOriginalPDF(candidates, 9)?.id, 1);
    assert.deepEqual(selectGuidePDF(candidates, 9), { source, display });
});

test("legacy translated PDF resolves to its parent's unique original after renaming", () => {
    const source = original(1);
    const display = translation();
    assert.equal(selectOriginalPDF([source, display], 9)?.id, 1);
    assert.equal(selectGuidePDF([source, display], 9)?.source.id, 1);
    assert.equal(selectOriginalPDF([source, display])?.id, 1);
});

test("multiple originals require selected identity, exact stored guide mapping or unique legacy match", () => {
    const sources = [original(1), original(2)];
    const display = translation();
    assert.equal(selectOriginalPDF([...sources, display], 9), null);
    assert.equal(selectOriginalPDF(sources), null);
    assert.equal(selectOriginalPDF([...sources, display], 2)?.id, 2);
    assert.equal(
        selectOriginalPDF(
            [...sources, { ...display, guideSourceKeys: ["SOURCE2"] }],
            9,
        )?.id,
        2,
    );
    assert.equal(
        selectOriginalPDF(
            [...sources, { ...display, matchingSourceKeys: ["SOURCE1"] }],
            9,
        )?.id,
        1,
    );
    assert.equal(
        selectOriginalPDF(
            [
                ...sources,
                { ...display, matchingSourceKeys: ["SOURCE1", "SOURCE2"] },
            ],
            9,
        ),
        null,
    );
});

test("stale, malformed, self-referential and conflicting recorded links never select a different sibling", () => {
    const source = original(1);
    const display = translation();
    for (const sourceURIs of [
        ["gone"],
        ["malformed"],
        [display.uri],
        [source.uri, "other"],
    ]) {
        assert.equal(
            selectOriginalPDF([source, { ...display, sourceURIs }], 9),
            null,
        );
        assert.equal(
            selectGuidePDF([source, { ...display, sourceURIs }], 9),
            null,
        );
    }
    assert.equal(
        selectOriginalPDF(
            [source, { ...display, guideSourceKeys: ["MISSING"] }],
            9,
        ),
        null,
    );
    assert.equal(
        selectOriginalPDF(
            [source, { ...display, guideSourceKeys: ["SOURCE1", "MISSING"] }],
            9,
        ),
        null,
    );
    assert.equal(selectOriginalPDF([source, display], 999), null);
});

test("stored attachment provenance wins over filename hints and older guide metadata", () => {
    const source = original(1);
    const display = {
        ...translation(),
        sourceURIs: [source.uri],
        guideSourceKeys: ["SOURCE2"],
        matchingSourceKeys: ["SOURCE2"],
    };
    assert.equal(selectOriginalPDF([source, original(2), display], 9)?.id, 1);
});

test("guide for selected original uses only its own translation, not a newer sibling's", () => {
    const source = original(1);
    const other = original(2);
    const own = { ...translation(9), sourceURIs: [source.uri] };
    const newer = {
        ...translation(10),
        sourceURIs: [other.uri],
        translationRank: 1000,
    };
    assert.equal(selectGuidePDF([source, other, own, newer], 1)?.display.id, 9);
    assert.equal(selectGuidePDF([source, other, newer], 1), null);
    assert.equal(selectGuidePDF([source, other, own, newer], 9)?.display.id, 9);
});

test("generated-to-generated cycles and missing standalone originals are unresolved", () => {
    const a = translation(9);
    const b = translation(10);
    a.sourceURIs = [b.uri];
    b.sourceURIs = [a.uri];
    assert.equal(selectOriginalPDF([a, b], 9), null);
    assert.equal(selectOriginalPDF([translation()], 9), null);
    assert.equal(selectOriginalPDF([original(1)], 1)?.id, 1);
});

test("attachment note provenance preserves user notes and round-trips stable Zotero URIs", () => {
    const uri = original(1).uri;
    const user = "<div><p>User note &amp; annotated text.</p></div>";
    const note = noteWithPDFSource(user, uri);
    assert.ok(note.includes(user));
    assert.deepEqual(pdfSourcesFromNote(note), [uri]);
    const updated = noteWithPDFSource(note, original(2).uri);
    assert.ok(updated.includes(user));
    assert.deepEqual(pdfSourcesFromNote(updated), [original(2).uri]);
    assert.deepEqual(
        pdfSourcesFromNote("<p>ScholarSplit-PDF-Source-v1: %bad%</p>"),
        ["invalid-source-marker"],
    );
    assert.deepEqual(pdfSourcesFromNote("<p>ScholarSplit-PDF-Source-v1:</p>"), [
        "invalid-source-marker",
    ]);
});
