import assert from "node:assert/strict";
import test from "node:test";
import { pdfAttachment, pdfHelper } from "./pdfUploadFixture.mts";

test("FileReader failures reject without treating loadend as a successful read", async () => {
    const failure = new Error("synthetic FileReader failure");
    await assert.rejects(
        pdfHelper(new Uint8Array(), failure).readPDFAsBase64(
            "/tmp/current.pdf",
        ),
        failure,
    );
});

test("real PDF helper strips FileReader data-URL prefix and preserves all bytes", async () => {
    const bytes = Uint8Array.from([
        37, 80, 68, 70, 45, 49, 46, 55, 10, 0, 128, 255, 10, 37, 37, 69, 79, 70,
    ]);
    const helper = pdfHelper(bytes);
    const result = await helper.prepareFileData(pdfAttachment);
    assert.equal(result.fileName, "current bilingual.pdf");
    assert.equal(result.base64, Buffer.from(bytes).toString("base64"));
    assert.ok(!result.base64.startsWith("data:"));
    assert.deepEqual(Buffer.from(result.base64, "base64"), Buffer.from(bytes));
});

test("JSON upload contract carries pure base64 for chat, guide and writing review", async () => {
    const bytes = new TextEncoder().encode("%PDF-1.7\nsynthetic text\n%%EOF");
    const result = await pdfHelper(bytes).prepareFileData(pdfAttachment);
    const body = JSON.parse(
        JSON.stringify({
            fileName: result.fileName,
            fileContent: result.base64,
        }),
    );
    assert.match(body.fileContent, /^[A-Za-z0-9+/]+={0,2}$/);
    assert.equal(
        Buffer.from(body.fileContent, "base64").toString(),
        new TextDecoder().decode(bytes),
    );
});
