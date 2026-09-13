import assert from "node:assert/strict";
import test from "node:test";
import {
  buildGuidePayload,
  buildTranslatePayload,
  displayTitleForTab,
  fileNameFromUrl,
  mergeInvokedTab,
  jobMatchesTab,
  permissionPatternForUrl,
  pickTranslationFile,
  safeFileName,
  taskIdFromPayload,
  validatePdfBytes
} from "../lib.js";

test("restores the invoked tab URL when side panel query hides sensitive fields", () => {
  assert.deepEqual(
    mergeInvokedTab(
      { id: 18, active: true, url: "", title: "" },
      { id: 18, url: "https://arxiv.org/pdf/2608.09917", title: "WhichTok?" }
    ),
    {
      id: 18,
      active: true,
      url: "https://arxiv.org/pdf/2608.09917",
      title: "WhichTok?"
    }
  );
  assert.equal(
    mergeInvokedTab(
      { id: 19, active: true, url: "", title: "" },
      { id: 18, url: "https://arxiv.org/pdf/2608.09917", title: "WhichTok?" }
    ).url,
    ""
  );
});

test("normalizes source PDF names without generated suffixes", () => {
  assert.equal(safeFileName("paper.compare.pdf"), "paper.pdf");
  assert.equal(safeFileName("bad:name.pdf"), "bad_name.pdf");
  assert.equal(fileNameFromUrl("https://example.com/path/paper.pdf?token=secret"), "paper.pdf");
});

test("uses a short PDF label and restores only the matching document", () => {
  const signedUrl = "https://pdf.example.org/main.pdf?X-Amz-Signature=secret";
  assert.equal(displayTitleForTab({ url: signedUrl, title: signedUrl }), "main.pdf");
  assert.equal(jobMatchesTab({ sourceUrl: signedUrl }, { url: signedUrl }), true);
  assert.equal(jobMatchesTab({ sourceUrl: "https://example.org/old.pdf" }, { url: signedUrl }), false);
  assert.equal(jobMatchesTab({ originalTabId: 1 }, { id: 1, url: signedUrl }), false);
});

test("builds minimal permission patterns", () => {
  assert.equal(permissionPatternForUrl("https://journals.example.org/paper.pdf"), "https://journals.example.org/*");
  assert.equal(permissionPatternForUrl("file:///tmp/paper.pdf"), "file:///*");
  assert.throws(() => permissionPatternForUrl("chrome://settings"));
});

test("does not transport the DeepSeek API key", () => {
  const payload = buildTranslatePayload("paper.pdf", "cGRm");
  assert.equal(payload.useServerDeepSeekConfig, true);
  assert.equal(Object.hasOwn(payload, "llm_api"), false);
  assert.equal(payload.next_service, "deepseek");
  assert.equal(payload.compare, true);
  assert.equal(payload.noMono, true);
  assert.equal(buildGuidePayload("paper.pdf", "cGRm", "Title").title, "Title");
});

test("selects compare output and accepts either task id spelling", () => {
  assert.equal(
    pickTranslationFile({ fileList: ["paper.mono.pdf", "paper.compare.pdf"] }),
    "paper.compare.pdf"
  );
  assert.equal(taskIdFromPayload({ task_id: 42 }), "42");
});

test("validates a complete PDF and rejects HTML", () => {
  const pdf = new TextEncoder().encode(`%PDF-1.7\n${"x".repeat(5000)}\n%%EOF`).buffer;
  assert.equal(validatePdfBytes(pdf), true);
  const html = new TextEncoder().encode(`<html>${"x".repeat(5000)}</html>`).buffer;
  assert.throws(() => validatePdfBytes(html), /不是 PDF/);
});
