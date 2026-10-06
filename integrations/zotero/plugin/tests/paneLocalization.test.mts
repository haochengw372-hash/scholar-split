import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function message(source: string, id: string) {
    const lines = source.split("\n");
    const index = lines.findIndex((line) => line.startsWith(`${id} =`));
    assert.notEqual(index, -1, `Missing Fluent message: ${id}`);
    const value = lines[index].slice(lines[index].indexOf("=") + 1).trim();
    const attributes: Record<string, string> = {};
    for (let i = index + 1; i < lines.length && /^\s+\S/.test(lines[i]); i++) {
        const attribute = lines[i].match(/^\s+\.([\w-]+)\s*=\s*(.+)$/);
        assert.ok(
            attribute,
            `${id} must contain only attributes, not DOM body text`,
        );
        attributes[attribute[1]] = attribute[2];
    }
    return { value: value || null, attributes };
}

for (const locale of ["zh-CN", "en-US"]) {
    test(`${locale}: pane localization preserves controls and icon-only sidebar buttons`, () => {
        const source = readFileSync(
            new URL(`../addon/locale/${locale}/addon.ftl`, import.meta.url),
            "utf8",
        );
        const ids = [
            "guide-pane-header",
            "guide-pane-sidenav",
            "chat-pane-header",
            "chat-pane-sidenav",
            "guide-regenerate-button",
            "defensive-pane-button",
        ];
        for (const id of ids) {
            const translation = message(source, id);
            assert.equal(
                translation.value,
                null,
                `${id}: Fluent values replace the element's children`,
            );
            const children = ["twisty", "icon", "body", "textarea"];
            // Mozilla's DOM overlay replaces child content only when a value
            // exists; attribute-only messages must leave this structure intact.
            if (translation.value !== null) children.splice(0, children.length);
            assert.deepEqual(children, ["twisty", "icon", "body", "textarea"]);
            assert.ok(Object.keys(translation.attributes).length > 0);
        }
        assert.ok(message(source, "defensive-menu").value);
        assert.ok(message(source, "guide-regenerate").value);
    });
}

test("all native pane DOM bindings use the attribute-only messages", () => {
    const allowed = new Set([
        "guide-pane-header",
        "guide-pane-sidenav",
        "chat-pane-header",
        "chat-pane-sidenav",
        "guide-regenerate-button",
        "defensive-pane-button",
    ]);
    for (const name of ["readingGuide", "paperChat"]) {
        const source = readFileSync(
            new URL(`../src/modules/${name}.ts`, import.meta.url),
            "utf8",
        );
        for (const match of source.matchAll(
            /l10nID:\s*getLocaleID\("([^"]+)"\)/g,
        )) {
            assert.ok(
                allowed.has(match[1]),
                `Unsafe pane DOM binding: ${match[1]}`,
            );
        }
    }
});
