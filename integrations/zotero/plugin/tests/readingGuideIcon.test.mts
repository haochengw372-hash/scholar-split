import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("../", import.meta.url);

test("reader action uses a dedicated small icon instead of the addon logo", () => {
    const source = readFileSync(new URL("src/modules/readingGuide.ts", root), "utf8");
    const icon = readFileSync(new URL("addon/content/icons/defensive-writing.svg", root), "utf8");
    assert.match(source, /type: "defensive-writing",\s*icon: defensiveIcon/s);
    assert.match(icon, /width="16" height="16"/);
    assert.doesNotMatch(icon, /#D3F2CA/i);
});

test("addon logos use the small monochrome translation mark", () => {
    for (const filename of ["favicon.svg", "favicon@0.5x.svg"]) {
        const svg = readFileSync(new URL(`addon/content/icons/${filename}`, root), "utf8");
        assert.match(svg, /width="16" height="16"/);
        assert.match(svg, />译<\/text>/);
        assert.doesNotMatch(svg, /#D3F2CA/i);
    }
});
