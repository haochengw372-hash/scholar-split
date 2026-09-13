/// <reference path="./zotero-api.d.ts" />

import {
  BridgeCommand,
  BridgeConfig,
  BridgeLogger,
  BridgeTransport,
  CommandAck,
  EntityVersion,
  SCHEMA_VERSION,
  VersionPrecondition,
} from "./types";
import {
  assertAllowedCommand,
  entityVersionEquals,
  noteReplayMarker,
  sanitizeNoteHtml,
} from "./protocol";
import { createLibrarySnapshot } from "./snapshot";
import { LoopbackTransport } from "./transport";

const DEFAULT_POLL_INTERVAL_MS = 2_000;
const MIN_POLL_INTERVAL_MS = 500;
const MAX_POLL_INTERVAL_MS = 60_000;
const MAX_RECENT_COMMANDS = 1_000;

const NOOP_LOGGER: BridgeLogger = Object.freeze({
  debug() {},
  info() {},
  warn() {},
  error() {},
});

class CommandError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

class ConflictError extends CommandError {
  constructor(
    readonly precondition: VersionPrecondition,
    readonly actual: EntityVersion | null,
  ) {
    super("VERSION_CONFLICT", "A Zotero entity changed after the command was prepared");
  }
}

/**
 * Runtime bridge embedded by a Zotero 7 plugin bootstrap.
 *
 * It uses public Zotero data APIs and Notifier only. It never opens or writes
 * zotero.sqlite, and it will execute only the command union in types.ts.
 */
export class ScholarSplitZoteroBridge {
  private readonly logger: BridgeLogger;
  private readonly transport: BridgeTransport;
  private readonly libraryIDs: ReadonlySet<number>;
  private readonly bridgeId: string;
  private readonly pollIntervalMs: number;
  private notifierID: string | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private snapshotTimer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private commandCursor = "";
  private snapshotSequence = 0;
  private readonly recentlyApplied = new Map<string, Record<string, unknown>>();

  private readonly observer: Zotero.Notifier.Observer = {
    notify: (_event, type, ids) => {
      if (type !== "item" && type !== "collection") return;
      const touchesConfiguredLibrary = ids.some((id) => {
        const entity =
          type === "item" ? Zotero.Items.get(id) : Zotero.Collections.get(id);
        // Deleted entities may no longer resolve; resnapshot all configured
        // libraries in that case so deletions are still represented.
        return !entity || this.libraryIDs.has(entity.libraryID);
      });
      if (touchesConfiguredLibrary) this.scheduleSnapshot();
    },
  };

  constructor(config: BridgeConfig, transport?: BridgeTransport) {
    if (!config.bridgeId) throw new Error("bridgeId is required");
    if (!config.libraryIDs.length) throw new Error("At least one library is required");
    this.bridgeId = config.bridgeId;
    this.libraryIDs = new Set(config.libraryIDs);
    this.pollIntervalMs = clampPollInterval(config.pollIntervalMs);
    this.logger = config.logger || NOOP_LOGGER;
    this.transport =
      transport || new LoopbackTransport(config.baseUrl, config.pairingToken, config.bridgeId);
    // Deliberately do not retain config or log it: config contains pairingToken.
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.notifierID = Zotero.Notifier.registerObserver(
      this.observer,
      ["item", "collection"],
      `scholar-split-${this.bridgeId}`,
      1,
    );
    try {
      await this.publishSnapshots();
    } catch (error) {
      this.logError("Initial Zotero snapshot failed", error);
    }
    this.schedulePoll(0);
  }

