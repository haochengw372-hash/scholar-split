#!/usr/bin/env python3
"""Opt-in real-engine smoke test. Uses the local saved profile, never reads keys."""
import argparse
import base64
import json
import time
import urllib.request
import uuid
import fitz

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8890")
parser.add_argument("--engine-python", help="Test a fresh engine environment directly")
parser.add_argument("--config-file", help="Existing private engine configuration; never copied or printed")
args = parser.parse_args()

def get(path):
    with urllib.request.urlopen(args.url + path, timeout=30) as response:
        return json.load(response)

pdf = fitz.open()
page = pdf.new_page()
page.insert_text((60, 70), "ScholarSplit engine verification", fontsize=16)
page.insert_textbox(fitz.Rect(60, 100, 520, 650), (
    "This synthetic document tests a local academic translation workflow. "
    "Researchers compare two groups receiving recommendations from a computer system. "
    "The first group sees a single source, while the second group sees multiple sources. "
    "The research question asks whether perceived source diversity changes trust in recommendations. "
    "Participants answer a questionnaire after reading the same message. "
    "The analysis reports the difference between groups and its uncertainty. "
    "The findings are restricted to this sample and task, rather than claiming a universal effect. "
    "No real participants or private research materials are included in this test."
), fontsize=11)
content = pdf.tobytes()
pdf.close()
if args.engine_python:
    import subprocess
    import tempfile
    from pathlib import Path
    if not args.config_file:
        raise SystemExit("--engine-python requires --config-file")
    with tempfile.TemporaryDirectory(prefix="scholarsplit-engine-smoke-") as directory:
        source = Path(directory) / "synthetic.pdf"
        source.write_bytes(content)
        executable = str(Path(args.engine_python).with_name("pdf2zh_next"))
        command = [executable, str(source),
                   "--deepseek", "--qps", "1", "--output", directory,
                   "--lang-in", "en", "--lang-out", "zh-CN",
                   "--config-file", args.config_file, "--no-dual",
                   "--no-auto-extract-glossary", "--watermark-output-mode", "no_watermark"]
        print("Testing fresh PDF2zh-next environment with synthetic PDF", flush=True)
        result = subprocess.run(command, capture_output=True, timeout=600)
        if result.returncode:
            raise SystemExit(f"Engine exited with code {result.returncode}; provider diagnostics not printed")
        outputs = list(Path(directory).glob("*.mono.pdf"))
        assert len(outputs) == 1, "Expected one translated PDF"
        translated = fitz.open(outputs[0])
        assert translated.page_count == 1
        assert any("\u4e00" <= character <= "\u9fff" for page in translated for character in page.get_text())
        print(json.dumps({"verified": True, "freshEngine": True, "pages": 1, "containsChinese": True}))
    raise SystemExit(0)
body = json.dumps({
    "fileName": f"scholarsplit-synthetic-{uuid.uuid4().hex[:12]}.pdf",
    "fileContent": base64.b64encode(content).decode(),
    "engine": "pdf2zh_next", "service": "deepseek", "next_service": "deepseek",
    "useServerDeepSeekConfig": True, "asyncJob": True,
    "sourceLang": "en", "targetLang": "zh-CN", "qps": 1,
    "mono": True, "dual": False, "noDual": True, "noWatermark": True,
    "disableGlossary": True,
}).encode()
request = urllib.request.Request(args.url + "/translate", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=30) as response:
    accepted = json.load(response)
task_id = accepted["taskId"]
print(f"Accepted synthetic translation task {task_id}", flush=True)
deadline = time.monotonic() + 600
last_status = ""
while time.monotonic() < deadline:
    response = get("/api/tasks")
    tasks = response if isinstance(response, list) else response.get("tasks", response.get("data", []))
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    task = next((row for row in tasks if row.get("taskId") == task_id), None)
    if task is None:
        history = get("/api/history")
        history = history if isinstance(history, list) else history.get("history", [])
        task = next((row for row in history if row.get("taskId") == task_id), None)
    if task:
        status = str(task.get("status"))
        if status != last_status:
            print(f"Status: {status}", flush=True)
            last_status = status
        if task.get("finished"):
            files = task.get("fileList", [])
            if not files:
                raise SystemExit("Translation failed; inspect the local service task (no diagnostic body printed).")
            from urllib.parse import quote
            with urllib.request.urlopen(args.url + "/translatedFile/" + quote(files[0]), timeout=30) as response:
                translated = fitz.open(stream=response.read(), filetype="pdf")
            text = "".join(page.get_text() for page in translated)
            assert translated.page_count == 1
            assert any("\u4e00" <= character <= "\u9fff" for character in text), "No Chinese text in translated PDF"
            print(json.dumps({"verified": True, "engine": "pdf2zh-next", "pages": translated.page_count, "containsChinese": True}))
            break
    time.sleep(2)
else:
    raise SystemExit("Translation timeout")
