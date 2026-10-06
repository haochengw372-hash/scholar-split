"""Real plugin file serialization through Flask routes, without model calls."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import fitz

from test_translation_host import host  # noqa: F401


def test_file_reader_payload_reaches_chat_and_guide(host, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "A researcher investigates affect and prediction.")
        pdf.new_page().insert_text((72, 72), "本研究讨论推荐算法与意外发现。", fontname="china-s")
        source = pdf.tobytes()
    script = """
import { pdfHelper, pdfAttachment } from './integrations/zotero/plugin/tests/pdfUploadFixture.mts';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const bytes = Uint8Array.from(Buffer.from(input, 'base64'));
const file = await pdfHelper(bytes).prepareFileData(pdfAttachment);
process.stdout.write(JSON.stringify({fileName:file.fileName, fileContent:file.base64}));
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=base64.b64encode(source).decode(), capture_output=True, text=True,
        cwd=root, timeout=30, check=True,
    )
    payload = json.loads(result.stdout)
    assert base64.b64decode(payload["fileContent"], validate=True) == source
    registered = host.client.post("/api/v1/paper-chat/documents", json=payload)
    assert registered.status_code == 201, registered.get_json()
    document = registered.get_json()["data"]
    assert document["documentId"] == hashlib.sha256(source).hexdigest()
    assert document["pageCount"] == document["textPageCount"] == 2
    monkeypatch.setattr(host.module, "load_deepseek_settings", lambda _: {
        "apiKey": "synthetic-test-key", "model": "synthetic-test-model",
    })

    def guide_provider(**kwargs):
        assert Path(kwargs["source_pdf_path"]).read_bytes() == source
        return {"title": "合成导读", "oneSentence": "用于验证上传协议。"}, "synthetic-test-model", {"pageCount": 2}

    monkeypatch.setattr(host.module, "generate_reading_guide", guide_provider)
    generated = host.client.post("/guide", json={**payload, "asyncJob": False})
    assert generated.status_code == 200, generated.get_json()
    assert generated.get_json()["kind"] == "guide"
    assert generated.get_json()["extraction"]["pageCount"] == 2
