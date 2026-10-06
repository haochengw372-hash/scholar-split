import assert from "node:assert/strict";
import test from "node:test";
import {
    escapeHTML,
    canonicalPDFStem,
    guideResultFromTask,
    isOriginalPDF,
    storedGuideFromNoteHTML,
    storedGuideToNoteHTML,
    sourceMatchesTranslation,
    translatedAttachmentRank,
    type StoredGuide,
} from "../src/modules/readingGuideUtils.ts";

test("ranks the selected translated PDF above newer siblings", () => {
    const selected = translatedAttachmentRank(
        "paper.mono.pdf",
        "DeepSeek mono",
        4,
        4,
    );
    const newer = translatedAttachmentRank(
        "paper.dual.pdf",
        "DeepSeek dual",
        9,
        4,
    );
    assert.ok(selected > newer);
    assert.equal(translatedAttachmentRank("paper.pdf", "Original", 10), 0);
    assert.equal(translatedAttachmentRank("DeepSeek-R1-paper.pdf", "", 11), 0);
    assert.equal(
        translatedAttachmentRank(
            "dual-process-theory.pdf",
            "Dual-process theory",
            12,
        ),
        0,
    );
    assert.ok(
        translatedAttachmentRank(
            "paper.pdf",
            "Paper title-deepseek-compare",
            13,
        ) > 0,
    );
    assert.ok(
        translatedAttachmentRank("paper.pdf", "deepseek-compare", 14) > 0,
    );
    assert.ok(
        translatedAttachmentRank("paper.pdf", "deepseek-crop-compare", 15) > 0,
    );
});

test("recognizes originals without confusing generated layouts", () => {
    assert.equal(isOriginalPDF("paper.pdf"), true);
    assert.equal(isOriginalPDF("paper.mono.pdf"), false);
    assert.equal(isOriginalPDF("paper.compare.pdf"), false);
    assert.equal(isOriginalPDF("paper.zh-CN.LR_dual.pdf"), false);
    assert.ok(translatedAttachmentRank("paper.zh-CN.TB_dual.pdf", "", 2) > 0);
    assert.equal(canonicalPDFStem("Paper.zh-CN.LR_dual.pdf"), "paper");
    assert.equal(
        sourceMatchesTranslation(
            "Paper.pdf",
            "Paper.no_watermark.zh-CN.mono.pdf",
        ),
        true,
    );
});

test("parses guide completion independently from PDF file results", () => {
    const guide = { title: "标题", oneSentence: "核心" };
    assert.deepEqual(
        guideResultFromTask(
            {
                taskId: "g1",
                finished: true,
                status: "完成",
                result: {
                    status: "success",
                    kind: "guide",
                    guideId: "abc",
                    guide,
                },
            },
            "g1",
        )?.guide,
        guide,
    );
    assert.equal(
        guideResultFromTask(
            {
                taskId: "g1",
                finished: true,
                status: "完成",
                fileList: ["x.pdf"],
            },
            "g1",
        ),
        null,
    );
});

test("stores guide JSON in a round-trippable and escaped Zotero note", () => {
    const stored: StoredGuide = {
        version: 1,
        parentItemKey: "PARENT",
        sourceAttachmentKey: "SOURCE",
        displayAttachmentKey: "DISPLAY",
        generatedAt: "2026-09-11T00:00:00Z",
        result: {
            status: "success",
            kind: "guide",
            guideId: "abc",
            modelName: "deepseek-flash",
            guide: {
                title: '<script>alert("x")</script>',
                oneSentence: "A & B",
                findings: [{ claim: "发现" }],
                advancedStatistics: {
                    summary: "使用贝叶斯层级模型估计组间差异。",
                    methods: [
                        {
                            name: "贝叶斯层级模型",
                            role: "部分汇聚各组估计",
                            assumptions: "层级结构设定正确",
                            interpretation: "用后验分布与可信区间解释",
                            page: "pp. 4-6",
                        },
                    ],
                },
            },
        },
    };
    const html = storedGuideToNoteHTML(stored);
    assert.ok(!html.includes('<script>alert("x")</script>'));
    assert.ok(html.includes("&lt;script&gt;"));
    assert.ok(html.includes("高级统计方法拆解"));
    assert.ok(html.includes("贝叶斯层级模型"));
    assert.deepEqual(storedGuideFromNoteHTML(html), stored);
    assert.equal(
        escapeHTML('<img onerror="x">'),
        "&lt;img onerror=&quot;x&quot;&gt;",
    );
});
