#!/bin/bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
xpi=""
output_dir="${SCHOLAR_SPLIT_DIST_DIR:-${repo_root}/dist}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --xpi) [[ $# -ge 2 ]] || { echo "--xpi 需要路径。" >&2; exit 2; }; xpi="$2"; shift 2 ;;
    --output) [[ $# -ge 2 ]] || { echo "--output 需要目录。" >&2; exit 2; }; output_dir="$2"; shift 2 ;;
    *) echo "用法：./scripts/package.sh --xpi /path/to/plugin.xpi [--output DIR]" >&2; exit 2 ;;
  esac
done
[[ -n "$xpi" && -f "$xpi" ]] || { echo "必须用 --xpi 指定已经校验的 Zotero 安装包。" >&2; exit 1; }
python3 - "$repo_root" "$output_dir" "$xpi" <<'PY'
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

root, output, xpi = map(Path, sys.argv[1:])
version = json.loads((root / "manifest.json").read_text())["version"]
with zipfile.ZipFile(xpi) as addon:
    plugin_version = json.loads(addon.read("manifest.json"))["version"]
    for entry in addon.infolist():
        if re.search(rb"sk-[A-Za-z0-9_-]{20,}", addon.read(entry)):
            raise SystemExit("XPI 发布检查发现疑似密钥；条目：" + entry.filename)
output.mkdir(parents=True, exist_ok=True)
xpi_name = f"ScholarSplit-Zotero-{plugin_version}.xpi"
archive = output / f"scholar-split-v{version}.zip"
root_files = {
    "background.js", "lib.js", "manifest.json", "sidepanel.css", "sidepanel.html", "sidepanel.js",
    "viewer.css", "viewer.html", "viewer.js", "package.json", "package-lock.json",
    "README.md", "AGENT_INSTALL.md", "LICENSE", "PRIVACY.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
    "release.json",
}
host_files = {
    "server.py", "configure.py", "requirements.txt", "requirements-next.txt", "requirements-legacy.txt",
    "index.html", "favicon.svg", "bo.mp3", "LICENSE", "README.md",
    "config/config.json.example", "config/config.toml.example", "config/venv.json.example",
}
excluded = {"node_modules", ".scaffold", "build", ".venv", "__pycache__", "data", "logs", "exports", "translated", "reading-guides", ".pytest_cache"}
text_suffixes = {".py", ".ts", ".mts", ".js", ".mjs", ".json", ".md", ".css", ".html", ".xhtml", ".toml", ".txt", ".sh", ".svg", ".ftl", ".patch"}

def allowed(name):
    path = PurePosixPath(name)
    if any(part in excluded or part.endswith("-venv") for part in path.parts):
        return False
    if path.name.startswith(".env") or path.suffix.lower() in {".pdf", ".xpi", ".zip", ".db", ".sqlite3", ".pid", ".log", ".pyc"}:
        return False
    if name in root_files:
        return True
    if name == "design/qa/library-dual-source.png":
        return True
    if name.startswith("tests/"):
        return path.suffix in {".py", ".mjs", ".json"}
    if name.startswith("icons/"):
        return path.suffix in {".png", ".svg"}
    if name.startswith("dashboard/"):
        return path.suffix in text_suffixes | {".png"}
    if name.startswith(("scripts/", "docs/")):
        return path.suffix in text_suffixes
    if name.startswith("integrations/server/host/"):
        relative = name.removeprefix("integrations/server/host/")
        return relative in host_files or (relative.startswith("utils/") and path.suffix == ".py") or (relative.startswith("tools/") and path.suffix == ".swift")
    if name.startswith("integrations/server/"):
        return len(path.parts) == 3 and (path.suffix in {".py", ".md"} or path.name == "LICENSE")
    if name.startswith("integrations/zotero/plugin/"):
        return path.suffix in text_suffixes | {".png"} or path.name in {"LICENSE", ".gitignore", ".prettierignore"}
    if name.startswith("integrations/zotero/"):
        return path.suffix in text_suffixes or path.name == "LICENSE"
    return False

tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0")
names = sorted(name for name in tracked if name and allowed(name))
required = {"scripts/install.sh", "scripts/start.sh", "scripts/stop.sh", "scripts/status.sh", "integrations/server/host/server.py", "integrations/zotero/plugin/package.json"}
missing = required.difference(names)
if missing:
    raise SystemExit("发布源文件必须先加入 Git 暂存区：" + ", ".join(sorted(missing)))
secret_pattern = re.compile(rb"sk-[A-Za-z0-9_-]{20,}")
for name in names:
    source = root / name
    if source.is_symlink():
        raise SystemExit("发布包不接受符号链接：" + name)
    if secret_pattern.search(source.read_bytes()):
        raise SystemExit("发布检查发现疑似密钥，未生成包；文件：" + name)
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
    prefix = f"scholar-split-v{version}/"
    for name in names:
        bundle.write(root / name, prefix + name)
    bundle.write(xpi, prefix + "artifacts/" + xpi_name)
    bundle.writestr(prefix + "artifacts/source-manifest.json", json.dumps({"version": version, "pluginVersion": plugin_version, "files": names}, ensure_ascii=False, indent=2) + "\n")
asset = output / xpi_name
if xpi.resolve() != asset.resolve():
    shutil.copy2(xpi, asset)
checksum = output / "SHA256SUMS"
checksum.write_text("".join(hashlib.sha256(item.read_bytes()).hexdigest() + "  " + item.name + "\n" for item in (archive, asset)))
print("已生成：" + str(archive))
print("Zotero：" + str(asset))
print("校验和：" + str(checksum))
PY
