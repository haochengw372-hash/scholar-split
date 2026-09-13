/// <reference path="./zotero-api.d.ts" />

import {
  AttachmentSnapshot,
  CollectionSnapshot,
  CreatorSnapshot,
  ItemSnapshot,
  SCHEMA_VERSION,
  SnapshotEnvelope,
  TagSnapshot,
} from "./types";
import { canonicalizeSnapshot } from "./protocol";

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionalText(value: unknown): string | undefined {
  const valueText = text(value);
  return valueText || undefined;
}

function versionOf(entity: { version?: number; dateModified?: string }) {
  return {
    version: Number.isSafeInteger(entity.version) ? Number(entity.version) : 0,
    dateModified: entity.dateModified || "",
  };
}

function keyForItemID(id: number): string | null {
  const item = Zotero.Items.get(id);
  return item ? item.key : null;
}

function keyForCollectionID(id: number): string | null {
  const collection = Zotero.Collections.get(id);
  return collection ? collection.key : null;
}

function creatorSnapshot(creator: Record<string, unknown>): CreatorSnapshot {
  return {
    creatorType: text(creator.creatorType),
    ...(optionalText(creator.firstName) ? { firstName: text(creator.firstName) } : {}),
    ...(optionalText(creator.lastName) ? { lastName: text(creator.lastName) } : {}),
    ...(optionalText(creator.name) ? { name: text(creator.name) } : {}),
  };
}

function regularItemSnapshot(item: Zotero.ZoteroItem): ItemSnapshot {
  return {
    key: item.key,
    itemType: item.itemType || "unknown",
    title: text(item.getField("title")),
    ...(optionalText(item.getField("abstractNote"))
      ? { abstractNote: text(item.getField("abstractNote")) }
      : {}),
    ...(optionalText(item.getField("date")) ? { date: text(item.getField("date")) } : {}),
    ...(optionalText(item.getField("DOI")) ? { DOI: text(item.getField("DOI")) } : {}),
    ...(optionalText(item.getField("url")) ? { url: text(item.getField("url")) } : {}),
    creators: item.getCreators().map(creatorSnapshot),
    collectionKeys: item
      .getCollections()
      .map(keyForCollectionID)
      .filter((key): key is string => key !== null),
    tags: item.getTags().map(({ tag }) => tag),
    attachmentKeys: item
      .getAttachments()
      .map(keyForItemID)
      .filter((key): key is string => key !== null),
    ...versionOf(item),
  };
}

async function attachmentSnapshot(
  attachment: Zotero.ZoteroItem,
): Promise<AttachmentSnapshot> {
  let hasLocalFile = false;
  try {
    // Presence is useful; the private filesystem path is intentionally discarded.
    hasLocalFile = Boolean(await attachment.getFilePathAsync());
  } catch {
    hasLocalFile = false;
  }
  return {
    key: attachment.key,
    parentItemKey:
      typeof attachment.parentItemID === "number"
        ? keyForItemID(attachment.parentItemID)
        : null,
    title: text(attachment.getField("title")),
    filename: attachment.attachmentFilename || null,
    contentType: attachment.attachmentContentType || null,
    linkMode: Number.isSafeInteger(attachment.attachmentLinkMode)
      ? Number(attachment.attachmentLinkMode)
      : null,
    url: optionalText(attachment.getField("url")) || null,
    hasLocalFile,
    ...versionOf(attachment),
  };
}

function collectionSnapshot(collection: Zotero.ZoteroCollection): CollectionSnapshot {
  return {
    key: collection.key,
    name: collection.name,
    parentKey:
      typeof collection.parentID === "number"
        ? keyForCollectionID(collection.parentID)
        : null,
    ...versionOf(collection),
  };
}

function tagSnapshots(items: ItemSnapshot[]): TagSnapshot[] {
  const keysByTag = new Map<string, string[]>();
  for (const item of items) {
    for (const tag of item.tags) {
      const keys = keysByTag.get(tag) || [];
      keys.push(item.key);
      keysByTag.set(tag, keys);
    }
  }
  return [...keysByTag].map(([name, itemKeys]) => ({ name, itemKeys }));
}

export async function createLibrarySnapshot(
  bridgeId: string,
  libraryID: number,
  sequence: number,
  capturedAt = new Date().toISOString(),
): Promise<SnapshotEnvelope> {
  const library = Zotero.Libraries.get(libraryID);
  const allItems = await Zotero.Items.getAll(libraryID, false, false);
  const regularItems = allItems.filter((item) => item.isRegularItem());
  const attachmentItems = allItems.filter((item) => item.isAttachment());
  const items = regularItems.map(regularItemSnapshot);
  const attachments = await Promise.all(attachmentItems.map(attachmentSnapshot));
  const collections = Zotero.Collections.getByLibrary(libraryID, true).map(collectionSnapshot);
  const libraryVersion = Number.isSafeInteger(library.libraryVersion)
    ? Number(library.libraryVersion)
    : 0;

  return canonicalizeSnapshot({
    schemaVersion: SCHEMA_VERSION,
    bridgeId,
    capturedAt,
    library: {
      id: libraryID,
      name: library.name,
      version: libraryVersion,
    },
    cursor: { libraryVersion, sequence },
    items,
    collections,
    tags: tagSnapshots(items),
    attachments,
  });
}
