# ScholarSplit server integration

This directory includes both the Flask/SQLite workspace integration and, under
`host/`, the complete AGPL-compatible PDF translation server. Use the repository
installer to assemble the `scholarsplit` Python package, dashboard and host.

It registers `/workspace`, `/workspace-assets/*`, and `/api/v1/*`. Existing
translation, guide, task, history, and file routes remain owned by the host
service, but the old page is not exposed as a workspace navigation entry.

The integration stores only local workspace state under
`data/scholarsplit.sqlite3`. The Zotero pairing token is generated at
`data/zotero-pairing-token` with file mode `0600` and is never returned by an
HTTP endpoint.

Files in this integration directory are distributed under AGPL-3.0-or-later
when combined with the supported AGPL host service. The independent dashboard
and Chrome extension remain MIT licensed.

The bundled host already implements one bounded retry for invalid guide JSON.
`patches/reading-guide-json-retry.patch` is only for older external hosts; do not
apply it again to this release.

## Zotero writing review

`POST /api/v1/writing/defensive` accepts a PDF from the local Zotero plugin and
starts a persisted `defensive_writing` job. The server reads its existing
DeepSeek profile; the model key never enters Zotero. The plugin polls
`/api/v1/jobs/<id>` and creates red Zotero highlight annotations only for
model-quoted passages found exactly once and located in the PDF text layer.
Each annotation comment gives the review reason and a revision direction.
Unlocated suggestions are counted, not highlighted. Repeating the action
skips existing ScholarSplit highlights on the same page and quote.

The editorial rubric was informed by the review-first, evidence-preserving
approach in these public GitHub academic-writing skills; their code is not
bundled here:

- https://github.com/David-Saeteros/claude-skills/blob/main/skills/academic-writing/SKILL.md
- https://github.com/YSLAB-ai/manuscript-writing/blob/main/SKILL.md
- https://github.com/YSLAB-ai/manuscript-writing/blob/main/references/revision-checklist.md

The review flags wording for human judgment. It does not score authorship,
change the PDF bytes, or claim that a necessary scientific limitation is bad
writing. The plugin writes annotations through `Zotero.Annotations.saveFromJSON`,
never by directly editing Zotero's database.

## Paper chat

The Zotero reader's **论文问答** section reads the selected original PDF without
requiring a translated attachment or a reading guide. It uses the independent
research model profile and the existing server-side DeepSeek key.

- `POST /api/v1/paper-chat/documents` registers base64 `fileContent` and `fileName`,
  returning `documentId` (PDF SHA-256), `pageCount`, and `textPageCount`.
- `GET /api/v1/paper-chat/documents/<id>/messages` restores that PDF's conversation.
- `POST /api/v1/paper-chat/documents/<id>/ask` accepts `question` and returns
  `answer`, verified `citations` (`page`, `quote`), `model`, and `contextPages`.
- `DELETE /api/v1/paper-chat/documents/<id>/messages` clears the conversation.

Page numbers refer to physical PDF pages, starting at 1. Short papers are supplied
in full; longer papers use model-generated English search terms and local BM25
passage selection, reported as `retrieved_passages` coverage. JSON or exact-quote
validation errors get one retry; provider failures remain visible. Only successful
question/answer pairs are saved. Conversations and extracted text persist under
`data/paper-chat/conversations.sqlite3` (mode `0600`, private directory `0700`).
No PDF copy, embeddings dependency, or model key is stored in that database.
