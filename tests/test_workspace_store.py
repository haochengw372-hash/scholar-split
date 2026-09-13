import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from integrations.server.workspace_store import (
    CURRENT_SCHEMA_VERSION,
    WorkspaceStore,
    backup_database,
    canonicalize_filename,
    scan_workspace,
)


class WorkspaceStoreSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "workspace.sqlite3"
        self.store = WorkspaceStore(self.db_path, self.root)

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def test_schema_pragmas_entities_and_idempotent_migration(self):
        self.assertEqual(self.store.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(self.store.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.store.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        tables = {
            row[0]
            for row in self.store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        expected = {
            "papers",
            "artifacts",
            "collections",
            "collection_papers",
            "tags",
            "paper_tags",
            "notes",
            "reading_state",
            "review_projects",
            "review_members",
            "screening_decisions",
            "evidence",
            "syntheses",
            "gaps",
            "recommendations",
            "acquisitions",
            "jobs",
            "zotero_commands",
            "sync_audit",
            "paper_fts",
        }
        self.assertTrue(expected.issubset(tables), expected - tables)
        self.assertEqual(self.store.migrate(), CURRENT_SCHEMA_VERSION)
        self.assertEqual(
            self.store.connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1
        )

    def test_paper_json_round_trip_full_text_search_and_cascade(self):
        paper = self.store.upsert_paper(
            {
                "id": "paper-1",
                "title": "Human Machine Communication",
                "authors": ["A. Researcher", "王浩成"],
                "abstract": "A study of social responses to artificial agents.",
                "year": 2026,
                "metadata": {"verified": True, "score": 3},
            }
        )
        self.assertEqual(paper["authors"], ["A. Researcher", "王浩成"])
        self.assertEqual(paper["metadata"], {"verified": True, "score": 3})
        artifact = self.store.upsert_artifact(
            {"paper_id": paper["id"], "kind": "translated_pdf", "path": "translated/Paper.compare.pdf"}
        )
        self.assertEqual(artifact["canonical_name"], "paper")
        self.assertEqual([row["id"] for row in self.store.list_papers(search="artificial")], ["paper-1"])

        self.store.upsert_paper({**paper, "abstract": "Updated evidence about news automation."})
        self.assertEqual(self.store.list_papers(search="artificial"), [])
        self.assertEqual([row["id"] for row in self.store.list_papers(search="automation")], ["paper-1"])
        self.assertTrue(self.store.delete("papers", paper["id"]))
        self.assertIsNone(self.store.get_paper(paper["id"]))
        self.assertEqual(self.store.list_artifacts(), [])

    def test_review_workflow_jobs_and_json_fields(self):
        paper = self.store.add_paper({"id": "p", "title": "Review candidate"})
        project = self.store.create_review_project(
            {"id": "r", "name": "HMC review", "protocol": {"stages": ["title", "full_text"]}}
        )
        self.store.insert(
            "review_members",
            {"project_id": project["id"], "paper_id": paper["id"], "metadata": {"origin": "seed"}},
        )
        self.store.insert(
            "screening_decisions",
            {
                "project_id": project["id"],
                "paper_id": paper["id"],
                "stage": "title",
                "decision": "include",
                "criteria": {"topic": True},
            },
        )
        evidence = self.store.insert(
            "evidence",
            {
                "project_id": project["id"],
                "paper_id": paper["id"],
                "claim": "Disclosure changes trust.",
                "codes": ["trust", "disclosure"],
            },
        )
        self.store.insert(
            "syntheses",
            {"project_id": project["id"], "title": "Trust", "source_ids": [evidence["id"]]},
        )
        self.store.insert(
            "gaps",
            {
                "project_id": project["id"],
                "title": "Longitudinal evidence",
                "evidence_ids": [evidence["id"]],
            },
        )
        recommendation = self.store.insert(
            "recommendations",
            {
                "project_id": project["id"],
                "title": "Collect panels",
                "priority": 7,
                "evidence_ids": [evidence["id"]],
            },
        )
        self.store.insert(
            "acquisitions",
            {"paper_id": paper["id"], "provider": "open_access", "requested_at": "2026-01-01T00:00:00Z"},
        )
        job = self.store.create_job(
            {"id": "job-1", "kind": "guide", "payload": {"paper_id": paper["id"]}}
        )
        completed = self.store.update_job(
            job["id"], {"status": "complete", "progress": 100, "result": {"guide": "ready"}}
        )
        self.store.insert("zotero_commands", {"kind": "attach", "payload": {"paper": paper["id"]}})
        self.store.insert(
            "sync_audit",
            {
                "direction": "outbound",
                "entity_type": "paper",
                "entity_id": paper["id"],
                "action": "upsert",
                "status": "ok",
                "detail": {"changed": ["title"]},
            },
        )
        self.assertEqual(completed["result"], {"guide": "ready"})
        self.assertEqual(self.store.list_jobs(status="complete")[0]["id"], "job-1")
        self.assertEqual(
            self.store.list_recommendations(project_id=project["id"])[0]["id"], recommendation["id"]
        )

    def test_collection_tag_and_reading_filters(self):
        self.store.add_paper({"id": "included", "title": "Included Paper"})
        self.store.add_paper({"id": "excluded", "title": "Excluded Paper"})
        collection = self.store.create_collection({"id": "collection", "name": "Core"})
        second_collection = self.store.create_collection({"id": "secondary", "name": "Secondary"})
        tag = self.store.create_tag({"id": "tag", "name": "HMC"})
        second_tag = self.store.create_tag({"id": "tag-method", "name": "Experiment"})
        self.store.add_paper_to_collection(collection["id"], "included")
        self.store.add_paper_to_collection(second_collection["id"], "included", position=2)
        self.store.tag_paper("included", tag["id"])
        self.store.tag_paper("included", second_tag["id"])
        self.store.set_reading_state("included", status="reading", progress=0.5)
        filtered = self.store.list_papers(
            search="Included", collection_id="collection", tag="hmc", status="reading"
        )
        self.assertEqual([row["id"] for row in filtered], ["included"])
        self.assertEqual(filtered[0]["collection_ids"], ["collection", "secondary"])
        self.assertEqual(filtered[0]["tags"], ["Experiment", "HMC"])
        self.assertEqual(filtered[0]["tag_ids"], ["tag-method", "tag"])
        self.assertEqual(filtered[0]["status"], "reading")
        self.assertEqual(filtered[0]["reading_progress"], 0.5)
        self.assertEqual([row["id"] for row in self.store.list_papers(tag="hmc")], ["included"])
        self.assertEqual([row["id"] for row in self.store.list_papers(status="reading")], ["included"])
        excluded = self.store.get_paper("excluded")
        self.assertEqual(excluded["collection_ids"], [])
        self.assertEqual(excluded["tags"], [])
        self.assertEqual(excluded["status"], "unread")

    def test_reading_state_foreign_keys_and_transactions(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.set_reading_state("missing", progress=0.1)
        self.store.add_paper({"id": "p", "title": "Read me"})
        state = self.store.set_reading_state("p", progress=0.25, page=4, position={"y": 120})
        self.assertEqual(state["position"], {"y": 120})
        self.assertEqual(self.store.get_reading_state("p")["page"], 4)
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as connection:
                connection.execute(
                    "INSERT INTO tags(id, name, created_at, updated_at) VALUES('rollback', 'temporary', 'x', 'x')"
                )
                raise RuntimeError("abort")
        self.assertEqual(self.store.list("tags"), [])

    def test_live_backup_contains_committed_wal_data(self):
        self.store.add_paper({"id": "persisted", "title": "Persisted in backup"})
        destination = self.store.backup(self.root / "backups" / "workspace.bak")
        self.assertTrue(destination.is_file())
        with sqlite3.connect(destination) as connection:
            self.assertEqual(
                connection.execute("SELECT title FROM papers WHERE id='persisted'").fetchone()[0],
                "Persisted in backup",
            )
        second = backup_database(destination, self.root / "copied.bak")
        self.assertTrue(second.is_file())


class WorkspaceScannerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "translated").mkdir()
        (self.root / "reading-guides").mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_filename_canonicalization_is_safe_and_conservative(self):
        self.assertEqual(canonicalize_filename("../A%20Paper.en.LR_dual.pdf?download=1"), "a paper")
        self.assertEqual(canonicalize_filename(r"C:\\unsafe\\Study.no_watermark.compare.PDF"), "study")
        self.assertNotEqual(canonicalize_filename("paper-one.pdf"), canonicalize_filename("paper two.pdf"))

    def test_scanner_exact_matches_ambiguity_invalid_json_and_no_mutation(self):
        files = {
            self.root / "translated" / "Exact Paper.compare.pdf": b"exact-pdf",
            self.root / "translated" / "Duplicate.pdf": b"first",
            self.root / "translated" / "Duplicate.mono.pdf": b"second",
            self.root / "translated" / "Explicit Target.pdf": b"third",
            self.root / "reading-guides" / "Exact Paper.json": json.dumps({"findings": []}).encode(),
            self.root / "reading-guides" / "Duplicate.json": json.dumps({"title": "duplicate"}).encode(),
            self.root / "reading-guides" / "Different Guide.json": json.dumps(
                {"sourceFile": "Explicit Target.pdf"}
            ).encode(),
            self.root / "reading-guides" / "Broken.json": b"{not-json",
        }
        for path, content in files.items():
            path.write_bytes(content)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}

        report = scan_workspace(self.root)

        self.assertEqual(len(report["translated_pdfs"]), 4)
        matches = {(Path(row["guide"]).name, Path(row["pdf"]).name) for row in report["matches"]}
        self.assertEqual(
            matches,
            {("Exact Paper.json", "Exact Paper.compare.pdf"), ("Different Guide.json", "Explicit Target.pdf")},
        )
        self.assertEqual(Path(report["ambiguous"][0]["guide"]).name, "Duplicate.json")
        self.assertEqual(len(report["ambiguous"][0]["candidates"]), 2)
        broken = next(row for row in report["unmatched"] if Path(row["guide"]).name == "Broken.json")
        self.assertEqual(broken["reason"], "invalid_json")
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
        self.assertEqual(before, after)

    def test_explicit_import_is_idempotent_and_preserves_sources(self):
        pdf = self.root / "translated" / "Stable.compare.pdf"
        guide = self.root / "reading-guides" / "Stable.json"
        pdf.write_bytes(b"pdf-content")
        guide.write_text("{}", encoding="utf-8")
        before = {pdf: pdf.read_bytes(), guide: guide.read_bytes()}
        db = self.root / "workspace.sqlite3"
        with WorkspaceStore(db, self.root) as store:
            scan = store.scan_migration_sources()
            first = store.import_scan(scan)
            second = store.import_workspace_scan(scan)
            self.assertEqual(first["importedCount"], 1)
            self.assertEqual(second["importedCount"], 1)
            self.assertEqual(len(store.list_papers()), 1)
            self.assertEqual(len(store.list_artifacts()), 2)
            self.assertEqual(store.list_papers()[0]["source_type"], "legacy_translation")
        self.assertEqual({pdf: pdf.read_bytes(), guide: guide.read_bytes()}, before)

    def test_missing_source_directories_return_empty_report(self):
        empty = self.root / "empty"
        empty.mkdir()
        report = scan_workspace(empty)
        self.assertEqual(report["translated_pdfs"], [])
        self.assertEqual(report["reading_guides"], [])
        self.assertEqual(report["matches"], [])

    def test_import_indexes_every_pdf_group_even_without_a_guide(self):
        original = self.root / "translated" / "Author - 2026 - Paper Without Guide.pdf"
        translated = self.root / "translated" / "Author - 2026 - Paper Without Guide.compare.pdf"
        original.write_bytes(b"%PDF-1.7\noriginal\n%%EOF")
        translated.write_bytes(b"%PDF-1.7\ntranslated\n%%EOF")

        with WorkspaceStore(self.root / "workspace.sqlite3", self.root) as store:
            report = store.scan_migration_sources()
            result = store.import_scan(report)
            papers = store.list_papers()
            artifacts = store.list_artifacts(paper_id=papers[0]["id"])

        self.assertEqual(result["importedCount"], 1)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "Paper Without Guide")
        self.assertEqual(papers[0]["authors"], ["Author"])
        self.assertEqual(papers[0]["year"], 2026)
        self.assertEqual({item["kind"] for item in artifacts}, {"original_pdf", "translated_pdf"})
        self.assertTrue(all(item["checksum"] for item in artifacts))

    def test_chrome_translation_is_the_only_workspace_default_scope(self):
        chrome_pdf = self.root / "translated" / "chrome-a1b2c3-paper.pdf"
        legacy_pdf = self.root / "translated" / "Older Zotero Translation.pdf"
        chrome_pdf.write_bytes(b"%PDF-1.7\nchrome\n%%EOF")
        legacy_pdf.write_bytes(b"%PDF-1.7\nlegacy\n%%EOF")

        with WorkspaceStore(self.root / "workspace.sqlite3", self.root) as store:
            store.import_scan(store.scan_migration_sources())
            scholar_split = store.list_papers(scope="scholarsplit")
            archived = store.list_papers(scope="legacy")

        self.assertEqual([paper["source_type"] for paper in scholar_split], ["chrome_extension"])
        self.assertEqual([paper["source_type"] for paper in archived], ["legacy_translation"])

    def test_legacy_guide_title_can_match_one_unique_pdf_group(self):
        original = self.root / "translated" / "Smith - 2024 - Human Machine Communication.pdf"
        original.write_bytes(b"%PDF-1.7\ncontent\n%%EOF")
        guide = self.root / "reading-guides" / "opaque-id.json"
        guide.write_text(
            json.dumps({"guide": {"originalTitle": "Human Machine Communication"}}),
            encoding="utf-8",
        )

        report = scan_workspace(self.root)

        self.assertEqual(len(report["matches"]), 1)
        self.assertEqual(report["matches"][0]["match_type"], "title_exact_unique")
        self.assertEqual(Path(report["matches"][0]["pdf"]).name, original.name)


class ZoteroSnapshotTests(unittest.TestCase):
    def test_snapshot_upserts_metadata_collections_tags_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with WorkspaceStore(root / "workspace.sqlite3", root) as store:
                result = store.ingest_zotero_snapshot(
                    {
                        "bridgeId": "zotero-main",
                        "libraryId": 1,
                        "items": [
                            {
                                "key": "ABCD1234",
                                "version": 7,
                                "dateModified": "2026-09-13T00:00:00Z",
                                "title": "Synced Paper",
                                "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
                                "year": 2026,
                                "DOI": "10.1000/example",
                                "abstractNote": "Evidence abstract",
                                "collections": ["COLL0001"],
                                "tags": ["HMC"],
                                "attachments": [],
                            }
                        ],
                        "collections": [{"key": "COLL0001", "name": "Core", "version": 2}],
                    }
                )
                paper = store.list_papers()[0]
                collections = store.list("collections")
                tags = store.list("tags")
                audit = store.list("sync_audit")

        self.assertEqual(result["papersUpserted"], 1)
        self.assertEqual(paper["source_key"], "1:ABCD1234")
        self.assertEqual(paper["doi"], "10.1000/example")
        self.assertEqual(collections[0]["name"], "Core")
        self.assertEqual(tags[0]["name"], "HMC")
        self.assertEqual(audit[0]["direction"], "inbound")


if __name__ == "__main__":
    unittest.main()
