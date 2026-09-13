/** Minimal ambient surface used by this standalone integration. */
declare namespace Zotero {
  interface ZoteroItem {
    id: number;
    key: string;
    libraryID: number;
    version?: number;
    dateModified?: string;
    itemType?: string;
    parentItemID?: number | false;
    attachmentContentType?: string;
    attachmentFilename?: string;
    attachmentLinkMode?: number;
    isRegularItem(): boolean;
    isAttachment(): boolean;
    isNote(): boolean;
    getField(field: string): unknown;
    getCreators(): Array<Record<string, unknown>>;
    getTags(): Array<{ tag: string; type?: number }>;
    getCollections(): number[];
    getAttachments(): number[];
    getNotes(): number[];
    getNote(): string;
    getFilePathAsync(): Promise<string | false>;
    addToCollection(collectionID: number): boolean;
    removeFromCollection(collectionID: number): boolean;
    addTag(tag: string): boolean;
    removeTag(tag: string): boolean;
    setNote(html: string): void;
    setField(field: string, value: string | number | boolean): void;
    setCreators(creators: Array<Record<string, unknown>>): void;
    save(): Promise<number>;
  }

  interface ZoteroCollection {
    id: number;
    key: string;
    libraryID: number;
    name: string;
    version?: number;
    dateModified?: string;
    parentID?: number | false;
  }

  interface ZoteroLibrary {
    libraryID: number;
    name: string;
    libraryVersion?: number;
  }

  namespace Items {
    function getAll(
      libraryID: number,
      onlyTopLevel?: boolean,
      includeDeleted?: boolean,
    ): Promise<ZoteroItem[]>;
    function get(id: number): ZoteroItem | false;
    function getByLibraryAndKey(libraryID: number, key: string): ZoteroItem | false;
  }

  namespace Collections {
    function getByLibrary(libraryID: number): ZoteroCollection[];
    function get(id: number): ZoteroCollection | false;
    function getByLibraryAndKey(
      libraryID: number,
      key: string,
    ): ZoteroCollection | false;
  }

  namespace Libraries {
    function get(libraryID: number): ZoteroLibrary;
  }

  namespace Notifier {
    interface Observer {
      notify(
        event: string,
        type: string,
        ids: number[],
        extraData: Record<string, unknown>,
      ): void | Promise<void>;
    }
    function registerObserver(
      observer: Observer,
      types: string[],
      id: string,
      priority?: number,
    ): string;
    function unregisterObserver(observerID: string): void;
  }

  namespace DB {
    function executeTransaction<T>(callback: () => Promise<T>): Promise<T>;
  }

  namespace Attachments {
    function importFromFile(options: {
      file: string;
      parentItemID?: number;
      libraryID?: number;
      title?: string;
    }): Promise<ZoteroItem>;
  }

  const Item: {
    new (itemType: "note" | "journalArticle"): ZoteroItem;
  };
}

declare namespace IOUtils {
  function stat(path: string): Promise<{ type: string; size?: number }>;
}
