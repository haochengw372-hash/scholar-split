"""SQLite persistence and legacy-workspace discovery for ScholarSplit.

The store deliberately uses only the Python standard library.  It owns database
state, but the migration scanner is read-only: it never renames, moves, or
deletes a user's translated PDFs or reading guides.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
import urllib.parse
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Iterator, Mapping, Sequence


CURRENT_SCHEMA_VERSION = 1

_GENERATED_SUFFIX = re.compile(
    r"(?:\.no_watermark)?(?:\.[a-z]{2}(?:-[a-z]{2})?)?"
    r"(?:[._](?:lr|tb)_dual|\.mono(?:-cut)?|\.dual(?:-cut)?|"
    r"\.crop-compare|\.compare)$",
    re.IGNORECASE,
)
_GUIDE_FILE_KEYS = frozenset(
    {
        "source",
        "source_file",
        "source_filename",
        "sourcefile",
        "sourcefilename",
        "file",
        "file_name",
        "filename",
        "pdf",
        "pdf_file",
        "pdf_filename",
        "pdffile",
        "pdffilename",
        "translation_file",
        "translation_filename",
        "translationfile",
        "translationfilename",
    }
)

_JSON_COLUMNS: dict[str, frozenset[str]] = {
    "papers": frozenset({"authors", "metadata"}),
    "artifacts": frozenset({"metadata"}),
    "collections": frozenset({"metadata"}),
    "tags": frozenset(),
    "notes": frozenset({"metadata"}),
    "reading_state": frozenset({"position"}),
    "review_projects": frozenset({"protocol"}),
    "review_members": frozenset({"metadata"}),
    "screening_decisions": frozenset({"criteria"}),
    "evidence": frozenset({"codes", "metadata"}),
    "syntheses": frozenset({"source_ids", "metadata"}),
    "gaps": frozenset({"evidence_ids", "metadata"}),
    "recommendations": frozenset({"evidence_ids", "metadata"}),
    "acquisitions": frozenset({"metadata"}),
    "jobs": frozenset({"payload", "result", "metadata"}),
    "zotero_commands": frozenset({"payload", "result"}),
    "sync_audit": frozenset({"detail"}),
}

_ENTITY_TABLES = frozenset(_JSON_COLUMNS)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonicalize_filename(value: str | Path | None) -> str:
    """Return the conservative key used for exact legacy-artifact matching.

    Only transport and generated-output decoration is removed.  Punctuation,
    words, and digits are retained, so the scanner cannot accidentally perform
    a fuzzy title match.
    """

    decoded = urllib.parse.unquote(str(value or ""), errors="replace").replace("\\", "/")
    decoded = re.sub(r"(?i)(\.pdf)[?#].*$", r"\1", decoded)
    leaf = PurePath(decoded).name
    normalized = unicodedata.normalize("NFKC", leaf).strip()
    if normalized.lower().endswith((".pdf", ".json")):
        normalized = normalized.rsplit(".", 1)[0]
    normalized = _GENERATED_SUFFIX.sub("", normalized)
    normalized = " ".join(normalized.split())
    return normalized.casefold()


def _normalized_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _guide_titles(payload: Any) -> set[str]:
    if not isinstance(payload, Mapping):
        return set()
    guide = payload.get("guide") if isinstance(payload.get("guide"), Mapping) else payload
    return {
        normalized
        for key in ("originalTitle", "title")
        if (normalized := _normalized_title(guide.get(key))) and len(normalized) >= 12
    }


def _artifact_kind(path: Path) -> str:
    stem = unicodedata.normalize("NFKC", path.stem).strip()
    return "translated_pdf" if _GENERATED_SUFFIX.search(stem) else "original_pdf"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filename_metadata(path: Path) -> dict[str, Any]:
    stem = unicodedata.normalize("NFKC", path.stem).strip()
    match = re.match(r"^(.+?)\s+-\s+((?:19|20)\d{2})\s+-\s+(.+)$", stem)
    if not match:
        return {"title": stem, "authors": [], "year": None}
    author, year, title = match.groups()
    return {"title": title.strip(), "authors": [author.strip()], "year": int(year)}


def _guide_filename_values(value: Any, key: str | None = None) -> Iterator[str]:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            normalized_key = re.sub(r"(?<!^)(?=[A-Z])", "_", str(child_key)).casefold()
            yield from _guide_filename_values(child_value, normalized_key)
    elif isinstance(value, list):
        for item in value:
            yield from _guide_filename_values(item, key)
    elif key in _GUIDE_FILE_KEYS and isinstance(value, str):
        yield value


def scan_workspace(root: str | Path) -> dict[str, Any]:
    """Inspect legacy output folders without modifying them.

    Guides are linked only when their canonical stem or an explicit filename in
    the JSON exactly equals one unique translated-PDF key.  Multiple exact
    candidates are reported under ``ambiguous`` and are never auto-selected.
    """

    workspace = Path(root).expanduser().resolve()
    translated_dir = workspace / "translated"
    guides_dir = workspace / "reading-guides"
    pdfs = sorted(
        (path for path in translated_dir.glob("*.pdf") if path.is_file()),
        key=lambda path: path.name.casefold(),
    ) if translated_dir.is_dir() else []
    guides = sorted(
        (path for path in guides_dir.glob("*.json") if path.is_file()),
        key=lambda path: path.name.casefold(),
    ) if guides_dir.is_dir() else []

    pdfs_by_key: dict[str, list[Path]] = defaultdict(list)
    for pdf in pdfs:
        pdfs_by_key[canonicalize_filename(pdf.name)].append(pdf)

    groups = []
    for key, paths in sorted(pdfs_by_key.items()):
        original = next((path for path in paths if _artifact_kind(path) == "original_pdf"), None)
        groups.append(
            {
                "canonical_name": key,
                "original": str(original) if original else None,
                "files": [
                    {
                        "path": str(path),
                        "kind": _artifact_kind(path),
                        "size_bytes": path.stat().st_size,
                        "mtime_ns": path.stat().st_mtime_ns,
                    }
                    for path in paths
                ],
            }
        )

    matches: list[dict[str, str]] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    guide_rows: list[dict[str, Any]] = []

    for guide in guides:
        keys = {canonicalize_filename(guide.name)}
        parse_error: str | None = None
        payload: Any = None
        try:
            payload = json.loads(guide.read_text(encoding="utf-8"))
            keys.update(filter(None, (canonicalize_filename(item) for item in _guide_filename_values(payload))))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"

        candidates = sorted(
            {path for key in keys for path in pdfs_by_key.get(key, [])},
            key=lambda path: str(path).casefold(),
        )
        match_type = "exact"
        matched_group_key: str | None = None
        if not candidates and payload is not None:
            title_group_keys = {
                group_key
                for title in _guide_titles(payload)
                for group_key in pdfs_by_key
                if title in _normalized_title(group_key) or _normalized_title(group_key) in title
            }
            if len(title_group_keys) == 1:
                matched_group_key = next(iter(title_group_keys))
                group_paths = pdfs_by_key[matched_group_key]
                candidates = [
                    next(
                        (path for path in group_paths if _artifact_kind(path) == "original_pdf"),
                        group_paths[0],
                    )
                ]
                match_type = "title_exact_unique"
        guide_row = {
            "path": str(guide),
            "canonical_names": sorted(keys),
            "parse_error": parse_error,
        }
        guide_rows.append(guide_row)
        if len(candidates) == 1:
            pdf = candidates[0]
            exact_keys = sorted(key for key in keys if pdf in pdfs_by_key.get(key, []))
            canonical_name = matched_group_key or exact_keys[0]
            matches.append(
                {
                    "guide": str(guide),
                    "pdf": str(pdf),
                    "canonical_name": canonical_name,
                    "match_type": match_type,
                    # Compatibility names used by the local workspace API.
                    "paperId": canonical_name,
                    "translatedFile": pdf.name,
                    "guideFile": guide.name,
                }
            )
        elif len(candidates) > 1:
            ambiguous.append(
                {
                    "guide": str(guide),
                    "candidates": [str(path) for path in candidates],
                    "reason": "multiple_exact_matches",
                }
            )
        else:
            unmatched.append(
                {
                    "guide": str(guide),
                    "reason": "invalid_json" if parse_error else "no_exact_match",
                    "parse_error": parse_error,
                }
            )

    return {
        "root": str(workspace),
        "translated_pdfs": [
            {"path": str(path), "canonical_name": canonicalize_filename(path.name), "size_bytes": path.stat().st_size}
            for path in pdfs
        ],
        "reading_guides": guide_rows,
        "groups": groups,
        "matches": matches,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
    }


# A descriptive alias for callers performing a v0.1 -> v0.2 migration.
scan_migration_sources = scan_workspace


def backup_database(source: str | Path, destination: str | Path | None = None) -> Path:
    """Create a transactionally consistent SQLite backup and return its path."""

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination_path = source_path.with_name(f"{source_path.name}.{stamp}.bak")
    else:
        destination_path = Path(destination).expanduser().resolve()
        if destination_path.is_dir():
            destination_path = destination_path / f"{source_path.name}.bak"
    if destination_path == source_path:
        raise ValueError("backup destination must differ from source database")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path) as source_conn, sqlite3.connect(destination_path) as backup_conn:
        source_conn.backup(backup_conn)
    return destination_path


_SCHEMA_V1 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '[]',
    year INTEGER,
    doi TEXT COLLATE NOCASE UNIQUE,
    abstract TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    url TEXT,
    source_type TEXT,
    source_key TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_updated ON papers(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source_type, source_key);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL DEFAULT '',
    mime_type TEXT,
    language TEXT,
    checksum TEXT,
    size_bytes INTEGER,
    source TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_paper ON artifacts(paper_id, kind);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    parent_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    PRIMARY KEY(collection_id, paper_id)
);
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    color TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL,
    PRIMARY KEY(paper_id, tag_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES review_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    locator TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS reading_state (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'unread',
    progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
    page INTEGER,
    position TEXT NOT NULL DEFAULT '{}',
    last_opened_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    protocol TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    stage TEXT NOT NULL DEFAULT 'imported',
    status TEXT NOT NULL DEFAULT 'pending',
    position INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_review_members_queue ON review_members(project_id, stage, status, position);
CREATE TABLE IF NOT EXISTS screening_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL DEFAULT 'local',
    stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    criteria TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, paper_id, reviewer, stage)
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'finding',
    claim TEXT NOT NULL DEFAULT '',
    quote TEXT NOT NULL DEFAULT '',
    locator TEXT,
    theme TEXT,
    codes TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id, theme, paper_id);
CREATE TABLE IF NOT EXISTS syntheses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'narrative',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    source_ids TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 0,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES review_projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'next_step',
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recommendations_project ON recommendations(project_id, status, priority DESC);

CREATE TABLE IF NOT EXISTS acquisitions (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    provider TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    url TEXT,
    path TEXT,
    error TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 100),
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    project_id TEXT REFERENCES review_projects(id) ON DELETE SET NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at DESC);
CREATE TABLE IF NOT EXISTS zotero_commands (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zotero_commands_pending ON zotero_commands(status, created_at);
CREATE TABLE IF NOT EXISTS sync_audit (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_audit_entity ON sync_audit(entity_type, entity_id, created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(
    title, authors, abstract, venue,
    content='papers', content_rowid='rowid', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS papers_fts_insert AFTER INSERT ON papers BEGIN
    INSERT INTO paper_fts(rowid, title, authors, abstract, venue)
    VALUES (new.rowid, new.title, new.authors, new.abstract, new.venue);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_delete AFTER DELETE ON papers BEGIN
    INSERT INTO paper_fts(paper_fts, rowid, title, authors, abstract, venue)
    VALUES ('delete', old.rowid, old.title, old.authors, old.abstract, old.venue);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_update AFTER UPDATE ON papers BEGIN
    INSERT INTO paper_fts(paper_fts, rowid, title, authors, abstract, venue)
    VALUES ('delete', old.rowid, old.title, old.authors, old.abstract, old.venue);
    INSERT INTO paper_fts(rowid, title, authors, abstract, venue)
    VALUES (new.rowid, new.title, new.authors, new.abstract, new.venue);
END;
INSERT INTO paper_fts(paper_fts) VALUES('rebuild');
"""


