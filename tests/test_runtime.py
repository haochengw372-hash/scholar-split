import tempfile
import unittest
from pathlib import Path

from flask import Flask

from integrations.server.runtime import initialize_workspace


class RuntimeIntegrationTest(unittest.TestCase):
    def test_runtime_mounts_workspace_without_a_legacy_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "translated").mkdir()
            (root / "reading-guides").mkdir()
            app = Flask(__name__)

            @app.get("/")
            def index():
                return "classic"

            store = initialize_workspace(app, root)
            try:
                client = app.test_client()
                self.assertEqual(client.get("/workspace").status_code, 200)
                self.assertEqual(client.get("/classic").status_code, 404)
                self.assertEqual(client.get("/api/v1/summary").status_code, 200)
                token = root / "data" / "zotero-pairing-token"
                self.assertTrue(token.is_file())
                self.assertEqual(token.stat().st_mode & 0o777, 0o600)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
