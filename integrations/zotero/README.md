# ScholarSplit Zotero sync bridge

`plugin/` now contains the complete AGPL-3.0-or-later ScholarSplit Zotero addon
source, including translation, reading guides, writing review, paper chat and
sync. Install the Release XPI, or run `npm ci`, `npm test` and `npm run build`
inside `plugin/`. It is not loaded by the Chrome extension.

The addon ID and preference prefix are retained to update existing PDF2zh guide
installations. This replaces that addon; the two cannot coexist with one ID.
New translated attachments record their original Zotero attachment key/URI;
renaming a file does not erase the association. Older files use an unambiguous
single original under the same parent, or require an explicit original choice.

The plugin reads a private pairing-token file and verifies it against the
configured loopback service, rather than assuming a personal installation path.
For custom roots set `extensions.zotero.pdf2zh.scholarSplitTokenPath` to
`<installation>/server/data/zotero-pairing-token` in Zotero's config editor.
Never put the token value in a preference. The server URL uses the existing
`extensions.zotero.pdf2zh.new_serverip` preference.

The remaining integration examples below are for maintainers embedding the
bridge in other addons; end users should install the complete XPI.

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

`host-plugin/defensiveWriting.ts` is the Zotero-hosted action for the
DeepSeek writing review. It sends the selected PDF to the local companion,
waits for a completed job, and uses Zotero's annotation API to save red
highlights with the reason and revision suggestion. It does not modify the PDF
or write Zotero's database directly. The host plugin wires this action into
the PDF context menu and reader sidebar.

`host-plugin/paperChat.ts` provides the separate paper-chat reader section.
Copy it with `paperChatUtils.ts` and `readingGuideUtils.ts` into the host's
`src/modules/`, register `PaperChatFactory.registerPane()` after locale setup,
and unregister it on shutdown. Copy `host-plugin/icons/paper-chat.svg` to the
host's `addon/content/icons/` and append the corresponding `host-plugin/locale/`
strings to the host's locale file. It reuses the host's existing locale helper,
PDF reader/base64 helper, and plugin configuration.
In a reader, paper chat reads the PDF actually open in that reader, even if it
is translated or bilingual. In the library, select a PDF explicitly when the
parent has multiple candidates. Citations reopen that supplied attachment at its
physical PDF page; unrelated readers are never used as a fallback.

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
