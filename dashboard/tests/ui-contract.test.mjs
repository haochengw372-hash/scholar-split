import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const [html, css, js] = await Promise.all([
  readFile(new URL("index.html", root), "utf8"),
  readFile(new URL("styles.css", root), "utf8"),
  readFile(new URL("app.js", root), "utf8"),
]);

test("dashboard stays dependency-free and uses served local assets", () => {
  assert.match(html, /\/workspace-assets\/styles\.css/);
  assert.match(html, /\/workspace-assets\/app\.js/);
  assert.match(html, /\/workspace-assets\/icon-128\.png/);
  assert.doesNotMatch(html + css, /https?:\/\//);
  assert.doesNotMatch(css, /@import/);
});

test("dashboard preserves core navigation and accessibility landmarks", () => {
  for (const route of ["library", "reviews", "reading", "tasks", "settings"]) {
    assert.match(html, new RegExp(`data-route="${route}"`));
  }
  assert.doesNotMatch(html, /\/classic/);
  assert.match(html, /<main id="workspace"/);
  assert.match(html, /role="search"/);
});

test("frontend calls only the v1 workspace contracts for research actions", () => {
  for (const endpoint of [
    "/papers", "/collections", "/review-projects", "/recommendations", "/jobs",
    "/library/scan", "/acquisitions/preview", "/acquisitions/confirm",
  ]) {
    assert.ok(js.includes(endpoint), `missing API endpoint ${endpoint}`);
  }
  assert.doesNotMatch(js, /\/api\/v1\/review\/projects/);
});

test("library separates ScholarSplit intake from synced Zotero folders", () => {
  assert.match(js, /ScholarSplit 文献库/);
  assert.match(js, /Zotero 文献库/);
  assert.match(js, /插件阅读记录/);
  assert.match(js, /旧翻译归档/);
  assert.match(js, /放入 Zotero 文件夹/);
  assert.match(js, /zotero-collections/);
});
