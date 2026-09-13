from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sys

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations.server.workspace_api import (
    create_blueprint,
    create_workspace_blueprint,
    register_classic_fallback,
)
from integrations.server.workspace_store import WorkspaceStore


class FakeStore:
    def __init__(self):
        self.tables = defaultdict(dict)

    def insert(self, table, values):
        record = dict(values)
        self.tables[table][record["id"]] = record
        return dict(record)

    def get(self, table, record_id):
        record = self.tables[table].get(record_id)
        return dict(record) if record else None

    def list(self, table, limit=100, offset=0, **_kwargs):
        return [dict(value) for value in self.tables[table].values()][offset : offset + limit]

    def update(self, table, record_id, values):
        if record_id not in self.tables[table]:
            return None
        self.tables[table][record_id].update(values)
        return dict(self.tables[table][record_id])

    def delete(self, table, record_id):
        return self.tables[table].pop(record_id, None) is not None

    def list_papers(self, search=None, collection_id=None, tag=None, status=None, limit=25, offset=0):
        values = list(self.tables["papers"].values())
        if search:
            values = [row for row in values if search.lower() in row.get("title", "").lower()]
        if collection_id:
            values = [row for row in values if row.get("collection_id") == collection_id]
        if tag:
            values = [row for row in values if tag in row.get("tags", [])]
        if status:
            values = [row for row in values if row.get("status") == status]
        return {"items": values[offset : offset + limit], "total": len(values)}

    def list_events(self, after=None, limit=100):
        values = list(self.tables["events"].values())
        if after:
            values = [row for row in values if row["id"] > after]
        return values[:limit]


@pytest.fixture()
def workspace(tmp_path: Path):
    dashboard = tmp_path / "dashboard"
    translated = tmp_path / "translated"
    guides = tmp_path / "guides"
    dashboard.mkdir()
    translated.mkdir()
    guides.mkdir()
    (dashboard / "index.html").write_text("<h1>Workspace</h1>", encoding="utf-8")
    (dashboard / "app.js").write_text("window.workspace = true;", encoding="utf-8")
    (translated / "paper-one.pdf").write_bytes(b"%PDF-1.4\n")
    (guides / "paper-one.json").write_text("{}", encoding="utf-8")

    store = FakeStore()
    store.insert(
        "papers",
        {"id": "p1", "title": "Local Research", "collection_id": "c1", "tags": ["ai"], "status": "ready"},
    )
    store.insert("papers", {"id": "p2", "title": "Other", "status": "queued"})
    store.insert("events", {"id": "e1", "type": "job.updated", "jobId": "j1"})

    app = Flask(__name__)

    @app.get("/")
    def classic():
        return "classic"

    app.register_blueprint(
        create_workspace_blueprint(store, dashboard, translated, guides, pairing_token="pair-me")
    )
    app.config.update(TESTING=True)
    return app, store, translated, guides


def test_blueprint_keeps_legacy_root_and_serves_dashboard(workspace):
    app, _, _, _ = workspace
    client = app.test_client()
    assert client.get("/").get_data(as_text=True) == "classic"
    assert "Workspace" in client.get("/workspace").get_data(as_text=True)
    assert "workspace = true" in client.get("/workspace-assets/app.js").get_data(as_text=True)


def test_papers_filter_pagination_patch_and_not_found(workspace):
    client = workspace[0].test_client()
    response = client.get("/api/v1/papers?q=local&collection=c1&tag=ai&status=ready&page=1&pageSize=1")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"] == "success"
    assert [paper["id"] for paper in payload["data"]] == ["p1"]
    assert payload["pagination"] == {"page": 1, "pageSize": 1, "total": 1, "totalPages": 1}

    changed = client.patch("/api/v1/papers/p1", json={"status": "reviewed", "id": "wrong"})
    assert changed.get_json()["data"] == {
        "id": "p1",
        "title": "Local Research",
        "collection_id": "c1",
        "tags": ["ai"],
        "status": "reviewed",
    }
    assert client.get("/api/v1/papers/missing").status_code == 404


