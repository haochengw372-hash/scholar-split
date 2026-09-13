import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.server.research_service import ResearchService
from integrations.server.workspace_store import WorkspaceStore


class ResearchServiceTest(unittest.TestCase):
    def test_builds_evidence_from_linked_guide_and_persists_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide_path = root / "guide.json"
            guide_path.write_text(json.dumps({"guide": {"findings": [{"claim": "Trust fell.", "evidence": "b=-.2", "page": "p. 4"}], "method": {"analysis": "OLS", "sample": "n=100"}}}), encoding="utf-8")
            with WorkspaceStore(root / "db.sqlite3", root) as store:
                store.add_paper({"id": "p", "title": "Paper"})
                store.add_artifact({"paper_id": "p", "kind": "reading_guide", "path": str(guide_path)})
                store.create_review_project({"id": "r", "name": "Review"})
                store.insert("review_members", {"project_id": "r", "paper_id": "p"})
                rows = ResearchService(store, root).build_project_evidence("r")
                self.assertEqual(rows[0]["location"], "p. 4")
                self.assertEqual(store.list("evidence")[0]["claim"], "Trust fell.")

    def test_discovery_maps_openalex_and_never_downloads(self):
        payload = {"results": [{"display_name": "Suggested Paper", "publication_year": 2025, "cited_by_count": 10, "doi": "https://doi.org/10.1/x", "authorships": [], "primary_location": {"landing_page_url": "https://example.org/p"}, "best_oa_location": {"pdf_url": "https://example.org/p.pdf"}}]}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with WorkspaceStore(root / "db.sqlite3", root) as store:
                store.create_review_project({"id": "r", "name": "Review"})
                with patch("urllib.request.urlopen", return_value=response):
                    rows = ResearchService(store, root).discover("r", "AI trust")
                self.assertEqual(rows[0]["title"], "Suggested Paper")
                self.assertEqual(store.list("acquisitions"), [])


if __name__ == "__main__":
    unittest.main()
