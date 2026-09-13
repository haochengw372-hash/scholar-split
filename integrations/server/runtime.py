"""Runtime bootstrap kept separate from the upstream Flask application."""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path
from typing import Any

from .workspace_api import create_workspace_blueprint
from .research_service import ResearchService
from .workspace_store import WorkspaceStore


class WorkspaceIngestWatcher:
    """Debounced directory watcher for completed translations and guides."""

    def __init__(self, store: WorkspaceStore, root: Path, interval_seconds: float = 5.0) -> None:
        self.store = store
        self.root = root
        self.interval_seconds = interval_seconds
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scholarsplit-ingest", daemon=True)
        self._fingerprint = self._current_fingerprint()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1)

    def _current_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        entries: list[tuple[str, int, int]] = []
        for directory, suffixes in (
            (self.root / "translated", {".pdf"}),
            (self.root / "reading-guides", {".json", ".md"}),
        ):
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(entries))

    def _run(self) -> None:
        while not self._stopped.wait(self.interval_seconds):
            fingerprint = self._current_fingerprint()
            if fingerprint == self._fingerprint:
                continue
            self._fingerprint = fingerprint
            try:
                self.store.import_scan(self.store.scan_migration_sources())
            except Exception:
                # Translation must remain available even if indexing fails; the
                # next filesystem change or manual scan retries the import.
                continue


def _pairing_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            os.chmod(path, 0o600)
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return token


def initialize_workspace(app: Any, server_root: str | Path) -> WorkspaceStore:
    root = Path(server_root).expanduser().resolve()
    integration_root = Path(__file__).resolve().parent
    dashboard = integration_root / "dashboard"
    if not dashboard.is_dir():
        dashboard = integration_root.parents[1] / "dashboard"
    data_dir = root / "data"
    store = WorkspaceStore(data_dir / "scholarsplit.sqlite3", root)
    store.research_service = ResearchService(store, root)
    watcher = WorkspaceIngestWatcher(store, root)
    watcher.start()
    store._on_close.append(watcher.stop)
    app.extensions["scholarsplit_ingest_watcher"] = watcher
    token = _pairing_token(data_dir / "zotero-pairing-token")
    app.register_blueprint(
        create_workspace_blueprint(
            store,
            dashboard,
            root / "translated",
            root / "reading-guides",
            pairing_token=token,
            research_service=store.research_service,
        )
    )
    return store
