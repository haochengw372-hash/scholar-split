"""Flask adapter for the ScholarSplit local research workspace.

The blueprint deliberately owns only ``/api/v1``, ``/workspace`` and
``/workspace-assets``.  It can therefore be registered on the existing local
service without replacing any of the extension's v0.1 routes.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import math
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from flask import Blueprint, Response, current_app, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import BadRequest, Forbidden, HTTPException, NotFound, UnsupportedMediaType

try:  # The store is optional at import time so this module can be embedded.
    from .workspace_store import scan_workspace as _store_scan_workspace
except (ImportError, ModuleNotFoundError):  # pragma: no cover - integration timing
    try:
        from workspace_store import scan_workspace as _store_scan_workspace
    except (ImportError, ModuleNotFoundError):  # pragma: no cover
        _store_scan_workspace = None


_MISSING = object()
_EXTENSION_API_PATHS = frozenset({"/api/v1/library/scan", "/api/v1/pdf/read-local"})
_SECRET_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "pairing_token",
    "password",
    "secret",
}
_TABLES = {
    "papers": "papers",
    "collections": "collections",
    "tags": "tags",
    "notes": "notes",
    "review-projects": "review_projects",
    "members": "review_members",
    "screening": "screening_decisions",
    "evidence": "evidence",
    "syntheses": "syntheses",
    "gaps": "gaps",
    "recommendations": "recommendations",
    "acquisitions": "acquisitions",
    "jobs": "jobs",
    "events": "sync_audit",
    "zotero-snapshots": "sync_audit",
    "zotero-commands": "zotero_commands",
    "zotero-acks": "sync_audit",
}
_METHOD_ALIASES = {
    "screening": "screening_decisions",
    "evidence": "evidence_items",
}


class StoreCapabilityError(RuntimeError):
    """Raised when neither a typed nor generic store operation is present."""


def _clean(value: Any) -> Any:
    """Remove credentials from responses while retaining Zotero item ``key`` fields."""
    if isinstance(value, Mapping):
        return {
            str(key): _clean(item)
            for key, item in value.items()
            if str(key).lower().replace("-", "_") not in _SECRET_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _reject_secret_fields(value: Any, path: str = "payload") -> None:
    """Keep credentials out of the local database and response boundary."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if normalized in _SECRET_FIELDS or compact in {
                "apikey", "accesstoken", "refreshtoken", "clientsecret", "pairingtoken"
            }:
                raise BadRequest(f"{path}.{key} is a secret field and must not be submitted")
            _reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")


def _success(data: Any = None, status_code: int = 200, **extra: Any):
    payload = {"status": "success", "data": _clean(data)}
    payload.update(_clean(extra))
    return jsonify(payload), status_code


def _error(message: str, error_type: str, status_code: int, **extra: Any):
    payload = {"status": "error", "message": message, "errorType": error_type}
    payload.update(_clean(extra))
    return jsonify(payload), status_code


def _json_body(*, required: bool = True) -> dict[str, Any]:
    if not request.is_json:
        raise UnsupportedMediaType("Content-Type must be application/json")
    value = request.get_json(silent=False)
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise BadRequest("JSON body must be an object")
    _reject_secret_fields(value)
    return value


