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
