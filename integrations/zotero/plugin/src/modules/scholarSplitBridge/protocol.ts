import {
  BridgeCommand,
  CommandBatch,
  EntityVersion,
  SCHEMA_VERSION,
  SnapshotEnvelope,
} from "./types";

export const ALLOWED_COMMAND_TYPES = Object.freeze([
  "collection.addItems",
  "collection.removeItems",
  "tag.add",
  "tag.remove",
  "note.createChild",
  "item.importPaper",
] as const);

const ALLOWED_COMMAND_SET: ReadonlySet<string> = new Set(ALLOWED_COMMAND_TYPES);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1"]);
const MAX_NOTE_BYTES = 512 * 1024;
const ZOTERO_KEY = /^[A-Z0-9]{8}$/u;

export function assertLoopbackBaseUrl(value: string): URL {
  const url = new URL(value);
  const hostname = url.hostname === "[::1]" ? "::1" : url.hostname;
  if (!LOOPBACK_HOSTS.has(hostname)) {
    throw new Error("Zotero bridge endpoint must use a loopback host");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Zotero bridge endpoint must use HTTP or HTTPS");
  }
  if (url.username || url.password) {
    throw new Error("Zotero bridge endpoint must not contain credentials");
  }
  url.hash = "";
  return url;
}

export function createAuthorizationHeaders(pairingToken: string): HeadersInit {
  if (!pairingToken || /[\r\n]/u.test(pairingToken)) {
    throw new Error("A valid in-memory pairing token is required");
  }
  return Object.freeze({
    Authorization: `Bearer ${pairingToken}`,
    "Content-Type": "application/json",
  });
}

/**
 * Total ordering used for both snapshot normalization and optimistic conflict
 * checks: Zotero version first, then ISO dateModified. No wall-clock guessing.
 */
export function compareEntityVersion(a: EntityVersion, b: EntityVersion): number {
  if (a.version !== b.version) return a.version < b.version ? -1 : 1;
  if (a.dateModified === b.dateModified) return 0;
  return a.dateModified < b.dateModified ? -1 : 1;
}

export function entityVersionEquals(a: EntityVersion, b: EntityVersion): boolean {
  return compareEntityVersion(a, b) === 0;
}

function sortedUnique(values: string[]): string[] {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b, "en"));
}

/** Sort every set-like field so identical Zotero state yields identical JSON. */
export function canonicalizeSnapshot(snapshot: SnapshotEnvelope): SnapshotEnvelope {
  return {
    ...snapshot,
    items: snapshot.items
      .map((item) => ({
        ...item,
        collectionKeys: sortedUnique(item.collectionKeys),
        tags: sortedUnique(item.tags),
        attachmentKeys: sortedUnique(item.attachmentKeys),
      }))
      .sort((a, b) => a.key.localeCompare(b.key, "en")),
    collections: [...snapshot.collections].sort((a, b) =>
      a.key.localeCompare(b.key, "en"),
    ),
    tags: snapshot.tags
      .map((tag) => ({ ...tag, itemKeys: sortedUnique(tag.itemKeys) }))
      .sort((a, b) => a.name.localeCompare(b.name, "en")),
    attachments: [...snapshot.attachments].sort((a, b) =>
      a.key.localeCompare(b.key, "en"),
    ),
  };
}

export function assertCommandBatch(value: unknown): asserts value is CommandBatch {
  if (!value || typeof value !== "object") throw new Error("Invalid command batch");
  const batch = value as Partial<CommandBatch>;
  if (batch.schemaVersion !== SCHEMA_VERSION || !Array.isArray(batch.commands)) {
    throw new Error("Unsupported command batch schema");
  }
  if (typeof batch.nextCursor !== "string") throw new Error("Missing next cursor");
  // Individual commands are validated during execution so malformed/unknown
  // commands can receive a deterministic rejected acknowledgement.
  for (const command of batch.commands) {
    if (!command || typeof command !== "object") throw new Error("Invalid command entry");
  }
}