  stop(): void {
    this.running = false;
    if (this.notifierID) Zotero.Notifier.unregisterObserver(this.notifierID);
    this.notifierID = null;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.snapshotTimer) clearTimeout(this.snapshotTimer);
    this.pollTimer = null;
    this.snapshotTimer = null;
  }

  /** Public for plugin lifecycle hooks and deterministic integration tests. */
  async publishSnapshots(): Promise<void> {
    for (const libraryID of [...this.libraryIDs].sort((a, b) => a - b)) {
      const snapshot = await createLibrarySnapshot(
        this.bridgeId,
        libraryID,
        ++this.snapshotSequence,
      );
      await this.transport.sendSnapshot(snapshot);
    }
  }

  /** Apply exactly one already-polled command and return its wire acknowledgement. */
  async executeCommand(command: unknown): Promise<CommandAck> {
    const completedAt = () => new Date().toISOString();
    const commandId =
      command &&
      typeof command === "object" &&
      typeof (command as Record<string, unknown>).commandId === "string"
        ? String((command as Record<string, unknown>).commandId).slice(0, 200)
        : "unknown";
    try {
      try {
        assertAllowedCommand(command);
      } catch (error) {
        throw new CommandError("INVALID_COMMAND", safeErrorMessage(error));
      }
      if (!this.libraryIDs.has(command.libraryId)) {
        throw new CommandError("LIBRARY_NOT_ALLOWED", "Command targets an unconfigured library");
      }

      const replayResult = this.recentlyApplied.get(command.commandId);
      if (replayResult) {
        return {
          schemaVersion: SCHEMA_VERSION,
          commandId,
          status: "applied",
          completedAt: completedAt(),
          result: { ...replayResult, replayed: true },
        };
      }

      let result: Record<string, unknown> = {};
      await Zotero.DB.executeTransaction(async () => {
        // Version checks and writes share one Zotero transaction, preventing a
        // notifier-driven edit from slipping between the check and mutation.
        await this.assertPreconditions(command);
        result = await this.applyAllowedCommand(command);
      });
      this.rememberApplied(command.commandId, result);
      return {
        schemaVersion: SCHEMA_VERSION,
        commandId,
        status: "applied",
        completedAt: completedAt(),
        result,
      };
    } catch (error) {
      if (error instanceof ConflictError) {
        return {
          schemaVersion: SCHEMA_VERSION,
          commandId,
          status: "conflict",
          completedAt: completedAt(),
          conflict: {
            entityType: error.precondition.entityType,
            key: error.precondition.key,
            expected: {
              version: error.precondition.version,
              dateModified: error.precondition.dateModified,
            },
            actual: error.actual,
          },
        };
      }
      const commandError = error instanceof CommandError ? error : null;
      return {
        schemaVersion: SCHEMA_VERSION,
        commandId,
        status: commandError ? "rejected" : "failed",
        completedAt: completedAt(),
        error: {
          code: commandError?.code || "COMMAND_FAILED",
          message: safeErrorMessage(error),
        },
      };
    }
  }

  private scheduleSnapshot(): void {
    if (!this.running || this.snapshotTimer) return;
    this.snapshotTimer = setTimeout(async () => {
      this.snapshotTimer = null;
      try {
        await this.publishSnapshots();
      } catch (error) {
        this.logError("Zotero snapshot publish failed", error);
      }
    }, 250);
  }

  private schedulePoll(delayMs: number): void {
    if (!this.running) return;
    this.pollTimer = setTimeout(() => void this.pollOnce(), delayMs);
  }

  private async pollOnce(): Promise<void> {
    let nextDelay = this.pollIntervalMs;
    try {
      const batch = await this.transport.pollCommands(this.commandCursor);
      for (const command of batch.commands) {
        const ack = await this.executeCommand(command);
        await this.transport.acknowledge(ack);
        if (ack.status === "applied") this.scheduleSnapshot();
      }
      // Advance only after all acknowledgements succeeded. Replays are safe.
      this.commandCursor = batch.nextCursor;
      nextDelay = clampPollInterval(batch.pollAfterMs ?? this.pollIntervalMs);
    } catch (error) {
      this.logError("Zotero command poll failed", error);
    } finally {
      this.schedulePoll(nextDelay);
    }
  }

  private async assertPreconditions(command: BridgeCommand): Promise<void> {
    for (const expected of command.preconditions || []) {
      const entity =
        expected.entityType === "item"
          ? Zotero.Items.getByLibraryAndKey(command.libraryId, expected.key)
          : Zotero.Collections.getByLibraryAndKey(command.libraryId, expected.key);
      const actual = entity ? versionOf(entity) : null;
      if (!actual || !entityVersionEquals(expected, actual)) {
        throw new ConflictError(expected, actual);
      }
    }
  }

  private async applyAllowedCommand(
    command: BridgeCommand,
  ): Promise<Record<string, unknown>> {
    switch (command.type) {
      case "collection.addItems":
      case "collection.removeItems":
        return this.applyCollectionCommand(command);
      case "tag.add":
      case "tag.remove":
        return this.applyTagCommand(command);
      case "note.createChild":
        return this.createChildNote(command);
      case "item.importPaper":
        return this.importPaper(command);
      default:
        throw new CommandError("COMMAND_NOT_ALLOWED", "Command type is not allowed");
    }
  }

  private async applyCollectionCommand(
    command: Extract<BridgeCommand, { type: "collection.addItems" | "collection.removeItems" }>,
  ): Promise<Record<string, unknown>> {
    const collection = Zotero.Collections.getByLibraryAndKey(
      command.libraryId,
      command.collectionKey,
    );
    if (!collection) throw new CommandError("COLLECTION_NOT_FOUND", "Collection not found");
    const items = this.resolveRegularItems(command.libraryId, command.itemKeys);
    for (const item of items) {
      if (command.type === "collection.addItems") item.addToCollection(collection.id);
      else item.removeFromCollection(collection.id);
      await item.save();
    }
    return { changedItemKeys: items.map((item) => item.key).sort() };
  }

  private async applyTagCommand(
    command: Extract<BridgeCommand, { type: "tag.add" | "tag.remove" }>,
  ): Promise<Record<string, unknown>> {
    const tag = command.tag.trim();
    if (!tag) throw new CommandError("INVALID_TAG", "Tag cannot be blank");
    const items = this.resolveRegularItems(command.libraryId, command.itemKeys);
    for (const item of items) {
      if (command.type === "tag.add") item.addTag(tag);
      else item.removeTag(tag);
      await item.save();
    }
    return { changedItemKeys: items.map((item) => item.key).sort(), tag };
  }

  private async createChildNote(
    command: Extract<BridgeCommand, { type: "note.createChild" }>,
  ): Promise<Record<string, unknown>> {
    const parent = Zotero.Items.getByLibraryAndKey(command.libraryId, command.parentItemKey);
    if (!parent || !parent.isRegularItem()) {
      throw new CommandError("PARENT_NOT_FOUND", "Regular parent item not found");
    }
    const marker = noteReplayMarker(command.commandId);
    for (const noteID of parent.getNotes()) {
      const existing = Zotero.Items.get(noteID);
      if (existing && existing.isNote() && existing.getNote().includes(marker)) {
        return { noteKey: existing.key, deduplicated: true };
      }
    }

    const cleanHtml = sanitizeNoteHtml(command.noteHtml);
    const note = new Zotero.Item("note");
    note.libraryID = command.libraryId;
    note.parentItemID = parent.id;
    note.setNote(`${marker}${cleanHtml}`);
    await note.save();
    return { noteKey: note.key, deduplicated: false };
  }

  private async importPaper(
    command: Extract<BridgeCommand, { type: "item.importPaper" }>,
  ): Promise<Record<string, unknown>> {
    const collection = Zotero.Collections.getByLibraryAndKey(
      command.libraryId,
      command.collectionKey,
    );
    if (!collection) throw new CommandError("COLLECTION_NOT_FOUND", "Collection not found");
    const fileInfo = await IOUtils.stat(command.attachmentPath);
    if (fileInfo.type !== "regular") {
      throw new CommandError("PDF_NOT_FOUND", "The local PDF is unavailable");
    }
    const item = new Zotero.Item("journalArticle");
    item.libraryID = command.libraryId;
    item.setField("title", command.paper.title);
    if (command.paper.year) item.setField("date", String(command.paper.year));
    if (command.paper.doi) item.setField("DOI", command.paper.doi);
    if (command.paper.abstract) item.setField("abstractNote", command.paper.abstract);
    if (command.paper.url) item.setField("url", command.paper.url);
    item.setCreators(
      command.paper.authors.map((name) => ({ creatorType: "author", name })),
    );
    item.addToCollection(collection.id);
    await item.save();
    const attachment = await Zotero.Attachments.importFromFile({
      file: command.attachmentPath,
      parentItemID: item.id,
      libraryID: command.libraryId,
      title: command.paper.title,
    });
    return { itemKey: item.key, attachmentKey: attachment.key };
  }

  private resolveRegularItems(libraryID: number, keys: string[]): Zotero.ZoteroItem[] {
    return [...new Set(keys)].sort().map((key) => {
      const item = Zotero.Items.getByLibraryAndKey(libraryID, key);
      if (!item || !item.isRegularItem()) {
        throw new CommandError("ITEM_NOT_FOUND", `Regular item not found: ${key}`);
      }
      return item;
    });
  }

  private rememberApplied(commandId: string, result: Record<string, unknown>): void {
    this.recentlyApplied.set(commandId, result);
    if (this.recentlyApplied.size > MAX_RECENT_COMMANDS) {
      const oldest = this.recentlyApplied.keys().next().value as string | undefined;
      if (oldest) this.recentlyApplied.delete(oldest);
    }
  }

  private logError(message: string, error: unknown): void {
    this.logger.error(message, {
      error: safeErrorMessage(error),
      // Never attach config, request headers, URLs, or pairingToken here.
    });
  }
}

function versionOf(entity: { version?: number; dateModified?: string }): EntityVersion {
  return {
    version: Number.isSafeInteger(entity.version) ? Number(entity.version) : 0,
    dateModified: entity.dateModified || "",
  };
}

function clampPollInterval(value: number | undefined): number {
  if (!Number.isFinite(value)) return DEFAULT_POLL_INTERVAL_MS;
  return Math.max(MIN_POLL_INTERVAL_MS, Math.min(MAX_POLL_INTERVAL_MS, Number(value)));
}

function safeErrorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "Unknown bridge error";
  // Error sources are bridge-generated or Zotero-generated. Avoid echoing long
  // remote bodies or arbitrary transport data into plugin logs/acks.
  return error.message.slice(0, 500);
}