def test_rejects_non_loopback_cross_origin_and_bad_json(workspace):
    client = workspace[0].test_client()
    remote = client.get("/api/v1/summary", environ_base={"REMOTE_ADDR": "192.0.2.3"})
    assert remote.status_code == 403
    assert remote.get_json()["errorType"] == "forbidden"

    origin = client.get("/api/v1/summary", headers={"Origin": "https://example.invalid"})
    assert origin.status_code == 403

    bad = client.post("/api/v1/collections", data="not json", content_type="text/plain")
    assert bad.status_code == 415
    assert bad.get_json()["status"] == "error"
    local_v6 = client.get("/api/v1/summary", environ_base={"REMOTE_ADDR": "::ffff:127.0.0.1"})
    assert local_v6.status_code == 200
    assert local_v6.headers["Cache-Control"] == "no-store"
    assert local_v6.headers["X-Content-Type-Options"] == "nosniff"


def test_chrome_extension_origin_is_limited_to_intake_routes(workspace):
    client = workspace[0].test_client()
    headers = {
        "Origin": "chrome-extension://abcdefghijklmnop",
        "X-ScholarSplit-Client": "chrome-extension",
    }
    allowed = client.post("/api/v1/library/scan", json={"commit": False}, headers=headers)
    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == headers["Origin"]
    assert client.get("/api/v1/summary", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/library/scan",
        json={"commit": False},
        headers={"Origin": headers["Origin"]},
    ).status_code == 403


def test_chrome_extension_can_read_only_its_temporary_pdf(workspace, tmp_path):
    download_dir = tmp_path / "ScholarSplit"
    download_dir.mkdir()
    pdf = download_dir / "scholarsplit-test.pdf"
    content = b"%PDF-1.7\n" + b"x" * 5000 + b"\n%%EOF"
    pdf.write_bytes(content)
    headers = {
        "Origin": "chrome-extension://abcdefghijklmnop",
        "X-ScholarSplit-Client": "chrome-extension",
    }
    client = workspace[0].test_client()
    response = client.post("/api/v1/pdf/read-local", json={"path": str(pdf)}, headers=headers)
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data == content

    wrong_name = download_dir / "paper.pdf"
    wrong_name.write_bytes(content)
    assert client.post(
        "/api/v1/pdf/read-local",
        json={"path": str(wrong_name)},
        headers=headers,
    ).status_code == 400


def test_scan_preview_and_commit_do_not_change_files(workspace):
    client = workspace[0].test_client()
    _, store, translated, guides = workspace
    before = {path: path.read_bytes() for path in [translated / "paper-one.pdf", guides / "paper-one.json"]}

    preview = client.post("/api/v1/library/scan", json={"commit": False})
    assert preview.status_code == 200
    assert preview.get_json()["data"]["matches"][0]["paperId"] == "paper-one"
    assert len(store.tables["papers"]) == 2

    committed = client.post("/api/v1/library/scan", json={"commit": True})
    assert committed.status_code == 201
    assert committed.get_json()["data"]["importedCount"] == 1
    assert client.post("/api/v1/scan/preview", json={}).status_code == 200
    assert client.post("/api/v1/scan/import", json={}).status_code == 201
    assert {path: path.read_bytes() for path in before} == before


def test_crud_and_nested_project_scope(workspace):
    client = workspace[0].test_client()
    project = client.post("/api/v1/review-projects", json={"name": "Review A"}).get_json()["data"]
    assert client.get(f"/api/v1/review-projects/{project['id']}").status_code == 200

    evidence = client.post(
        f"/api/v1/review-projects/{project['id']}/evidence",
        json={"claim": "Claim", "source": "p1"},
    ).get_json()["data"]
    listed = client.get(f"/api/v1/review-projects/{project['id']}/evidence").get_json()
    assert [row["id"] for row in listed["data"]] == [evidence["id"]]

    wrong_project = client.get(f"/api/v1/review-projects/other/evidence/{evidence['id']}")
    assert wrong_project.status_code == 404
    assert client.delete(f"/api/v1/review-projects/{project['id']}/evidence/{evidence['id']}").status_code == 200


