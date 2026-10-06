import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("owned frontend uses Apache while upstream integrations retain AGPL", () => {
  assert.match(fs.readFileSync("LICENSE", "utf8"), /Apache License\s+Version 2\.0/);
  assert.equal(JSON.parse(fs.readFileSync("package.json")).license, "Apache-2.0");
  assert.equal(JSON.parse(fs.readFileSync("release.json")).licenses.serverAndZotero, "AGPL-3.0-or-later");
  assert.match(fs.readFileSync("integrations/server/host/LICENSE", "utf8"), /GNU AFFERO GENERAL PUBLIC LICENSE/);
  assert.equal(JSON.parse(fs.readFileSync("integrations/zotero/plugin/package.json")).license, "AGPL-3.0-or-later");
});

test("citation is at the top and packaged with attribution without new license restrictions", () => {
  const readme = fs.readFileSync("README.md", "utf8");
  assert.ok(readme.startsWith("> 使用请引用："));
  assert.match(readme.split("\n")[0], /Haocheng Wang/);
  assert.match(readme.split("\n")[0], /不是额外的许可证限制/);
  const citation = fs.readFileSync("CITATION.cff", "utf8");
  assert.match(citation, /family-names: Wang/);
  assert.match(citation, /given-names: Haocheng/);
  assert.match(fs.readFileSync("NOTICE", "utf8"), /ScholarSplit contributors/);
  assert.match(fs.readFileSync("scripts/package.sh", "utf8"), /"NOTICE", "CITATION.cff"/);
});
