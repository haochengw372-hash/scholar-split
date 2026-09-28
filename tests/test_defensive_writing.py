import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import fitz

from integrations.server.defensive_writing import DefensiveWritingService, decode_pdf, review_pdf
from integrations.server.workspace_store import WorkspaceStore


class DefensiveWritingTest(unittest.TestCase):
    def _pdf(self):
        document = fitz.open()
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), "It could perhaps be argued that this result may possibly suggest a change.")
        page.insert_text((72, 105), "The sample consisted of 30 participants from one university.")
        document.set_metadata({"subject": "test corpus " * 400})
        data = document.tobytes()
        document.close()
        return data

    def test_validates_pdf(self):
        source = self._pdf()
        self.assertEqual(decode_pdf(base64.b64encode(source).decode()), source)
        with self.assertRaises(ValueError):
            decode_pdf(base64.b64encode(b"not a PDF").decode())

    def test_only_exactly_located_flags_get_pdf_rectangles(self):
        quote = "It could perhaps be argued that this result may possibly suggest a change."
        reported = []

        def fake_review(prompt, _config):
            reported.append(prompt)
            return {
                "flags": [
                    {"pageIndex": 0, "quote": quote, "reason": "限定词叠加", "suggestion": "把结果先说清楚"},
                    {"pageIndex": 0, "quote": "No such sentence is present in the PDF.", "reason": "错误", "suggestion": "不应标注"},
                ]
            }

        result = review_pdf(self._pdf(), Path("unused"), review=fake_review)
        self.assertEqual(len(reported), 1)
        self.assertIn("Do not flag a single may/might", reported[0])
        self.assertEqual(result["pageCount"], 1)
        self.assertEqual(result["unlocatedCount"], 1)
        self.assertEqual(len(result["flags"]), 1)
        flag = result["flags"][0]
        self.assertEqual(flag["quote"], quote)
        self.assertEqual(flag["position"]["pageIndex"], 0)
        self.assertTrue(flag["position"]["rects"])
        self.assertEqual(flag["pageLabel"], "1")
        self.assertRegex(flag["sortIndex"], r"^\d{5}\|\d{6}\|\d{5}$")
        self.assertGreater(flag["position"]["rects"][0][1], 700)

    def test_async_job_persists_the_result_without_the_pdf_or_model_key(self):
        source = self._pdf()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with WorkspaceStore(root / "workspace.sqlite3", root) as store:
                service = DefensiveWritingService(
                    store,
                    root,
                    reviewer=lambda _pdf, _config, on_progress: {
                        "flags": [], "pageCount": 1, "unlocatedCount": 0,
                    },
                )
                accepted = service.start(base64.b64encode(source).decode(), "paper.pdf")
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    job = store.get("jobs", accepted["id"])
                    if job["status"] == "completed":
                        break
                    sleep(.01)
                self.assertEqual(job["status"], "completed")
                self.assertEqual(job["result"]["pageCount"], 1)
                self.assertNotIn("fileContent", job["payload"])


if __name__ == "__main__":
    unittest.main()
