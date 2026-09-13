import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [source, html, css, manifestText] = await Promise.all([
  readFile(new URL("../sidepanel.js", import.meta.url), "utf8"),
  readFile(new URL("../sidepanel.html", import.meta.url), "utf8"),
  readFile(new URL("../sidepanel.css", import.meta.url), "utf8"),
  readFile(new URL("../manifest.json", import.meta.url), "utf8"),
]);
const manifest = JSON.parse(manifestText);

test("completed Chrome jobs commit the local ScholarSplit library scan", () => {
  assert.match(source, /\/api\/v1\/library\/scan/);
  assert.match(source, /JSON\.stringify\(\{ commit: true \}\)/);
  assert.match(source, /已收录到 ScholarSplit/);
});

test("remote PDFs use Chrome's download session and stale jobs require a URL match", () => {
  assert.ok(manifest.permissions.includes("downloads"));
  assert.match(source, /chrome\.downloads\.download/);
  assert.match(source, /chrome\.downloads\.search/);
  assert.match(source, /\/api\/v1\/pdf\/read-local/);
  assert.match(source, /chrome\.downloads\.removeFile/);
  assert.match(source, /jobMatchesTab\(saved, activeTab\)/);
  assert.doesNotMatch(source, /fetch\(tab\.url, \{ credentials/);
});

test("failure state has an explicit retry and uses the monochrome console style", () => {
  assert.match(html, /id="retryCurrent"/);
  assert.match(source, /retryCurrent\.addEventListener/);
  assert.match(css, /--ink: #171717/);
  assert.match(css, /background: #222/);
  assert.doesNotMatch(css, /--blue|--gold|--red/);
});
