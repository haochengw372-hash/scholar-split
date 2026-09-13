import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../background.js", import.meta.url), "utf8");

test("opens the side panel before invoking another Chrome API", () => {
  const handler = source.slice(source.indexOf("chrome.action.onClicked"));
  const openIndex = handler.indexOf("chrome.sidePanel.open");
  const storageIndex = handler.indexOf("chrome.storage.session.set");

  assert.ok(openIndex >= 0);
  assert.ok(storageIndex >= 0);
  assert.ok(openIndex < storageIndex);
  assert.equal(handler.includes("addListener(async"), false);
});
