"""Exercise the redistributable host without a model account or local library."""

import base64
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import sys
from types import SimpleNamespace

import fitz
import pytest


REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path, monkeypatch):
    # Host imports create guide/data folders. Run the actual installation layout
    # in a disposable directory instead of importing it inside the source tree.
    root = tmp_path / "installation with spaces" / "server"
    source = REPOSITORY / "integrations" / "server"
    shutil.copytree(source / "host", root, ignore=shutil.ignore_patterns("__pycache__"))
    package = root / "scholarsplit"
    package.mkdir()
    for path in source.glob("*.py"):
        shutil.copy2(path, package / path.name)
    shutil.copytree(REPOSITORY / "dashboard", package / "dashboard")
    (root / "translated").mkdir()

    prefixes = ("utils", "scholarsplit")
    previous = {
        name: module for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)
    }
    for name in previous:
        sys.modules.pop(name)
    monkeypatch.syspath_prepend(str(root))
    name = "scholarsplit_test_translation_host"
    spec = importlib.util.spec_from_file_location(name, root / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    translator = None
    try:
        spec.loader.exec_module(module)
        module.args = SimpleNamespace(
            enable_venv=False, env_tool="uv", enable_mirror=False,
            mirror_source="", skip_install=True, enable_winexe=False, port=8890,
        )
        module.prepare_path()
        translator = module.PDFTranslator(module.args)
        translator.app.testing = True
        yield SimpleNamespace(module=module, translator=translator, root=root,
                              client=translator.app.test_client())
    finally:
        if translator is not None and translator.workspace_store is not None:
            translator.workspace_store.close()
        sys.modules.pop(name, None)
        for imported in list(sys.modules):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in prefixes):
                sys.modules.pop(imported)
        sys.modules.update(previous)


def synthetic_pdf():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "A synthetic translation smoke test. Thirty participants completed the task.")
    result = document.tobytes()
    document.close()
    return result


def test_complete_host_mounts_workspace_and_reports_public_health(host):
    health = host.client.get("/health")
    assert health.status_code == 200
    payload = health.get_json()
    assert payload["status"] == "ok"
    assert payload["version"] == host.module.__version__
    assert payload["workspaceReady"] is True
    assert "readingGuideV1" in payload["capabilities"]
    assert "serverDeepSeekProfileV1" in payload["capabilities"]
    assert "apiKey" not in json.dumps(payload)
    assert "deepseek_api_key" not in json.dumps(payload)

    assert host.client.get("/").headers["Location"] == "/workspace"
    workspace = host.client.get("/workspace")
    assert workspace.status_code == 200
    assert "ScholarSplit" in workspace.get_data(as_text=True)
    summary = host.client.get("/api/v1/summary")
    assert summary.status_code == 200
    assert summary.get_json()["data"]["counts"]["papers"] == 0
    token = host.root / "data" / "zotero-pairing-token"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert token.read_text().strip() not in health.get_data(as_text=True)


def test_pdf2zh_next_translation_command_and_download_round_trip(host, monkeypatch):
    commands = []
    source = synthetic_pdf()

    def execute(command, task_id, args, environment):
        commands.append(command)
        assert environment is None
        assert task_id
        input_path = Path(command[1])
        assert input_path.read_bytes() == source
        output = Path(command[command.index("--output") + 1])
        target = output / f"{input_path.stem}.no_watermark.zh-CN.mono.pdf"
        shutil.copy2(input_path, target)

    monkeypatch.setattr(host.module, "execute_with_progress", execute)
    request = {
        "fileName": "synthetic paper.pdf",
        "fileContent": base64.b64encode(source).decode(),
        "engine": "pdf2zh_next", "next_service": "bing",
        "sourceLang": "en", "targetLang": "zh-CN", "qps": 2,
        "mono": True, "dual": False, "noDual": True,
        "noWatermark": True, "asyncJob": False,
    }
    response = host.client.post("/translate", json=request)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["status"] == "success"
    assert len(result["fileList"]) == 1
    assert len(commands) == 1
    command = commands[0]
    assert command[0] == "pdf2zh_next"
    assert "--bing" in command
    assert command[command.index("--qps") + 1] == "2"
    assert command[command.index("--config-file") + 1] == host.module.config_path["pdf2zh_next"]
    assert command[command.index("--watermark-output-mode") + 1] == "no_watermark"
    assert "--no-dual" in command
    assert "--service" not in command

    download = host.client.get("/translatedFile/" + result["fileList"][0])
    assert download.status_code == 200
    document = fitz.open(stream=download.data, filetype="pdf")
    assert len(document) == 1
    assert "Thirty participants" in document[0].get_text()
    document.close()
    assert host.client.get("/api/history").get_json()["history"][0]["fileList"] == result["fileList"]


def test_missing_legacy_environment_never_uses_next_alias(host, monkeypatch):
    from utils.venv import VirtualEnvManager

    manager = VirtualEnvManager(
        host.module.config_path["venv"], host.module.venv_name,
        "uv", enable_mirror=False, skip_install=True,
    )
    selected = []
    next_environment = host.root / "zotero-pdf2zh-next-venv"

    def existing(engine, tool):
        selected.append(engine)
        if engine == "pdf2zh_next":
            return tool, next_environment, next_environment / "bin" / "python"
        return None

    monkeypatch.setattr(manager, "_existing", existing)
    with pytest.raises(RuntimeError, match="pdf2zh 托管翻译环境"):
        manager.get_command_and_env(["pdf2zh", "paper.pdf", "--service", "bing"])
    assert selected == ["pdf2zh"]