def test_acquisition_requires_explicit_confirmation(workspace):
    client = workspace[0].test_client()
    preview = client.post("/api/v1/acquisitions/preview", json={"recommendationId": "r1"})
    assert preview.get_json()["data"]["dryRun"] is True
    assert client.post("/api/v1/acquisitions/confirm", json={"recommendationId": "r1"}).status_code == 400
    confirmed = client.post(
        "/api/v1/acquisitions/confirm", json={"recommendationId": "r1", "confirmed": True}
    )
    assert confirmed.status_code == 202
    record_id = confirmed.get_json()["data"]["id"]
    assert client.get("/api/v1/acquisitions").status_code == 200
    assert client.get(f"/api/v1/acquisitions/{record_id}").status_code == 200


def test_zotero_pairing_ack_validation_and_secret_redaction(workspace):
    client = workspace[0].test_client()
    snapshot = {"bridgeId": "b1", "items": []}
    assert client.post("/api/v1/zotero/snapshot", json=snapshot).status_code == 403
    accepted = client.post(
        "/api/v1/zotero/snapshot",
        json=snapshot,
        headers={"Authorization": "Bearer pair-me"},
    )
    assert accepted.status_code == 202
    assert accepted.get_json()["data"]["detail"]["bridgeId"] == "b1"

    secret = client.post("/api/v1/collections", json={"name": "No", "api_key": "must-not-store"})
    assert secret.status_code == 400
    assert secret.get_json()["errorType"] == "bad_request"

    invalid_ack = client.post(
        "/api/v1/zotero/commands/c1/ack",
        json={"status": "maybe"},
        headers={"Authorization": "Bearer pair-me"},
    )
    assert invalid_ack.status_code == 400
    valid_ack = client.post(
        "/api/v1/zotero/commands/c1/ack",
        json={"status": "applied"},
        headers={"Authorization": "Bearer pair-me"},
    )
    assert valid_ack.status_code == 202


def test_jobs_events_and_summary(workspace):
    app, store, _, _ = workspace
    store.insert("jobs", {"id": "j1", "status": "running"})
    client = app.test_client()
    assert client.get("/api/v1/jobs/j1").get_json()["data"]["status"] == "running"
    event_response = client.get("/api/v1/events")
    assert event_response.mimetype == "text/event-stream"
    assert 'data: {"id":"e1","type":"job.updated","jobId":"j1"}' in event_response.get_data(as_text=True)
    summary = client.get("/api/v1/summary").get_json()["data"]
    assert summary["counts"]["papers"] == 2


def test_compatibility_constructor_and_classic_helper(tmp_path: Path):
    translated = tmp_path / "translated"
    guides = tmp_path / "guides"
    translated.mkdir()
    guides.mkdir()
    app = Flask("compat")

    @app.get("/")
    def old_home():
        return "old home"

    app.register_blueprint(create_blueprint(FakeStore(), translated, guides))
    register_classic_fallback(app, "old_home")
    app.config.update(TESTING=True)
    client = app.test_client()
    assert client.get("/").get_data(as_text=True) == "old home"
    assert client.get("/classic").get_data(as_text=True) == "old home"
    with pytest.raises(ValueError):
        register_classic_fallback(app, "old_home")


