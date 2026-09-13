# ScholarSplit Zotero sync bridge

This directory is a standalone, AGPL-3.0-or-later integration intended to be
copied or bundled into a Zotero 7 plugin. It is not loaded by the Chrome
extension in the repository root.

## Boundary and data flow

The bridge reads regular items, collections, tags, and attachment metadata via
the supported `Zotero.Items`, `Zotero.Collections`, and `Zotero.Libraries` APIs.
It observes changes through `Zotero.Notifier`, publishes canonical snapshots to
a loopback companion, polls that companion for commands, and posts an
acknowledgement for every command it attempts.

It never opens or writes `zotero.sqlite`. Attachment paths and contents do not
leave Zotero: a snapshot exposes only attachment metadata and a boolean saying
whether a local file exists.

Only these mutation commands are accepted:

- `collection.addItems` and `collection.removeItems`
- `tag.add` and `tag.remove`
- `note.createChild`
- `item.importPaper` — imports one PDF resolved from ScholarSplit's trusted
  local artifact index and places the new parent item in a synced collection

All entity references are Zotero library-scoped keys, never internal database
IDs. Unknown command types and commands for an unconfigured library are rejected.
The browser cannot submit an arbitrary filesystem path: the loopback service
resolves the attachment from its local database before queuing the command.

## Pairing and transport

The companion base URL must use the literal loopback host `127.0.0.1` or `::1`.
Pairing is performed by the host plugin or its setup UI. Pass the
resulting high-entropy token directly to `ScholarSplitZoteroBridge`; do not put it
in Zotero preferences, source code, logs, snapshots, acknowledgements, or crash
reports. The default transport retains it in memory only and sends it solely as
an `Authorization: Bearer ...` header. Restarting Zotero therefore requires the
host's approved pairing flow to supply it again.

The loopback service implements these routes under `/api/v1`:

- `POST /api/v1/zotero/snapshots`
- `GET /api/v1/zotero/commands?bridgeId=...&after=...`
- `POST /api/v1/zotero/acks`

Wire contracts are JSON Schema 2020-12 files in `schemas/`. The transport is
replaceable through the `BridgeTransport` interface, so the schema does not
depend on a particular HTTP framework or ScholarSplit server implementation.

## Determinism, conflicts, and replay

Snapshots sort entities by Zotero key and sort every set-like field. An entity
version is the tuple `(version, dateModified)`, compared in that order. Before
any mutation, every supplied precondition must exactly match the current Zotero
tuple. A missing or changed entity yields a `conflict` acknowledgement and no
write begins.

Collection and tag operations are naturally idempotent. Child notes embed a
hidden marker derived from `commandId`; a retry searches the parent's existing
notes and returns the original note key instead of creating a duplicate. The
poll cursor advances only after every acknowledgement in the batch succeeds.
The loopback service must keep command IDs unique and retain commands until their
acknowledgement is received.

## Zotero plugin integration

Include `src/` in the plugin's TypeScript build and initialize the bridge after
Zotero has finished loading:

```ts
import { ScholarSplitZoteroBridge } from "./scholar-split-zotero";

const bridge = new ScholarSplitZoteroBridge({
  bridgeId: "zotero-desktop-main",
  libraryIDs: [Zotero.Libraries.userLibraryID],
  baseUrl: "http://127.0.0.1:8890/api/v1/",
  pairingToken: tokenFromInteractivePairing,
});

await bridge.start();
// In the plugin shutdown hook:
bridge.stop();
```

If the host already has an authenticated local IPC layer, implement
`BridgeTransport` and pass it as the constructor's second argument. Never log
the bridge config object, request headers, or the token.

## Verification

The repository-level contract check has no third-party dependencies:

```bash
python3 tests/test_zotero_bridge_contract.py
```

The fixtures are examples and conformance inputs; they contain no user data.

## License

All files under `integrations/zotero/` are licensed under the GNU Affero General
Public License, version 3 or (at your option) any later version. See `LICENSE`.
This directory-level notice overrides the repository's MIT license for this
integration only.
