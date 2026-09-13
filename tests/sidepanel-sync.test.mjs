import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../sidepanel.js", import.meta.url), "utf8");

test("completed Chrome jobs commit the local ScholarSplit library scan", () => {
  assert.match(source, /\/api\/v1\/library\/scan/);
  assert.match(source, /JSON\.stringify\(\{ commit: true \}\)/);
  assert.match(source, /已收录到 ScholarSplit/);
});