def test_real_workspace_store_adapter_contract(tmp_path: Path):
    root = tmp_path / "workspace"
    dashboard = root / "dashboard"
    translated = root / "translated"
    guides = root / "reading-guides"
    for directory in (dashboard, translated, guides):
        directory.mkdir(parents=True, exist_ok=True)
    (dashboard / "index.html").write_text("workspace", encoding="utf-8")
    (translated / "study.dual.pdf").write_bytes(b"%PDF-1.4\n")
    (guides / "study.json").write_text('{"source_file":"study.pdf"}', encoding="utf-8")

    with WorkspaceStore(root / "workspace.sqlite3", workspace_root=root) as store:
        store.upsert_paper({"id": "paper-real", "title": "Real paper"})
        store.insert(
            "zotero_commands",
            {
                "id": "command-real",
                "kind": "tag.add",
                "payload": {"libraryId": 1, "tag": "theory", "itemKeys": ["AB12CD34"]},
            },
        )
        app = Flask("real-store")
        app.register_blueprint(create_workspace_blueprint(store, dashboard, translated, guides))
        app.config.update(TESTING=True)
        client = app.test_client()

        assert client.get("/api/v1/papers?q=Real").status_code == 200
        assert client.post("/api/v1/collections", json={"name": "Inbox"}).status_code == 201
        assert client.post("/api/v1/tags", json={"name": "theory"}).status_code == 201
        project = client.post("/api/v1/review-projects", json={"name": "Systematic review"})
        assert project.status_code == 201
        project_id = project.get_json()["data"]["id"]
        evidence = client.post(
            f"/api/v1/review-projects/{project_id}/evidence",
            json={"paper_id": "paper-real", "claim": "Supported"},
        )
        assert evidence.status_code == 201
        assert client.post(
            "/api/v1/acquisitions/confirm",
            json={"paperId": "paper-real", "provider": "repository", "confirmed": True},
        ).status_code == 202
        assert client.post(
            "/api/v1/zotero/snapshot", json={"bridgeId": "bridge-real", "items": []}
        ).status_code == 202
        command_batch = client.get("/api/v1/zotero/commands?bridgeId=bridge-real&after=").get_json()["data"]
        assert command_batch["commands"][0]["commandId"] == "command-real"
        assert command_batch["commands"][0]["type"] == "tag.add"
        assert client.post(
            "/api/v1/zotero/commands/command-real/ack", json={"status": "applied"}
        ).status_code == 202
        assert client.get("/api/v1/events").status_code == 200
        scan = client.post("/api/v1/library/scan", json={"commit": False}).get_json()["data"]
        assert len(scan["matches"]) == 1


def test_local_paper_can_be_queued_for_a_synced_zotero_folder(tmp_path: Path):
    root = tmp_path / "workspace"
    dashboard = root / "dashboard"
    translated = root / "translated"
    guides = root / "reading-guides"
    for directory in (dashboard, translated, guides):
        directory.mkdir(parents=True, exist_ok=True)
    (dashboard / "index.html").write_text("workspace", encoding="utf-8")
    source_pdf = translated / "local-paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n%%EOF")

    with WorkspaceStore(root / "workspace.sqlite3", workspace_root=root) as store:
        paper = store.upsert_paper(
            {"id": "local-paper", "title": "A Local Paper", "authors": ["Wang Haocheng"], "year": 2026}
        )
        store.upsert_artifact(
            {
                "paper_id": paper["id"],
                "kind": "original_pdf",
                "path": str(source_pdf),
                "mime_type": "application/pdf",
                "source": "test",
            }
        )
        folder = store.create_collection(
            {
                "id": "zotero-folder",
                "name": "Theory",
                "metadata": {
                    "source": "zotero",
                    "libraryId": 1,
                    "key": "AB12CD34",
                    "version": 4,
                    "dateModified": "2026-09-13 08:00:00",
                },
            }
        )
        app = Flask("zotero-folder-command")
        app.register_blueprint(create_workspace_blueprint(store, dashboard, translated, guides))
        app.config.update(TESTING=True)
        client = app.test_client()

        queued = client.post(
            f"/api/v1/papers/{paper['id']}/zotero-collections",
            json={"collectionId": folder["id"]},
        )
        assert queued.status_code == 202
        assert queued.get_json()["data"]["kind"] == "item.importPaper"
        command_id = queued.get_json()["data"]["id"]
        batch = client.get("/api/v1/zotero/commands?after=").get_json()["data"]
        command = next(item for item in batch["commands"] if item["commandId"] == command_id)
        assert command["type"] == "item.importPaper"
        assert command["collectionKey"] == "AB12CD34"
        assert command["attachmentPath"] == str(source_pdf)
        assert "_local" not in command

        ack = client.post(
            "/api/v1/zotero/acks",
            json={
                "schemaVersion": "1.0",
                "commandId": command_id,
                "status": "applied",
                "completedAt": "2026-09-13T08:01:00.000Z",
                "result": {"itemKey": "ZX98CV76", "attachmentKey": "QP12LM34"},
            },
        )
        assert ack.status_code == 202
        saved = store.get_paper(paper["id"])
        assert saved["metadata"]["itemKey"] == "ZX98CV76"
        assert folder["id"] in saved["collection_ids"]