export function assertAllowedCommand(value: unknown): asserts value is BridgeCommand {
  if (!value || typeof value !== "object") throw new Error("Invalid command");
  const command = value as Partial<BridgeCommand> & Record<string, unknown>;
  if (typeof command.type !== "string" || !ALLOWED_COMMAND_SET.has(command.type)) {
    throw new Error("Command type is not allowed");
  }
  if (typeof command.commandId !== "string" || command.commandId.length < 1) {
    throw new Error("Command ID is required");
  }
  if (command.commandId.length > 200) throw new Error("Command ID is too long");
  if (!Number.isSafeInteger(command.libraryId) || Number(command.libraryId) < 0) {
    throw new Error("Invalid library ID");
  }
  if (typeof command.issuedAt !== "string" || Number.isNaN(Date.parse(command.issuedAt))) {
    throw new Error("Invalid command issue time");
  }
  if (command.preconditions !== undefined) {
    if (!Array.isArray(command.preconditions)) throw new Error("Invalid command preconditions");
    for (const value of command.preconditions) {
      if (!value || typeof value !== "object") throw new Error("Invalid precondition");
      const precondition = value as unknown as Record<string, unknown>;
      if (precondition.entityType !== "item" && precondition.entityType !== "collection") {
        throw new Error("Invalid precondition entity type");
      }
      if (!isZoteroKey(precondition.key)) throw new Error("Invalid precondition key");
      if (!Number.isSafeInteger(precondition.version) || Number(precondition.version) < 0) {
        throw new Error("Invalid precondition version");
      }
      if (typeof precondition.dateModified !== "string") {
        throw new Error("Invalid precondition modification date");
      }
      assertOnlyKeys(precondition, ["entityType", "key", "version", "dateModified"]);
    }
  }
  if (command.type === "note.createChild") {
    assertOnlyKeys(command, [
      "commandId",
      "libraryId",
      "issuedAt",
      "preconditions",
      "type",
      "parentItemKey",
      "noteHtml",
    ]);
    if (!isZoteroKey(command.parentItemKey)) {
      throw new Error("Parent item key is required");
    }
    if (typeof command.noteHtml !== "string") throw new Error("Note HTML is required");
    if (new TextEncoder().encode(command.noteHtml).byteLength > MAX_NOTE_BYTES) {
      throw new Error("Note HTML exceeds size limit");
    }
  } else if (command.type === "item.importPaper") {
    assertOnlyKeys(command, [
      "commandId",
      "libraryId",
      "issuedAt",
      "preconditions",
      "type",
      "collectionKey",
      "paper",
      "attachmentPath",
    ]);
    if (!isZoteroKey(command.collectionKey)) throw new Error("Collection key is required");
    if (!isNonEmptyString(command.attachmentPath) || command.attachmentPath.length > 4096) {
      throw new Error("Attachment path is required");
    }
    if (!/^(?:\/|[A-Za-z]:[\\/]|\\\\)/u.test(command.attachmentPath) || !/\.pdf$/iu.test(command.attachmentPath)) {
      throw new Error("Attachment path must be an absolute PDF path");
    }
    const paper = command.paper as Record<string, unknown>;
    if (!paper || typeof paper !== "object") throw new Error("Paper metadata is required");
    assertOnlyKeys(paper, ["title", "authors", "year", "doi", "abstract", "url"]);
    if (!isNonEmptyString(paper.title) || paper.title.length > 2000) throw new Error("Paper title is required");
    if (!Array.isArray(paper.authors) || !paper.authors.every((author) => isNonEmptyString(author) && author.length <= 500)) {
      throw new Error("Paper authors must be text values");
    }
    if (paper.year != null && (!Number.isSafeInteger(paper.year) || Number(paper.year) < 1000 || Number(paper.year) > 3000)) {
      throw new Error("Paper year is invalid");
    }
  } else {
    if (
      !Array.isArray(command.itemKeys) ||
      command.itemKeys.length < 1 ||
      !command.itemKeys.every(isZoteroKey) ||
      new Set(command.itemKeys).size !== command.itemKeys.length
    ) {
      throw new Error("Item keys are required");
    }
    if (command.type.startsWith("collection.")) {
      assertOnlyKeys(command, [
        "commandId",
        "libraryId",
        "issuedAt",
        "preconditions",
        "type",
        "collectionKey",
        "itemKeys",
      ]);
      if (!isZoteroKey(command.collectionKey)) throw new Error("Collection key is required");
    }
    if (command.type.startsWith("tag.")) {
      assertOnlyKeys(command, [
        "commandId",
        "libraryId",
        "issuedAt",
        "preconditions",
        "type",
        "tag",
        "itemKeys",
      ]);
      if (!isNonEmptyString(command.tag) || command.tag.length > 500) {
        throw new Error("Tag is required");
      }
    }
  }
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isZoteroKey(value: unknown): value is string {
  return typeof value === "string" && ZOTERO_KEY.test(value);
}

function assertOnlyKeys(value: Record<string, unknown>, keys: string[]): void {
  const allowed = new Set(keys);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new Error("Command contains unsupported fields");
  }
}

/**
 * Active content is not needed for research notes. Zotero itself performs its
 * normal note cleanup; this removes the highest-risk constructs before that.
 */
export function sanitizeNoteHtml(html: string): string {
  return html
    .replace(/<(script|iframe|object|embed|style)\b[^>]*>[\s\S]*?<\/\1\s*>/giu, "")
    .replace(/<(script|iframe|object|embed|style)\b[^>]*\/?\s*>/giu, "")
    .replace(/\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/giu, "")
    .replace(/\s(?:href|src)\s*=\s*(["'])\s*javascript:[\s\S]*?\1/giu, "");
}

export function noteReplayMarker(commandId: string): string {
  const encoded = commandId.replace(/[^A-Za-z0-9._:-]/gu, "_");
  return `<span data-scholar-split-command-id="${encoded}" style="display:none"></span>`;
}
