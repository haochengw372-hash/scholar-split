import assert from "node:assert/strict";
import test from "node:test";
import { ChatRenderGate } from "../src/modules/paperChatUtils.ts";

test("late answers remain invalid after changing to another paper and back", () => {
    const gate = new ChatRenderGate();
    const first = gate.begin("1:PAPER_A");
    assert.ok(gate.current(first, "1:PAPER_A"));
    gate.invalidate();
    assert.equal(gate.current(first, "1:PAPER_A"), false);
    const second = gate.begin("1:PAPER_B");
    assert.equal(gate.current(first, "1:PAPER_A"), false);
    assert.ok(gate.current(second, "1:PAPER_B"));
    const backToA = gate.begin("1:PAPER_A");
    assert.equal(gate.current(first, "1:PAPER_A"), false);
    assert.equal(gate.current(second, "1:PAPER_B"), false);
    assert.ok(gate.current(backToA, "1:PAPER_A"));
    assert.equal(gate.current(backToA, "2:PAPER_A"), false);
});
