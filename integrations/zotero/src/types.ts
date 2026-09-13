/**
 * Wire types for the ScholarSplit Zotero bridge.
 *
 * These types deliberately contain no Zotero-specific numeric database IDs.
 * Library-scoped Zotero keys are stable enough to exchange with a local
 * service, while internal IDs are resolved only immediately before a command
 * is applied.
 */

export const SCHEMA_VERSION = "1.0" as const;

export interface EntityVersion {
  /** Zotero's monotonically increasing sync version, or 0 for unsynced data. */
  version: number;
  /** ISO dateModified is the deterministic tie-breaker for version 0/equality. */
  dateModified: string;
}

export interface CreatorSnapshot {
  creatorType: string;
  firstName?: string;
  lastName?: string;
  name?: string;
}

export interface ItemSnapshot extends EntityVersion {
  key: string;
  itemType: string;
  title: string;
  abstractNote?: string;
  date?: string;
  DOI?: string;
  url?: string;
  creators: CreatorSnapshot[];
  collectionKeys: string[];
  tags: string[];
  attachmentKeys: string[];
}

export interface CollectionSnapshot extends EntityVersion {
  key: string;
  name: string;
  parentKey: string | null;
}

export interface TagSnapshot {
  name: string;
  /** Sorted keys of regular items carrying the tag. */
  itemKeys: string[];
}

export interface AttachmentSnapshot extends EntityVersion {
  key: string;
  parentItemKey: string | null;
  title: string;
  filename: string | null;
  contentType: string | null;
  linkMode: number | null;
  url: string | null;
  /** Local paths never cross the bridge. */
  hasLocalFile: boolean;
}

export interface LibrarySnapshot {
  id: number;
  name: string;
  version: number;
}

export interface SnapshotEnvelope {
  schemaVersion: typeof SCHEMA_VERSION;
  bridgeId: string;
  capturedAt: string;
  library: LibrarySnapshot;
  cursor: {
    libraryVersion: number;
    sequence: number;
  };
  items: ItemSnapshot[];
  collections: CollectionSnapshot[];
  tags: TagSnapshot[];
  attachments: AttachmentSnapshot[];
}

export type EntityType = "item" | "collection";

export interface VersionPrecondition extends EntityVersion {
  entityType: EntityType;
  key: string;
}

interface CommandBase {
  commandId: string;
  libraryId: number;
  issuedAt: string;
  /** Every listed entity must exactly match before any write begins. */
  preconditions?: VersionPrecondition[];
}

export interface CollectionMembershipCommand extends CommandBase {
  type: "collection.addItems" | "collection.removeItems";
  collectionKey: string;
  itemKeys: string[];
}

export interface TagCommand extends CommandBase {
  type: "tag.add" | "tag.remove";
  tag: string;
  itemKeys: string[];
}

export interface CreateChildNoteCommand extends CommandBase {
  type: "note.createChild";
  parentItemKey: string;
  /** Zotero note HTML. Size and unsafe active content are checked by bridge. */
  noteHtml: string;
}

export interface ImportPaperCommand extends CommandBase {
  type: "item.importPaper";
  collectionKey: string;
  paper: {
    title: string;
    authors: string[];
    year?: number | null;
    doi?: string | null;
    abstract?: string;
    url?: string | null;
  };
  /** Absolute local PDF path resolved by the loopback service, not the browser. */
  attachmentPath: string;
}

export type BridgeCommand =
  | CollectionMembershipCommand
  | TagCommand
  | CreateChildNoteCommand
  | ImportPaperCommand;

export interface CommandBatch {
  schemaVersion: typeof SCHEMA_VERSION;
  commands: BridgeCommand[];
  nextCursor: string;
  pollAfterMs?: number;
}

export type AckStatus = "applied" | "conflict" | "rejected" | "failed";

export interface CommandAck {
  schemaVersion: typeof SCHEMA_VERSION;
  commandId: string;
  status: AckStatus;
  completedAt: string;
  result?: Record<string, unknown>;
  conflict?: {
    entityType: EntityType;
    key: string;
    expected: EntityVersion;
    actual: EntityVersion | null;
  };
  error?: {
    code: string;
    /** Must never contain the pairing token. */
    message: string;
  };
}

export interface BridgeTransport {
  sendSnapshot(snapshot: SnapshotEnvelope): Promise<void>;
  pollCommands(afterCursor: string): Promise<CommandBatch>;
  acknowledge(ack: CommandAck): Promise<void>;
}

export interface BridgeLogger {
  debug(message: string, context?: Record<string, unknown>): void;
  info(message: string, context?: Record<string, unknown>): void;
  warn(message: string, context?: Record<string, unknown>): void;
  error(message: string, context?: Record<string, unknown>): void;
}

export interface BridgeConfig {
  bridgeId: string;
  libraryIDs: number[];
  baseUrl: string;
  /** Kept only in memory and placed only in the Authorization header. */
  pairingToken: string;
  pollIntervalMs?: number;
  logger?: BridgeLogger;
}