class WorkspaceStore:
    """Thread-safe, dictionary-oriented SQLite workspace repository."""

    def __init__(
        self,
        db_path: str | Path,
        workspace_root: str | Path | None = None,
        *,
        auto_migrate: bool = True,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else self.db_path.parent
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._on_close: list[Any] = []
        self._connection = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        os.chmod(self.db_path, 0o600)
        if auto_migrate:
            self.migrate()

    def __enter__(self) -> "WorkspaceStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        for callback in self._on_close:
            callback()
        self._on_close.clear()
        with self._lock:
            self._connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def migrate(self, *, backup_before: bool = False, backup_to: str | Path | None = None) -> int:
        """Apply all pending schema migrations and return the active version."""

        with self._lock:
            version = self.schema_version
            if version > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported schema {CURRENT_SCHEMA_VERSION}"
                )
            if version == CURRENT_SCHEMA_VERSION:
                return version
            if backup_before and self.db_path.exists() and self.db_path.stat().st_size:
                self.backup(backup_to)
            if version < 1:
                applied_at = _utcnow().replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + _SCHEMA_V1
                    + f"\nINSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, '{applied_at}');\n"
                    + "PRAGMA user_version = 1;\nCOMMIT;"
                )
                try:
                    self._connection.executescript(script)
                except BaseException:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    raise
            return self.schema_version

    def backup(self, destination: str | Path | None = None) -> Path:
        """Back up the live connection, including uncheckpointed WAL pages."""

        with self._lock:
            if destination is None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                destination_path = self.db_path.with_name(f"{self.db_path.name}.{stamp}.bak")
            else:
                destination_path = Path(destination).expanduser().resolve()
                if destination_path.is_dir():
                    destination_path = destination_path / f"{self.db_path.name}.bak"
            if destination_path == self.db_path:
                raise ValueError("backup destination must differ from source database")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(destination_path) as target:
                self._connection.backup(target)
            return destination_path

    def scan_migration_sources(self, root: str | Path | None = None) -> dict[str, Any]:
        return scan_workspace(root or self.workspace_root)

    def scan_workspace(
        self,
        translated_dir: str | Path | None = None,
        guide_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Workspace-API-compatible scanner entry point.

        Explicit directories must be the conventional sibling directories;
        this keeps scanning bounded to the documented migration surface.
        """

        if translated_dir is None and guide_dir is None:
            return scan_workspace(self.workspace_root)
        if translated_dir is None or guide_dir is None:
            raise ValueError("translated_dir and guide_dir must be provided together")
        translated = Path(translated_dir).expanduser().resolve()
        guides = Path(guide_dir).expanduser().resolve()
        if translated.parent != guides.parent or translated.name != "translated" or guides.name != "reading-guides":
            raise ValueError("migration sources must be sibling translated/ and reading-guides/ directories")
        return scan_workspace(translated.parent)

    def import_scan(self, scan: Mapping[str, Any]) -> dict[str, Any]:
        """Persist every canonical PDF group and any confidently linked guides."""

        matches_by_key = {
            str(match.get("canonical_name")): match
            for match in scan.get("matches", [])
            if isinstance(match, Mapping) and match.get("match_type") in {"exact", "title_exact_unique"}
        }
        imported: list[dict[str, Any]] = []
        for group in scan.get("groups", []):
            if not isinstance(group, Mapping):
                continue
            canonical_name = str(group.get("canonical_name") or "").strip()
            files = group.get("files") if isinstance(group.get("files"), list) else []
            paths = [
                Path(str(item.get("path", ""))).expanduser().resolve()
                for item in files
                if isinstance(item, Mapping)
            ]
            paths = [path for path in paths if path.is_file()]
            if not canonical_name or not paths:
                continue
            original = next((path for path in paths if _artifact_kind(path) == "original_pdf"), paths[0])
            paper_id = uuid.uuid5(uuid.NAMESPACE_URL, f"scholarsplit:{canonical_name}").hex
            match = matches_by_key.get(canonical_name)
            file_metadata = _filename_metadata(original)
            title = file_metadata["title"]
            if match:
                try:
                    guide_payload = json.loads(Path(str(match["guide"])).read_text(encoding="utf-8"))
                    guide = guide_payload.get("guide") if isinstance(guide_payload.get("guide"), Mapping) else {}
                    title = str(guide.get("originalTitle") or guide.get("title") or title)
                except (OSError, KeyError, json.JSONDecodeError):
                    pass
            source_type = (
                "chrome_extension"
                if canonical_name.lower().startswith("chrome-")
                else "legacy_translation"
            )
            paper = self.upsert_paper(
                {
                    "id": paper_id,
                    "title": title,
                    "authors": file_metadata["authors"],
                    "year": file_metadata["year"],
                    "source_type": source_type,
                    "source_key": canonical_name,
                    "metadata": {
                        "migrationMatch": match.get("match_type") if match else "pdf_group",
                        "originalMissing": not bool(group.get("original")),
                    },
                }
            )
            artifacts = []
            for path in paths:
                artifacts.append(
                    self.upsert_artifact(
                        {
                            "paper_id": paper_id,
                            "kind": _artifact_kind(path),
                            "path": str(path),
                            "canonical_name": canonical_name,
                            "mime_type": "application/pdf",
                            "checksum": _sha256_file(path),
                            "size_bytes": path.stat().st_size,
                            "source": source_type,
                        }
                    )
                )
            if match:
                guide_path = Path(str(match.get("guide", ""))).expanduser().resolve()
                if guide_path.is_file():
                    artifacts.append(
                        self.upsert_artifact(
                            {
                                "paper_id": paper_id,
                                "kind": "reading_guide",
                                "path": str(guide_path),
                                "canonical_name": canonical_name,
                                "mime_type": "application/json",
                                "checksum": _sha256_file(guide_path),
                                "size_bytes": guide_path.stat().st_size,
                                "source": source_type,
                                "metadata": {"matchType": match.get("match_type")},
                            }
                        )
                    )
            imported.append({"paper": paper, "artifacts": artifacts, "match": dict(match or {})})
        return {"scan": dict(scan), "imported": imported, "importedCount": len(imported)}

    import_workspace_scan = import_scan

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            counts = {
                name: int(self._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for name, table in {
                    "papers": "papers",
                    "collections": "collections",
                    "review_projects": "review_projects",
                    "recommendations": "recommendations",
                    "jobs": "jobs",
                }.items()
            }
            job_rows = self._connection.execute(
                "SELECT status, count(*) AS count FROM jobs GROUP BY status ORDER BY status"
            ).fetchall()
        return {"counts": counts, "jobsByStatus": {row["status"]: row["count"] for row in job_rows}}

    def _columns(self, table: str) -> set[str]:
        self._validate_table(table)
        return {str(row[1]) for row in self._connection.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _validate_table(table: str) -> None:
        if table not in _ENTITY_TABLES:
            raise ValueError(f"unsupported entity table: {table}")

    @staticmethod
    def _validate_column(column: str, available: set[str]) -> None:
        if column not in available:
            raise ValueError(f"unsupported column: {column}")

    @staticmethod
    def _encode_record(table: str, values: Mapping[str, Any]) -> dict[str, Any]:
        encoded = dict(values)
        for column in _JSON_COLUMNS.get(table, ()):
            if column in encoded and not isinstance(encoded[column], str):
                encoded[column] = json.dumps(encoded[column], ensure_ascii=False, separators=(",", ":"))
        return encoded

    @staticmethod
    def _decode_row(table: str, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        decoded = dict(row)
        for column in _JSON_COLUMNS.get(table, ()):
            raw = decoded.get(column)
            if isinstance(raw, str):
                try:
                    decoded[column] = json.loads(raw)
                except json.JSONDecodeError:
                    decoded[column] = raw
        return decoded

    def insert(self, table: str, values: Mapping[str, Any]) -> dict[str, Any]:
        columns = self._columns(table)
        record = dict(values)
        now = _utcnow()
        if "id" in columns:
            record.setdefault("id", uuid.uuid4().hex)
        if "created_at" in columns:
            record.setdefault("created_at", now)
        if "updated_at" in columns:
            record.setdefault("updated_at", now)
        if table == "acquisitions":
            record.setdefault("requested_at", now)
        for column in record:
            self._validate_column(column, columns)
        encoded = self._encode_record(table, record)
        names = list(encoded)
        placeholders = ", ".join("?" for _ in names)
        sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"
        with self.transaction():
            self._connection.execute(sql, [encoded[name] for name in names])
        identifier = record.get("id")
        if identifier is None:
            return record
        return self.get(table, str(identifier)) or record

    def upsert(
        self,
        table: str,
        values: Mapping[str, Any],
        *,
        conflict_columns: Sequence[str] = ("id",),
    ) -> dict[str, Any]:
        columns = self._columns(table)
        record = dict(values)
        now = _utcnow()
        if "id" in columns:
            record.setdefault("id", uuid.uuid4().hex)
        if "created_at" in columns:
            record.setdefault("created_at", now)
        if "updated_at" in columns:
            record["updated_at"] = now
        if table == "acquisitions":
            record.setdefault("requested_at", now)
        for column in record:
            self._validate_column(column, columns)
        for column in conflict_columns:
            self._validate_column(column, columns)
            if column not in record:
                raise ValueError(f"conflict column is absent from values: {column}")
        encoded = self._encode_record(table, record)
        names = list(encoded)
        mutable = [
            name for name in names if name not in conflict_columns and name not in {"id", "created_at"}
        ]
        action = (
            "DO UPDATE SET " + ", ".join(f"{name}=excluded.{name}" for name in mutable)
            if mutable
            else "DO NOTHING"
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
            f"ON CONFLICT ({', '.join(conflict_columns)}) {action}"
        )
        with self.transaction():
            self._connection.execute(sql, [encoded[name] for name in names])
        where = " AND ".join(f"{name} = ?" for name in conflict_columns)
        row = self._connection.execute(
            f"SELECT * FROM {table} WHERE {where}", [encoded[name] for name in conflict_columns]
        ).fetchone()
        return self._decode_row(table, row) or record

    def get(self, table: str, record_id: str) -> dict[str, Any] | None:
        columns = self._columns(table)
        if "id" not in columns:
            raise ValueError(f"table has no id column: {table}")
        with self._lock:
            row = self._connection.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        return self._decode_row(table, row)

    def list(
        self,
        table: str,
        *,
        filters: Mapping[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        columns = self._columns(table)
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (filters or {}).items():
            self._validate_column(column, columns)
            clauses.append(f"{column} IS ?" if value is None else f"{column} = ?")
            parameters.append(value)
        if order_by is None:
            order_by = "created_at" if "created_at" in columns else next(iter(columns))
        self._validate_column(order_by, columns)
        bounded_limit = max(0, min(int(limit), 10_000))
        bounded_offset = max(0, int(offset))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM {table}{where} ORDER BY {order_by} {'DESC' if descending else 'ASC'} LIMIT ? OFFSET ?"
        parameters.extend((bounded_limit, bounded_offset))
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [self._decode_row(table, row) or {} for row in rows]

    def update(self, table: str, record_id: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
        columns = self._columns(table)
        changes = dict(values)
        changes.pop("id", None)
        if "updated_at" in columns:
            changes["updated_at"] = _utcnow()
        if not changes:
            return self.get(table, record_id)
        for column in changes:
            self._validate_column(column, columns)
        encoded = self._encode_record(table, changes)
        with self.transaction():
            cursor = self._connection.execute(
                f"UPDATE {table} SET {', '.join(f'{name} = ?' for name in encoded)} WHERE id = ?",
                [*encoded.values(), record_id],
            )
        return self.get(table, record_id) if cursor.rowcount else None

    def delete(self, table: str, record_id: str) -> bool:
        self._columns(table)
        with self.transaction():
            cursor = self._connection.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        return bool(cursor.rowcount)

    def upsert_paper(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.upsert("papers", values)

    add_paper = upsert_paper

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        paper = self.get("papers", paper_id)
        if paper is None:
            return None
        return self._enrich_papers([paper])[0]

    def _enrich_papers(self, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach collection, tag, and reading-state summaries in bounded queries."""

        if not papers:
            return papers
        paper_ids = [str(paper["id"]) for paper in papers]
        placeholders = ", ".join("?" for _ in paper_ids)
        collections: dict[str, list[str]] = defaultdict(list)
        tag_names: dict[str, list[str]] = defaultdict(list)
        tag_ids: dict[str, list[str]] = defaultdict(list)
        artifacts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        reading: dict[str, sqlite3.Row] = {}
        with self._lock:
            for row in self._connection.execute(
                f"""SELECT paper_id, collection_id FROM collection_papers
                    WHERE paper_id IN ({placeholders})
                    ORDER BY paper_id, position, added_at, collection_id""",
                paper_ids,
            ):
                collections[str(row["paper_id"])].append(str(row["collection_id"]))
            for row in self._connection.execute(
                f"""SELECT pt.paper_id, tags.id, tags.name FROM paper_tags pt
                    JOIN tags ON tags.id = pt.tag_id
                    WHERE pt.paper_id IN ({placeholders})
                    ORDER BY pt.paper_id, tags.name COLLATE NOCASE, tags.id""",
                paper_ids,
            ):
                paper_id = str(row["paper_id"])
                tag_ids[paper_id].append(str(row["id"]))
                tag_names[paper_id].append(str(row["name"]))
            for row in self._connection.execute(
                f"""SELECT paper_id, status, progress, page, last_opened_at FROM reading_state
                    WHERE paper_id IN ({placeholders})""",
                paper_ids,
            ):
                reading[str(row["paper_id"])] = row
            for row in self._connection.execute(
                f"""SELECT id, paper_id, kind, path, mime_type, language, checksum, size_bytes,
                           source, metadata, created_at, updated_at
                    FROM artifacts WHERE paper_id IN ({placeholders})
                    ORDER BY paper_id, kind, created_at""",
                paper_ids,
            ):
                decoded = self._decode_row("artifacts", row) or {}
                artifacts[str(row["paper_id"])].append(decoded)

        for paper in papers:
            paper_id = str(paper["id"])
            state = reading.get(paper_id)
            paper["collection_ids"] = collections[paper_id]
            paper["tags"] = tag_names[paper_id]
            paper["tag_ids"] = tag_ids[paper_id]
            paper["artifacts"] = artifacts[paper_id]
            paper["status"] = str(state["status"]) if state else "unread"
            paper["reading_progress"] = float(state["progress"]) if state else 0.0
            paper["reading_page"] = state["page"] if state else None
            paper["last_opened_at"] = state["last_opened_at"] if state else None
        return papers

    def list_papers(
        self,
        *,
        search: str | None = None,
        collection_id: str | None = None,
        collection: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        scope: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = str(search or "").strip()
        selected_collection = collection_id or collection
        joins: list[str] = []
        clauses: list[str] = []
        parameters: list[Any] = []
        order = "papers.updated_at DESC"
        if query:
            joins.append("JOIN paper_fts ON paper_fts.rowid = papers.rowid")
            clauses.append("paper_fts MATCH ?")
            parameters.append(f'"{query.replace(chr(34), chr(34) * 2)}"')
            order = "bm25(paper_fts), papers.updated_at DESC"
        if selected_collection:
            joins.append("JOIN collection_papers cp ON cp.paper_id = papers.id")
            clauses.append("cp.collection_id = ?")
            parameters.append(selected_collection)
        if tag:
            joins.extend(
                (
                    "JOIN paper_tags pt ON pt.paper_id = papers.id",
                    "JOIN tags ON tags.id = pt.tag_id",
                )
            )
            clauses.append("(tags.id = ? OR tags.name = ? COLLATE NOCASE)")
            parameters.extend((tag, tag))
        if status:
            joins.append("JOIN reading_state rs ON rs.paper_id = papers.id")
            clauses.append("rs.status = ?")
            parameters.append(status)
        if scope == "scholarsplit":
            clauses.append("papers.source_type IN ('chrome_extension', 'manual_import', 'scholarsplit')")
        elif scope == "zotero":
            clauses.append("papers.source_type = 'zotero'")
        elif scope == "legacy":
            clauses.append("papers.source_type = 'legacy_translation'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((max(0, min(int(limit), 10_000)), max(0, int(offset))))
        sql = (
            f"SELECT DISTINCT papers.* FROM papers {' '.join(joins)} {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        papers = [self._decode_row("papers", row) or {} for row in rows]
        return self._enrich_papers(papers)

    def upsert_artifact(self, values: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(values)
        if "path" in record and not record.get("canonical_name"):
            record["canonical_name"] = canonicalize_filename(record["path"])
        return self.upsert("artifacts", record, conflict_columns=("path",))

    add_artifact = upsert_artifact

    def list_artifacts(self, *, paper_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.list("artifacts", filters={"paper_id": paper_id} if paper_id else None, limit=limit)

    def create_collection(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("collections", values)

    def add_paper_to_collection(self, collection_id: str, paper_id: str, *, position: int = 0) -> None:
        with self.transaction():
            self._connection.execute(
                """INSERT INTO collection_papers(collection_id, paper_id, position, added_at)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(collection_id, paper_id) DO UPDATE SET position=excluded.position""",
                (collection_id, paper_id, int(position), _utcnow()),
            )

    def create_tag(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("tags", values)

    def tag_paper(self, paper_id: str, tag_id: str) -> None:
        with self.transaction():
            self._connection.execute(
                "INSERT OR IGNORE INTO paper_tags(paper_id, tag_id, added_at) VALUES(?, ?, ?)",
                (paper_id, tag_id, _utcnow()),
            )

    def create_note(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("notes", values)

    def set_reading_state(self, paper_id: str, **values: Any) -> dict[str, Any]:
        return self.upsert("reading_state", {"paper_id": paper_id, **values}, conflict_columns=("paper_id",))

    def get_reading_state(self, paper_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM reading_state WHERE paper_id = ?", (paper_id,)).fetchone()
        return self._decode_row("reading_state", row)

    def create_review_project(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("review_projects", values)

    def get_review_project(self, project_id: str) -> dict[str, Any] | None:
        return self.get("review_projects", project_id)

    def list_review_projects(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.list("review_projects", order_by="updated_at", limit=limit, offset=offset)

    def _list_project_records(
        self, table: str, project_id: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        return self.list(
            table,
            filters={"project_id": project_id} if project_id else None,
            limit=limit,
            offset=offset,
        )

    def create_review_member(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("review_members", values)

    def list_review_members(
        self, *, project_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_project_records("review_members", project_id, limit, offset)

    def create_screening_decision(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("screening_decisions", values)

    def list_screening_decisions(
        self, *, project_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_project_records("screening_decisions", project_id, limit, offset)

    def create_evidence(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("evidence", values)

    create_evidence_item = create_evidence

    def list_evidence(
        self, *, project_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_project_records("evidence", project_id, limit, offset)

    list_evidence_items = list_evidence

    def create_synthesis(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("syntheses", values)

    def list_syntheses(
        self, *, project_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_project_records("syntheses", project_id, limit, offset)

    def create_gap(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("gaps", values)

    def list_gaps(
        self, *, project_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_project_records("gaps", project_id, limit, offset)

    def create_recommendation(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("recommendations", values)

    def create_acquisition(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("acquisitions", values)

    def create_job(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("jobs", values)

    def update_job(self, job_id: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.update("jobs", job_id, values)

    def list_jobs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.list("jobs", filters={"status": status} if status else None, limit=limit)

    def list_recommendations(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        filters = {key: value for key, value in {"project_id": project_id, "status": status}.items() if value is not None}
        return self.list(
            "recommendations", filters=filters, order_by="priority", limit=limit, offset=offset
        )

    def ingest_zotero_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Upsert a canonical Zotero snapshot without touching zotero.sqlite."""

        library = snapshot.get("library") if isinstance(snapshot.get("library"), Mapping) else {}
        library_id = str(snapshot.get("libraryId") or library.get("id") or "1")
        bridge_id = str(snapshot.get("bridgeId") or "zotero")
        collection_ids: dict[str, str] = {}
        for collection in snapshot.get("collections", []):
            if not isinstance(collection, Mapping) or not collection.get("key"):
                continue
            key = str(collection["key"])
            record_id = uuid.uuid5(uuid.NAMESPACE_URL, f"zotero-collection:{library_id}:{key}").hex
            collection_ids[key] = record_id
            self.upsert(
                "collections",
                {
                    "id": record_id,
                    "name": str(collection.get("name") or key),
                    "metadata": {
                        "source": "zotero",
                        "libraryId": library_id,
                        "key": key,
                        "version": collection.get("version"),
                        "dateModified": collection.get("dateModified"),
                        "parentKey": collection.get("parentKey"),
                    },
                },
            )
        for collection in snapshot.get("collections", []):
            if not isinstance(collection, Mapping):
                continue
            key = str(collection.get("key") or "")
            parent_key = str(collection.get("parentKey") or "")
            if key in collection_ids and parent_key in collection_ids:
                self.update("collections", collection_ids[key], {"parent_id": collection_ids[parent_key]})

        existing_papers = self.list("papers", limit=10_000, offset=0)
        by_zotero_key: dict[tuple[str, str], dict[str, Any]] = {}
        by_doi: dict[str, dict[str, Any]] = {}
        by_title_year: dict[tuple[str, int | None], dict[str, Any]] = {}
        for existing in existing_papers:
            metadata = existing.get("metadata") if isinstance(existing.get("metadata"), Mapping) else {}
            existing_item_key = str(metadata.get("itemKey") or "")
            existing_library = str(metadata.get("libraryId") or "")
            if existing_item_key and existing_library:
                by_zotero_key[(existing_library, existing_item_key)] = existing
            existing_doi = str(existing.get("doi") or "").strip().lower()
            if existing_doi:
                by_doi[existing_doi] = existing
            by_title_year[(_normalized_title(existing.get("title")), existing.get("year"))] = existing

        papers_upserted = 0
        for item in snapshot.get("items", []):
            if not isinstance(item, Mapping) or not item.get("key"):
                continue
            item_key = str(item["key"])
            creators = item.get("creators") if isinstance(item.get("creators"), list) else []
            authors = []
            for creator in creators:
                if isinstance(creator, str):
                    authors.append(creator)
                elif isinstance(creator, Mapping):
                    name = str(creator.get("name") or "").strip()
                    if not name:
                        name = " ".join(
                            filter(
                                None,
                                (
                                    str(creator.get("firstName") or "").strip(),
                                    str(creator.get("lastName") or "").strip(),
                                ),
                            )
                        )
                    if name:
                        authors.append(name)
            raw_year = item.get("year")
            if raw_year in (None, ""):
                match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|2100)\b", str(item.get("date") or ""))
                raw_year = int(match.group(1)) if match else None
            try:
                year = int(raw_year) if raw_year not in (None, "") else None
            except (TypeError, ValueError):
                year = None
            doi = str(item.get("DOI") or item.get("doi") or "").strip() or None
            title = str(item.get("title") or "未命名文献")
            existing = by_zotero_key.get((library_id, item_key))
            if existing is None and doi:
                existing = by_doi.get(doi.lower())
            if existing is None:
                existing = by_title_year.get((_normalized_title(title), year))
            paper_id = (
                str(existing["id"])
                if existing is not None
                else uuid.uuid5(uuid.NAMESPACE_URL, f"zotero-item:{library_id}:{item_key}").hex
            )
            existing_metadata = (
                dict(existing.get("metadata") or {})
                if existing is not None and isinstance(existing.get("metadata"), Mapping)
                else {}
            )
            existing_metadata.update(
                {
                    "bridgeId": bridge_id,
                    "libraryId": library_id,
                    "itemKey": item_key,
                    "itemType": item.get("itemType"),
                    "version": item.get("version"),
                    "dateModified": item.get("dateModified"),
                }
            )
            paper = self.upsert_paper(
                {
                    "id": paper_id,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "doi": doi,
                    "abstract": str(item.get("abstractNote") or item.get("abstract") or ""),
                    "venue": str(item.get("publicationTitle") or item.get("venue") or ""),
                    "url": str(item.get("url") or "").strip() or None,
                    "source_type": str(existing.get("source_type") or "zotero") if existing else "zotero",
                    "source_key": f"{library_id}:{item_key}",
                    "metadata": existing_metadata,
                }
            )
            by_zotero_key[(library_id, item_key)] = paper
            if doi:
                by_doi[doi.lower()] = paper
            by_title_year[(_normalized_title(title), year)] = paper
            papers_upserted += 1
            selected_collections = item.get("collectionKeys") or item.get("collections") or []
            for collection_key in selected_collections:
                record_id = collection_ids.get(str(collection_key))
                if record_id:
                    self.add_paper_to_collection(record_id, paper["id"])
            for tag_value in item.get("tags", []):
                name = str(tag_value.get("tag") if isinstance(tag_value, Mapping) else tag_value).strip()
                if not name:
                    continue
                tag_id = uuid.uuid5(uuid.NAMESPACE_URL, f"zotero-tag:{name.casefold()}").hex
                self.upsert("tags", {"id": tag_id, "name": name})
                self.tag_paper(paper["id"], tag_id)

        self.insert(
            "sync_audit",
            {
                "direction": "inbound",
                "entity_type": "zotero_library",
                "entity_id": library_id,
                "action": "snapshot",
                "status": "accepted",
                "detail": {
                    "bridgeId": bridge_id,
                    "papersUpserted": papers_upserted,
                    "collectionsUpserted": len(collection_ids),
                },
            },
        )
        return {
            "papersUpserted": papers_upserted,
            "collectionsUpserted": len(collection_ids),
            "bridgeId": bridge_id,
            "libraryId": library_id,
        }

    def create_zotero_command(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("zotero_commands", values)

    def list_zotero_commands(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0, **_filters: Any
    ) -> list[dict[str, Any]]:
        return self.list(
            "zotero_commands",
            filters={"status": status} if status else None,
            limit=limit,
            offset=offset,
        )

    def record_sync_audit(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.insert("sync_audit", values)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "WorkspaceStore",
    "backup_database",
    "canonicalize_filename",
    "scan_migration_sources",
    "scan_workspace",
]