def _positive_int(name: str, default: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise BadRequest(f"{name} must be between 1 and {maximum}")
    return value


def _invoke(store: Any, names: Iterable[str], *args: Any, **kwargs: Any) -> Any:
    """Invoke the first supported method, filtering optional keyword arguments."""
    for name in names:
        method = getattr(store, name, None)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(*args, **kwargs)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        call_kwargs = kwargs if accepts_kwargs else {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
        return method(*args, **call_kwargs)
    return _MISSING


def _method_stems(resource: str) -> list[str]:
    stems = [resource.replace("-", "_")]
    alias = _METHOD_ALIASES.get(resource)
    if alias:
        stems.append(alias)
    return stems


def _singular(stem: str) -> str:
    if stem == "syntheses":
        return "synthesis"
    if stem.endswith("ies"):
        return stem[:-3] + "y"
    if stem.endswith("ses"):
        return stem[:-2]
    if stem.endswith("s"):
        return stem[:-1]
    return stem


def _unpack_list(result: Any, resource: str) -> tuple[list[Any], int]:
    if result is None:
        return [], 0
    if isinstance(result, Mapping):
        candidates = ("items", "results", "data", resource, resource.replace("-", "_"))
        items = next((result[key] for key in candidates if isinstance(result.get(key), list)), [])
        total = result.get("total", result.get("count", len(items)))
        return list(items), int(total)
    if isinstance(result, (list, tuple)):
        return list(result), len(result)
    return list(result), 0


def _list_records(
    store: Any,
    resource: str,
    *,
    page: int,
    page_size: int,
    filters: Mapping[str, Any] | None = None,
) -> tuple[list[Any], int]:
    filters = {key: value for key, value in (filters or {}).items() if value not in (None, "")}
    offset = (page - 1) * page_size
    typed_names = [f"list_{stem}" for stem in _method_stems(resource)]
    result = _invoke(
        store,
        typed_names,
        **filters,
        search=filters.get("q"),
        limit=10_000,
        offset=0,
        page=1,
        page_size=page_size,
    )
    if result is _MISSING:
        generic = getattr(store, "list", None)
        if not callable(generic):
            raise StoreCapabilityError(f"Listing {resource} is not supported by this store")
        try:
            result = generic(_TABLES[resource], limit=10_000, offset=0)
        except ValueError as exc:
            if "unsupported entity table" in str(exc):
                raise StoreCapabilityError(f"Listing {resource} is not supported by this store") from exc
            raise
    items, total = _unpack_list(result, resource)
    # Apply filters after retrieval too: older typed stores may accept search
    # but not yet implement collection, tag, status, or project joins.
    def field(item: Mapping[str, Any], name: str) -> Any:
        if name in item:
            return item[name]
        metadata = item.get("metadata")
        return metadata.get(name) if isinstance(metadata, Mapping) else None

    def matches(item: Any) -> bool:
        if not isinstance(item, Mapping):
            return True
        for key, expected in filters.items():
            if key == "q":
                haystack = " ".join(str(value) for value in item.values()).lower()
                if str(expected).lower() not in haystack:
                    return False
            elif key in {"collection", "collection_id"}:
                actual = field(item, "collection_id")
                collections = field(item, "collection_ids") or field(item, "collections") or []
                if str(actual) != str(expected) and str(expected) not in {str(value) for value in collections}:
                    return False
            elif key == "tag":
                tags = field(item, "tags") or []
                values = {
                    str(tag.get("id") or tag.get("name")) if isinstance(tag, Mapping) else str(tag)
                    for tag in tags
                }
                if str(expected) not in values:
                    return False
            elif key == "scope":
                source_type = str(field(item, "source_type") or "")
                if expected == "scholarsplit" and source_type not in {
                    "chrome_extension",
                    "manual_import",
                    "scholarsplit",
                }:
                    return False
                if expected == "zotero" and source_type != "zotero":
                    return False
                if expected == "legacy" and source_type != "legacy_translation":
                    return False
            elif str(field(item, key) or "") != str(expected):
                return False
        return True

    if filters:
        items = [item for item in items if matches(item)]
        total = len(items)
    elif not isinstance(result, Mapping):
        total = len(items)
    return items[offset : offset + page_size], total


def _get_record(store: Any, resource: str, record_id: str) -> Any:
    names: list[str] = []
    for stem in _method_stems(resource):
        names.extend((f"get_{_singular(stem)}", f"get_{stem}"))
    result = _invoke(store, names, record_id)
    if result is _MISSING:
        method = getattr(store, "get", None)
        if not callable(method):
            raise StoreCapabilityError(f"Reading {resource} is not supported by this store")
        result = method(_TABLES[resource], record_id)
    if result is None:
        raise NotFound(f"{resource} record not found")
    return result


def _create_record(store: Any, resource: str, values: Mapping[str, Any]) -> Any:
    payload = dict(values)
    payload.setdefault("id", str(uuid.uuid4()))
    names: list[str] = []
    for stem in _method_stems(resource):
        singular = _singular(stem)
        names.extend((f"create_{singular}", f"upsert_{singular}"))
    result = _invoke(store, names, payload)
    if result is _MISSING:
        method = getattr(store, "insert", None)
        if not callable(method):
            raise StoreCapabilityError(f"Creating {resource} is not supported by this store")
        result = method(_TABLES[resource], payload)
    return result if result is not None else payload


def _update_record(store: Any, resource: str, record_id: str, values: Mapping[str, Any]) -> Any:
    payload = dict(values)
    payload.pop("id", None)
    names: list[str] = []
    for stem in _method_stems(resource):
        singular = _singular(stem)
        names.extend((f"update_{singular}", f"patch_{singular}"))
    result = _invoke(store, names, record_id, payload)
    if result is _MISSING:
        method = getattr(store, "update", None)
        if not callable(method):
            raise StoreCapabilityError(f"Updating {resource} is not supported by this store")
        result = method(_TABLES[resource], record_id, payload)
    if result is None:
        raise NotFound(f"{resource} record not found")
    return result


def _delete_record(store: Any, resource: str, record_id: str) -> Any:
    names = [f"delete_{_singular(stem)}" for stem in _method_stems(resource)]
    result = _invoke(store, names, record_id)
    if result is _MISSING:
        method = getattr(store, "delete", None)
        if not callable(method):
            raise StoreCapabilityError(f"Deleting {resource} is not supported by this store")
        result = method(_TABLES[resource], record_id)
    if result in (None, False):
        raise NotFound(f"{resource} record not found")
    return {"id": record_id, "deleted": True}


def _pagination(page: int, page_size: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size) if total else 0,
    }


def _fallback_scan(translated_dir: Path, guide_dir: Path) -> dict[str, Any]:
    translated = sorted(translated_dir.glob("*.pdf")) if translated_dir.is_dir() else []
    guides = []
    if guide_dir.is_dir():
        guides = sorted(path for path in guide_dir.iterdir() if path.suffix.lower() in {".json", ".md"})
    guide_by_stem = {path.stem: path for path in guides}
    matches = []
    unmatched = []
    for pdf in translated:
        guide = guide_by_stem.get(pdf.stem)
        item = {"paperId": pdf.stem, "translatedFile": pdf.name}
        if guide:
            item["guideFile"] = guide.name
            matches.append(item)
        else:
            unmatched.append(item)
    return {
        "translated_pdfs": [path.name for path in translated],
        "reading_guides": [path.name for path in guides],
        "matches": matches,
        "ambiguous": [],
        "unmatched": unmatched,
    }


def _scan(store: Any, translated_dir: Path, guide_dir: Path) -> dict[str, Any]:
    result = _invoke(store, ("scan_workspace", "scan_library"), translated_dir, guide_dir)
    if result is not _MISSING:
        return dict(result)
    if (
        _store_scan_workspace is not None
        and translated_dir.name == "translated"
        and guide_dir.name == "reading-guides"
        and translated_dir.parent == guide_dir.parent
    ):
        try:
            return dict(_store_scan_workspace(translated_dir.parent))
        except (OSError, TypeError, ValueError):
            pass
    return _fallback_scan(translated_dir, guide_dir)


def create_workspace_blueprint(
    store: Any,
    dashboard_dir: str | Path | None,
    translated_dir: str | Path,
    guide_dir: str | Path,
    pairing_token: str | None = None,
    research_service: Any | None = None,
) -> Blueprint:
    """Build the v0.2 blueprint without modifying or starting a Flask app."""

    if dashboard_dir:
        dashboard_path = Path(dashboard_dir).resolve()
    else:
        repository_dashboard = Path(__file__).resolve().parents[2] / "dashboard"
        dashboard_path = repository_dashboard if repository_dashboard.is_dir() else Path(__file__).with_name("dashboard")
    translated_path = Path(translated_dir).resolve()
    guide_path = Path(guide_dir).resolve()
    api = Blueprint("scholar_split_workspace", __name__)

    @api.before_request
    def local_same_origin_only():
        try:
            peer = ipaddress.ip_address(request.remote_addr or "")
        except ValueError as exc:
            raise Forbidden("Workspace API is available only from the local computer") from exc
        mapped_peer = getattr(peer, "ipv4_mapped", None)
        if not peer.is_loopback and not (mapped_peer and mapped_peer.is_loopback):
            raise Forbidden("Workspace API is available only from the local computer")
        origin = request.headers.get("Origin")
        if origin:
            expected = request.host_url.rstrip("/")
            extension_request = (
                origin.startswith("chrome-extension://")
                and request.path in _EXTENSION_API_PATHS
                and (
                    request.method == "OPTIONS"
                    or request.headers.get("X-ScholarSplit-Client") == "chrome-extension"
                )
            )
            if origin.rstrip("/") != expected and not extension_request:
                raise Forbidden("Cross-origin requests are not allowed")

    @api.after_request
    def harden_response(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.path.startswith("/api/v1/"):
            response.headers.setdefault("Cache-Control", "no-store")
        origin = request.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") and request.path in _EXTENSION_API_PATHS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-ScholarSplit-Client"
            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response

    @api.errorhandler(StoreCapabilityError)
    def unsupported(error):
        return _error(str(error), "not_supported", 501)

    @api.errorhandler(ValueError)
    @api.errorhandler(sqlite3.IntegrityError)
    def validation_error(error):
        return _error(str(error), "validation_error", 400)

    @api.errorhandler(HTTPException)
    def http_error(error):
        return _error(error.description, error.name.lower().replace(" ", "_"), error.code or 500)

    @api.errorhandler(Exception)
    def unexpected_error(error):  # pragma: no cover - defensive app boundary
        current_app.logger.exception("Workspace API request failed", exc_info=error)
        return _error("The workspace request failed", "internal_error", 500)

    @api.get("/workspace")
    @api.get("/workspace/")
    def dashboard_index():
        if not (dashboard_path / "index.html").is_file():
            raise NotFound("Workspace dashboard is not installed")
        return send_from_directory(dashboard_path, "index.html")

    @api.get("/workspace-assets/<path:filename>")
    def dashboard_asset(filename: str):
        if filename in {"icon-16.png", "icon-32.png", "icon-48.png", "icon-128.png"}:
            local_asset = dashboard_path / filename
            if not local_asset.is_file():
                return send_from_directory(Path(__file__).resolve().parents[2] / "icons", filename)
        return send_from_directory(dashboard_path, filename)

    @api.get("/api/v1/summary")
    def summary():
        result = _invoke(store, ("get_summary", "summary"))
        if result is _MISSING:
            counts = {}
            for resource in ("papers", "collections", "review-projects", "recommendations", "jobs"):
                try:
                    _, counts[resource.replace("-", "_")] = _list_records(
                        store, resource, page=1, page_size=1
                    )
                except StoreCapabilityError:
                    counts[resource.replace("-", "_")] = 0
            result = {"counts": counts}
        return _success(result)

    @api.get("/api/v1/papers")
    def papers():
        page = _positive_int("page", 1, 1_000_000)
        page_size = _positive_int("pageSize", 25, 200)
        filters = {
            "q": request.args.get("q"),
            "collection": request.args.get("collection"),
            "collection_id": request.args.get("collection"),
            "tag": request.args.get("tag"),
            "status": request.args.get("status"),
            "scope": request.args.get("scope"),
        }
        items, total = _list_records(store, "papers", page=page, page_size=page_size, filters=filters)
        return _success(items, pagination=_pagination(page, page_size, total))

    @api.get("/api/v1/papers/<record_id>")
    def paper_detail(record_id: str):
        return _success(_get_record(store, "papers", record_id))

    @api.patch("/api/v1/papers/<record_id>")
    def paper_update(record_id: str):
        return _success(_update_record(store, "papers", record_id, _json_body()))

    @api.post("/api/v1/papers/<record_id>/zotero-collections")
    def paper_to_zotero_collection(record_id: str):
        """Queue a Zotero folder assignment or a local-paper import.

        The browser supplies only a ScholarSplit paper id and a synced Zotero
        collection id. File paths and Zotero keys are resolved from trusted
        local records, never accepted from the web request.
        """

        body = _json_body()
        collection_id = str(body.get("collectionId") or body.get("collection_id") or "").strip()
        if not collection_id:
            raise BadRequest("collectionId is required")
        paper = _get_record(store, "papers", record_id)
        collection = _get_record(store, "collections", collection_id)
        collection_meta = collection.get("metadata") if isinstance(collection, Mapping) else None
        collection_meta = collection_meta if isinstance(collection_meta, Mapping) else {}
        if collection_meta.get("source") != "zotero":
            raise BadRequest("The destination must be a synced Zotero collection")
        collection_key = str(collection_meta.get("key") or "")
        if not collection_key:
            raise BadRequest("The Zotero collection is missing its stable key")
        try:
            library_id = int(collection_meta.get("libraryId"))
        except (TypeError, ValueError) as exc:
            raise BadRequest("The Zotero collection is missing its library id") from exc

        paper_meta = paper.get("metadata") if isinstance(paper, Mapping) else None
        paper_meta = paper_meta if isinstance(paper_meta, Mapping) else {}
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        command_id = str(uuid.uuid4())
        preconditions = [
            {
                "entityType": "collection",
                "key": collection_key,
                "version": int(collection_meta.get("version") or 0),
                "dateModified": str(collection_meta.get("dateModified") or ""),
            }
        ]
        item_key = str(paper_meta.get("itemKey") or "")
        if item_key and str(paper_meta.get("libraryId") or library_id) == str(library_id):
            preconditions.append(
                {
                    "entityType": "item",
                    "key": item_key,
                    "version": int(paper_meta.get("version") or 0),
                    "dateModified": str(paper_meta.get("dateModified") or ""),
                }
            )
            kind = "collection.addItems"
            payload = {
                "commandId": command_id,
                "libraryId": library_id,
                "issuedAt": now,
                "type": kind,
                "collectionKey": collection_key,
                "itemKeys": [item_key],
                "preconditions": preconditions,
                "_local": {"paperId": record_id, "collectionId": collection_id},
            }
        else:
            artifacts = paper.get("artifacts") if isinstance(paper, Mapping) else []
            originals = [
                artifact
                for artifact in artifacts or []
                if isinstance(artifact, Mapping) and artifact.get("kind") == "original_pdf"
            ]
            source = originals[0] if originals else next(
                (
                    artifact
                    for artifact in artifacts or []
                    if isinstance(artifact, Mapping) and artifact.get("kind") == "translated_pdf"
                ),
                None,
            )
            source_path = Path(str(source.get("path") or "")).expanduser().resolve() if source else None
            if source_path is None or not source_path.is_file() or source_path.suffix.lower() != ".pdf":
                raise BadRequest("This paper has no local PDF that can be imported into Zotero")
            kind = "item.importPaper"
            payload = {
                "commandId": command_id,
                "libraryId": library_id,
                "issuedAt": now,
                "type": kind,
                "collectionKey": collection_key,
                "paper": {
                    "title": str(paper.get("title") or "Untitled paper"),
                    "authors": list(paper.get("authors") or []),
                    "year": paper.get("year"),
                    "doi": paper.get("doi"),
                    "abstract": str(paper.get("abstract") or ""),
                    "url": paper.get("url"),
                },
                "attachmentPath": str(source_path),
                "preconditions": preconditions,
                "_local": {"paperId": record_id, "collectionId": collection_id},
            }
        create_command = getattr(store, "create_zotero_command", None)
        command = (
            create_command({"id": command_id, "kind": kind, "status": "pending", "payload": payload})
            if callable(create_command)
            else _create_record(
                store,
                "zotero-commands",
                {"id": command_id, "kind": kind, "status": "pending", "payload": payload},
            )
        )
        return _success(
            {
                "id": command.get("id", command_id) if isinstance(command, Mapping) else command_id,
                "kind": kind,
                "status": command.get("status", "pending") if isinstance(command, Mapping) else "pending",
            },
            202,
        )

    def run_scan(commit: bool):
        if not isinstance(commit, bool):
            raise BadRequest("commit must be a boolean")
        scan = _scan(store, translated_path, guide_path)
        if not commit:
            return _success(scan)
        result = _invoke(store, ("import_scan", "commit_scan", "import_workspace_scan"), scan)
        if result is _MISSING:
            imported = []
            for match in scan.get("matches", []):
                if isinstance(match, Mapping):
                    imported.append(_create_record(store, "papers", match))
            result = {"scan": scan, "imported": imported, "importedCount": len(imported)}
        return _success(result, 201)

    @api.post("/api/v1/library/scan")
    def library_scan():
        body = _json_body(required=False)
        return run_scan(body.get("commit", False))

    @api.post("/api/v1/scan/preview")
    def scan_preview():
        _json_body(required=False)
        return run_scan(False)

    @api.post("/api/v1/scan/import")
    def scan_import():
        _json_body(required=False)
        return run_scan(True)

    @api.post("/api/v1/pdf/read-local")
    def read_downloaded_pdf():
        body = _json_body()
        path = Path(str(body.get("path") or "")).expanduser().resolve()
        if (
            path.parent.name != "ScholarSplit"
            or not path.name.startswith("scholarsplit-")
            or path.suffix.lower() != ".pdf"
            or not path.is_file()
        ):
            raise BadRequest("ScholarSplit temporary PDF was not found")
        if path.stat().st_size > 100 * 1024 * 1024:
            raise BadRequest("PDF exceeds the 100 MB limit")
        content = path.read_bytes()
        if b"%PDF-" not in content[:1024] or b"%%EOF" not in content[-65536:]:
            raise BadRequest("Downloaded file is not a complete PDF")
        return Response(content, mimetype="application/pdf")

    def register_crud(resource: str):
        base = f"/api/v1/{resource}"
        endpoint_stem = resource.replace("-", "_")

        def list_view():
            page = _positive_int("page", 1, 1_000_000)
            page_size = _positive_int("pageSize", 25, 200)
            filters: dict[str, Any] = {}
            if resource == "recommendations":
                filters = {
                    "project_id": request.args.get("projectId") or request.args.get("project_id"),
                    "status": request.args.get("status"),
                }
            elif resource == "notes":
                filters = {
                    "paper_id": request.args.get("paperId") or request.args.get("paper_id"),
                    "project_id": request.args.get("projectId") or request.args.get("project_id"),
                }
            elif resource in {"collections", "tags"}:
                filters = {"q": request.args.get("q")}
            items, total = _list_records(
                store, resource, page=page, page_size=page_size, filters=filters
            )
            return _success(items, pagination=_pagination(page, page_size, total))

        def create_view():
            return _success(_create_record(store, resource, _json_body()), 201)

        def get_view(record_id: str):
            return _success(_get_record(store, resource, record_id))

        def patch_view(record_id: str):
            return _success(_update_record(store, resource, record_id, _json_body()))

        def delete_view(record_id: str):
            return _success(_delete_record(store, resource, record_id))

        api.add_url_rule(base, f"list_{endpoint_stem}", list_view, methods=["GET"])
        api.add_url_rule(base, f"create_{endpoint_stem}", create_view, methods=["POST"])
        api.add_url_rule(f"{base}/<record_id>", f"get_{endpoint_stem}", get_view, methods=["GET"])
        api.add_url_rule(f"{base}/<record_id>", f"patch_{endpoint_stem}", patch_view, methods=["PATCH"])
        api.add_url_rule(f"{base}/<record_id>", f"delete_{endpoint_stem}", delete_view, methods=["DELETE"])

    for _resource in ("collections", "tags", "notes", "review-projects", "recommendations"):
        register_crud(_resource)

    def register_nested(resource: str):
        base = f"/api/v1/review-projects/<project_id>/{resource}"
        endpoint_stem = f"project_{resource}"

        def list_view(project_id: str):
            page = _positive_int("page", 1, 1_000_000)
            page_size = _positive_int("pageSize", 25, 200)
            items, total = _list_records(
                store, resource, page=page, page_size=page_size, filters={"project_id": project_id}
            )
            return _success(items, pagination=_pagination(page, page_size, total))

        def create_view(project_id: str):
            body = _json_body()
            body["project_id"] = project_id
            return _success(_create_record(store, resource, body), 201)

        def get_view(project_id: str, record_id: str):
            record = _get_record(store, resource, record_id)
            if isinstance(record, Mapping) and str(record.get("project_id")) != str(project_id):
                raise NotFound(f"{resource} record not found")
            return _success(record)

        def patch_view(project_id: str, record_id: str):
            existing = _get_record(store, resource, record_id)
            if isinstance(existing, Mapping) and str(existing.get("project_id")) != str(project_id):
                raise NotFound(f"{resource} record not found")
            return _success(_update_record(store, resource, record_id, _json_body()))

        def delete_view(project_id: str, record_id: str):
            existing = _get_record(store, resource, record_id)
            if isinstance(existing, Mapping) and str(existing.get("project_id")) != str(project_id):
                raise NotFound(f"{resource} record not found")
            return _success(_delete_record(store, resource, record_id))

        api.add_url_rule(base, f"list_{endpoint_stem}", list_view, methods=["GET"])
        api.add_url_rule(base, f"create_{endpoint_stem}", create_view, methods=["POST"])
        api.add_url_rule(f"{base}/<record_id>", f"get_{endpoint_stem}", get_view, methods=["GET"])
        api.add_url_rule(f"{base}/<record_id>", f"patch_{endpoint_stem}", patch_view, methods=["PATCH"])
        api.add_url_rule(f"{base}/<record_id>", f"delete_{endpoint_stem}", delete_view, methods=["DELETE"])

    for _nested_resource in ("members", "screening", "evidence", "syntheses", "gaps"):
        register_nested(_nested_resource)

    @api.post("/api/v1/acquisitions/preview")
    def acquisition_preview():
        body = _json_body()
        result = _invoke(store, ("preview_acquisition", "preview_acquisitions"), body)
        if result is _MISSING:
            result = {"dryRun": True, "request": body, "requiresConfirmation": True}
        return _success(result)

    @api.post("/api/v1/acquisitions/confirm")
    def acquisition_confirm():
        body = _json_body()
        if body.get("confirmed") is not True:
            raise BadRequest("confirmed must be true")
        result = _invoke(store, ("confirm_acquisition", "confirm_acquisitions"), body)
        if result is _MISSING:
            now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            record = {
                "paper_id": body.get("paper_id") or body.get("paperId"),
                "provider": body.get("provider"),
                "status": "pending",
                "url": body.get("url"),
                "metadata": {"request": body},
                "requested_at": now,
            }
            result = _create_record(
                store, "acquisitions", {key: value for key, value in record.items() if value is not None}
            )
        return _success(result, 202)

    @api.get("/api/v1/acquisitions")
    def acquisitions():
        page = _positive_int("page", 1, 1_000_000)
        page_size = _positive_int("pageSize", 25, 200)
        items, total = _list_records(
            store,
            "acquisitions",
            page=page,
            page_size=page_size,
            filters={"status": request.args.get("status")},
        )
        return _success(items, pagination=_pagination(page, page_size, total))

    @api.get("/api/v1/acquisitions/<record_id>")
    def acquisition_detail(record_id: str):
        return _success(_get_record(store, "acquisitions", record_id))

    def require_pairing_token():
        if pairing_token is None:
            return
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {pairing_token}"
        if not secrets.compare_digest(supplied, expected):
            raise Forbidden("A valid local pairing token is required")

    @api.post("/api/v1/zotero/snapshot")
    @api.post("/api/v1/zotero/snapshots")
    def zotero_snapshot():
        require_pairing_token()
        body = _json_body()
        if not isinstance(body.get("items", []), list):
            raise BadRequest("items must be an array")
        result = _invoke(store, ("ingest_zotero_snapshot", "save_zotero_snapshot"), body)
        if result is _MISSING:
            result = _create_record(
                store,
                "zotero-snapshots",
                {
                    "direction": "inbound",
                    "entity_type": "zotero_library",
                    "entity_id": body.get("libraryId") or body.get("bridgeId"),
                    "action": "snapshot",
                    "status": "accepted",
                    "detail": body,
                },
            )
        return _success(result, 202)

    @api.get("/api/v1/zotero/commands")
    def zotero_commands():
        require_pairing_token()
        after = request.args.get("after")
        bridge_id = request.args.get("bridgeId")
        library_id = request.args.get("libraryId")
        result = _invoke(
            store,
            ("get_zotero_commands", "list_zotero_commands"),
            after=after,
            bridge_id=bridge_id,
            library_id=library_id,
            limit=100,
        )
        if result is _MISSING:
            rows, _ = _list_records(
                store,
                "zotero-commands",
                page=1,
                page_size=100,
                filters={"status": "pending"},
            )
        elif isinstance(result, list):
            rows = result
        else:
            rows = None

        if rows is not None:
            commands = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
                command = dict(payload)
                command.setdefault("commandId", row.get("id"))
                command.setdefault("type", row.get("kind"))
                command.setdefault("issuedAt", row.get("created_at"))
                if bridge_id and command.get("bridgeId") not in (None, bridge_id):
                    continue
                if after and str(command.get("commandId", "")) <= after:
                    continue
                command.pop("bridgeId", None)
                command.pop("_local", None)
                commands.append(command)
            next_cursor = str(commands[-1]["commandId"]) if commands else (after or "")
            result = {
                "schemaVersion": "1.0",
                "commands": commands,
                "nextCursor": next_cursor,
                "pollAfterMs": 2000,
            }
        return _success(result)

    @api.post("/api/v1/zotero/acks")
    def zotero_ack_envelope():
        require_pairing_token()
        body = _json_body()
        command_id = body.get("commandId")
        if not command_id:
            raise BadRequest("commandId is required")
        return _success(_save_ack(str(command_id), body), 202)

    @api.post("/api/v1/zotero/commands/<command_id>/ack")
    def zotero_ack(command_id: str):
        require_pairing_token()
        body = _json_body()
        body.setdefault("commandId", command_id)
        return _success(_save_ack(command_id, body), 202)

    def _save_ack(command_id: str, body: Mapping[str, Any]):
        allowed = {"applied", "conflict", "rejected", "failed"}
        if body.get("status") not in allowed:
            raise BadRequest("status must be applied, conflict, rejected, or failed")
        result = _invoke(store, ("ack_zotero_command", "save_zotero_ack"), command_id, dict(body))
        if result is _MISSING:
            get_method = getattr(store, "get", None)
            update_method = getattr(store, "update", None)
            command = get_method("zotero_commands", command_id) if callable(get_method) else None
            if command is not None and callable(update_method):
                command_payload = command.get("payload") if isinstance(command, Mapping) else {}
                command_payload = command_payload if isinstance(command_payload, Mapping) else {}
                command_status = "completed" if body["status"] == "applied" else body["status"]
                changes = {
                    "status": command_status,
                    "result": body.get("result") or body.get("conflict") or {},
                    "error": body.get("error"),
                    "executed_at": body.get("completedAt")
                    or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                }
                update_method("zotero_commands", command_id, changes)
                local = command_payload.get("_local")
                local = local if isinstance(local, Mapping) else {}
                if body["status"] == "applied" and local.get("paperId") and local.get("collectionId"):
                    add_membership = getattr(store, "add_paper_to_collection", None)
                    if callable(add_membership):
                        add_membership(str(local["collectionId"]), str(local["paperId"]))
                    imported_key = (body.get("result") or {}).get("itemKey") if isinstance(body.get("result"), Mapping) else None
                    if imported_key:
                        paper = _get_record(store, "papers", str(local["paperId"]))
                        metadata = paper.get("metadata") if isinstance(paper, Mapping) else {}
                        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
                        metadata.update(
                            {
                                "itemKey": imported_key,
                                "libraryId": command_payload.get("libraryId"),
                                "bridgeId": command_payload.get("bridgeId", "zotero-desktop-main"),
                            }
                        )
                        update_method("papers", str(local["paperId"]), {"metadata": metadata})
            result = _create_record(
                store,
                "zotero-acks",
                {
                    "direction": "inbound",
                    "entity_type": "zotero_command",
                    "entity_id": command_id,
                    "action": "ack",
                    "status": str(body["status"]),
                    "detail": dict(body),
                },
            )
        return result

    @api.get("/api/v1/jobs")
    def jobs():
        page = _positive_int("page", 1, 1_000_000)
        page_size = _positive_int("pageSize", 25, 200)
        items, total = _list_records(
            store,
            "jobs",
            page=page,
            page_size=page_size,
            filters={"status": request.args.get("status")},
        )
        return _success(items, pagination=_pagination(page, page_size, total))

    @api.post("/api/v1/review-projects/<project_id>/analyze")
    def analyze_review_project(project_id: str):
        if research_service is None:
            raise StoreCapabilityError("Research analysis service is not configured")
        return _success(research_service.start_analysis(project_id), 202)

    @api.post("/api/v1/review-projects/<project_id>/recommendations/discover")
    def discover_review_recommendations(project_id: str):
        if research_service is None:
            raise StoreCapabilityError("Research discovery service is not configured")
        body = _json_body()
        query = str(body.get("query") or "").strip()
        if not query:
            raise BadRequest("query is required")
        return _success(research_service.discover(project_id, query, int(body.get("limit") or 12)), 201)

    @api.get("/api/v1/jobs/<record_id>")
    def job_detail(record_id: str):
        return _success(_get_record(store, "jobs", record_id))

    @api.get("/api/v1/events")
    def events():
        limit = _positive_int("limit", 100, 500)
        after = request.args.get("after") or request.headers.get("Last-Event-ID")
        result = _invoke(store, ("list_events", "get_events"), after=after, limit=limit)
        if result is _MISSING:
            try:
                items, _ = _list_records(store, "events", page=1, page_size=limit)
            except StoreCapabilityError:
                items = []
        else:
            items, _ = _unpack_list(result, "events")

        def stream():
            yield "retry: 5000\n"
            for event in items:
                event_id = event.get("id") if isinstance(event, Mapping) else None
                if event_id is not None:
                    yield f"id: {event_id}\n"
                yield "event: workspace\n"
                yield f"data: {json.dumps(_clean(event), ensure_ascii=False, separators=(',', ':'))}\n\n"
            yield ": snapshot-complete\n\n"

        return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-store"})

    return api


def create_blueprint(store: Any, translated_dir: str | Path, guide_dir: str | Path) -> Blueprint:
    """Compatibility constructor requested by the v0.2 integration contract."""
    return create_workspace_blueprint(store, None, translated_dir, guide_dir)


def register_classic_fallback(app: Any, legacy_endpoint: str, path: str = "/classic") -> None:
    """Expose an existing legacy view at ``/classic`` without replacing ``/``.

    This helper is intentionally opt-in: callers resolve the endpoint that
    already owns the classic UI, and registration fails rather than silently
    shadowing another route.
    """
    if path in {rule.rule for rule in app.url_map.iter_rules()}:
        raise ValueError(f"A route is already registered at {path}")
    if legacy_endpoint not in app.view_functions:
        raise ValueError(f"Unknown legacy endpoint: {legacy_endpoint}")
    endpoint_suffix = legacy_endpoint.replace(".", "_")
    app.add_url_rule(path, f"scholar_split_classic_{endpoint_suffix}", app.view_functions[legacy_endpoint])


__all__ = ["create_blueprint", "create_workspace_blueprint", "register_classic_fallback"]
