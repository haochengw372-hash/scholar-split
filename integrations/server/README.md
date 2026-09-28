# ScholarSplit server integration

This directory is the local Flask/SQLite integration for ScholarSplit. It is
designed to be copied into an AGPL-compatible local translation server as a
`scholarsplit` Python package together with the `dashboard/` directory.

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

`patches/reading-guide-json-retry.patch` adds one bounded retry only when the
model response cannot be parsed as JSON. Apply it from the compatible host
server root with `patch -p1 < scholarsplit/patches/reading-guide-json-retry.patch`.

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
