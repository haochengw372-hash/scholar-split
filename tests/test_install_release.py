"""Exercise install and release scripts without downloading translation engines."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import zipfile

import pytest


REPO = Path(__file__).resolve().parents[1]


def invoke(script, *args, root, **environment):
    env = dict(os.environ, SCHOLAR_SPLIT_INSTALL_ROOT=str(root), **environment)
    return subprocess.run(
        ["bash", str(REPO / "scripts" / script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_install_contains_backend_and_preserves_private_runtime(tmp_path):
    target = tmp_path / "Application Support" / "ScholarSplit"
    first = invoke("install.sh", "--skip-deps", "--skip-xpi", "--no-start", "--no-open", root=target)
    assert first.returncode == 0, first.stderr
    assert (target / "extension/manifest.json").is_file()
    assert (target / "server/server.py").is_file()
    assert (target / "server/scholarsplit/paper_chat.py").is_file()
    assert (target / "server/scholarsplit/dashboard/app.js").is_file()
    assert (target / "server/config/config.toml.example").is_file()
    assert not (target / "server/config/config.toml").exists()
    assert os.access(target / "scripts/start.sh", os.X_OK)
    retained = {
        "server/config/config.toml": "private profile fixture",
        "server/data/zotero-pairing-token": "fixture pairing token",
        "server/data/scholarsplit.sqlite3": "fixture research data",
        "server/reading-guides/existing.json": "fixture guide",
    }
    for name, text in retained.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    second = invoke("install.sh", "--no-open", "--skip-deps", "--skip-xpi", "--no-start", root=target)
    assert second.returncode == 0, second.stderr
    for name, text in retained.items():
        assert (target / name).read_text() == text


def test_dependencies_are_installed_in_separate_environments(tmp_path):
    target = tmp_path / "Scholar Split"
    tools = tmp_path / "tools"
    tools.mkdir()
    record = tmp_path / "uv-invocations.jsonl"
    uv = tools / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['UV_TEST_RECORD'], 'a') as log: log.write(json.dumps(args) + '\\n')\n"
        f"if args[:2] == ['python', 'find']: print({sys.executable!r})\n"
        "elif args[0] == 'venv':\n"
        "    folder = pathlib.Path(args[-1]) / 'bin'; folder.mkdir(parents=True, exist_ok=True)\n"
        "    (folder / 'python').symlink_to(sys.executable)\n"
    )
    uv.chmod(0o755)
    result = invoke(
        "install.sh", "--no-start", "--no-open", "--skip-xpi", "--legacy", root=target,
        PATH=str(tools) + os.pathsep + os.environ["PATH"], UV_TEST_RECORD=str(record),
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    installs = [call for call in calls if call[:2] == ["pip", "install"]]
    assert len(installs) == 3
    paths = [Path(call[call.index("--python") + 1]) for call in installs]
    assert {path.parent.parent.name for path in paths} == {
        ".venv", "zotero-pdf2zh-next-venv", "zotero-pdf2zh-venv",
    }
    rerun = invoke(
        "install.sh", "--no-start", "--no-open", "--skip-xpi", "--legacy", root=target,
        PATH=str(tools) + os.pathsep + os.environ["PATH"], UV_TEST_RECORD=str(record),
    )
    assert rerun.returncode == 0, rerun.stderr
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert sum(call[0] == "venv" for call in calls) == 3


def test_start_does_not_take_over_an_existing_listener(tmp_path):
    target = tmp_path / "Scholar Split"
    for name in (".venv", "zotero-pdf2zh-next-venv"):
        folder = target / "server" / name / "bin"
        folder.mkdir(parents=True)
        (folder / "python").symlink_to(sys.executable)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = str(listener.getsockname()[1])
        result = invoke("start.sh", root=target, SCHOLAR_SPLIT_PORT=port)
        assert result.returncode != 0
        assert "已被其他服务占用" in result.stderr
        assert listener.fileno() >= 0
        assert not (target / "run/server.pid").exists()
        assert not (target / "run/start.lock").exists()


def test_stop_never_kills_a_pid_owned_by_another_program(tmp_path):
    target = tmp_path / "ScholarSplit"
    (target / "run").mkdir(parents=True)
    (target / "run/server.pid").write_text(str(os.getpid()))
    result = invoke("stop.sh", root=target)
    assert result.returncode == 0
    assert "未停止其他进程" in result.stdout
    os.kill(os.getpid(), 0)


def test_owned_lifecycle_with_a_local_http_fixture(tmp_path):
    target = tmp_path / "Scholar Split"
    for name in (".venv", "zotero-pdf2zh-next-venv"):
        folder = target / "server" / name / "bin"
        folder.mkdir(parents=True)
        (folder / "python").symlink_to(sys.executable)
    server = target / "server/server.py"
    server.write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "import argparse\n"
        "parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int)\n"
        "args, rest = parser.parse_known_args()\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')\n"
        "    def log_message(self, *args): pass\n"
        "HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()\n"
    )
    retained = target / "server/config/config.toml"
    retained.parent.mkdir()
    retained.write_text("retained profile fixture")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = str(listener.getsockname()[1])
    try:
        started = invoke("start.sh", root=target, SCHOLAR_SPLIT_PORT=port)
        assert started.returncode == 0, started.stderr
        pid = (target / "run/server.pid").read_text()
        second = invoke("start.sh", root=target, SCHOLAR_SPLIT_PORT=port)
        assert second.returncode == 0
        assert "已运行" in second.stdout
        assert (target / "run/server.pid").read_text() == pid
        status = invoke("status.sh", root=target, SCHOLAR_SPLIT_PORT=port)
        assert status.returncode == 0
        assert "此安装的服务进程运行中" in status.stdout
        stopped = invoke("stop.sh", root=target)
        assert stopped.returncode == 0, stopped.stderr
        assert not (target / "run/server.pid").exists()
        assert retained.read_text() == "retained profile fixture"
        status = invoke("status.sh", root=target, SCHOLAR_SPLIT_PORT=port)
        assert status.returncode != 0
    finally:
        invoke("stop.sh", root=target)


def test_skip_dependencies_does_not_claim_a_working_service(tmp_path):
    result = invoke("install.sh", "--skip-deps", "--skip-xpi", "--no-open", root=tmp_path / "target")
    assert result.returncode != 0
    assert "未验证翻译引擎可用性" in result.stdout
    assert "尚未安装服务依赖" in result.stderr


def test_source_install_requires_explicit_xpi_or_skip(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts/install.sh", source / "scripts/install.sh")
    target = tmp_path / "target"
    result = subprocess.run(
        ["bash", str(source / "scripts/install.sh"), "--skip-deps", "--no-start", "--no-open"],
        env=dict(os.environ, SCHOLAR_SPLIT_INSTALL_ROOT=str(target)),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "未找到 Zotero XPI" in result.stderr
    assert "npm run build" in result.stderr
    assert not target.exists()


def test_install_copies_the_explicit_built_zotero_package(tmp_path):
    xpi = tmp_path / "release.xpi"
    with zipfile.ZipFile(xpi, "w") as addon:
        addon.writestr("manifest.json", json.dumps({"version": "4.1.8-guide.13"}))
    target = tmp_path / "target"
    result = invoke("install.sh", "--skip-deps", "--no-start", "--no-open", "--xpi", str(xpi), root=target)
    assert result.returncode == 0, result.stderr
    installed = target / "zotero/ScholarSplit-Zotero-4.1.8-guide.13.xpi"
    assert installed.read_bytes() == xpi.read_bytes()


def package_fixture(tmp_path):
    root = tmp_path / "source repository"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts/package.sh", root / "scripts/package.sh")
    for name in (
        "scripts/install.sh", "scripts/start.sh", "scripts/stop.sh", "scripts/status.sh",
        "integrations/server/host/server.py", "integrations/server/host/utils/config.py",
        "integrations/zotero/plugin/package.json", "integrations/zotero/plugin/src/index.ts",
        "integrations/server/host/config/config.toml.example", "README.md",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source fixture\n")
    (root / "manifest.json").write_text(json.dumps({"version": "0.3.0"}))
    private = (
        "integrations/server/host/config/config.toml",
        "integrations/server/host/config/config.json",
        "integrations/server/host/.venv/bin/python",
        "integrations/zotero/plugin/node_modules/private.js",
        "integrations/server/data/notes.json",
        "integrations/server/document.pdf",
        "integrations/zotero/plugin/old.xpi",
        "integrations/server/host/logs/server.log",
    )
    for name in private:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private fixture\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    xpi = tmp_path / "built.xpi"
    with zipfile.ZipFile(xpi, "w") as addon:
        addon.writestr("manifest.json", json.dumps({"version": "4.1.8-guide.13"}))
        addon.writestr("bootstrap.js", "// plugin fixture")
    return root, xpi, private


def test_release_manifest_excludes_private_files_and_replaces_old_zip(tmp_path):
    root, xpi, private = package_fixture(tmp_path)
    output = tmp_path / "release assets"
    output.mkdir()
    archive = output / "scholar-split-v0.3.0.zip"
    with zipfile.ZipFile(archive, "w") as old:
        old.writestr("stale-private.pdf", "do not retain")
    result = subprocess.run(
        ["bash", str(root / "scripts/package.sh"), "--xpi", str(xpi), "--output", str(output)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        prefix = "scholar-split-v0.3.0/"
        assert prefix + "integrations/server/host/server.py" in names
        assert prefix + "scripts/install.sh" in names
        assert prefix + "integrations/zotero/plugin/src/index.ts" in names
        assert prefix + "artifacts/ScholarSplit-Zotero-4.1.8-guide.13.xpi" in names
        assert all(prefix + name not in names for name in private)
        assert "stale-private.pdf" not in names
    for row in (output / "SHA256SUMS").read_text().splitlines():
        digest, name = row.split("  ", 1)
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_release_rejects_an_accidentally_committed_api_key(tmp_path):
    root, xpi, _ = package_fixture(tmp_path)
    (root / "README.md").write_text("sk-" + "dummy" * 8)
    result = subprocess.run(
        ["bash", str(root / "scripts/package.sh"), "--xpi", str(xpi)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "疑似密钥" in result.stderr
    assert "dummy" not in result.stderr
    assert not (root / "dist/scholar-split-v0.3.0.zip").exists()


@pytest.mark.parametrize("script", ["install.sh", "start.sh", "stop.sh", "status.sh", "package.sh"])
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(REPO / "scripts" / script)], check=True)
