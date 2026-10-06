#!/usr/bin/env python3
"""Refresh reviewed upstream source only; never copy a user's runtime state."""
import argparse
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("upstream", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = args.upstream.resolve()
host = root / "integrations/server/host"
plugin = root / "integrations/zotero/plugin"

def copy(relative, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / relative, destination)

for name in ("server.py", "index.html", "favicon.svg", "bo.mp3"):
    copy("server/" + name, host / name)
for name in ("auto_update", "config", "config_map", "config_migration", "cropper", "deepseek_thinking", "environment_lifecycle", "execute", "package_network", "reading_guide", "task_manager", "venv"):
    copy(f"server/utils/{name}.py", host / "utils" / f"{name}.py")
for name in ("config.json.example", "config.toml.example", "venv.json.example"):
    copy("server/config/" + name, host / "config" / name)
copy("server/tools/ocr_pdf_text.swift", host / "tools/ocr_pdf_text.swift")
for directory in ("src", "addon", "typings", "tests"):
    for path in sorted((source / "plugin" / directory).rglob("*")):
        if path.is_file() and path.suffix not in {".pdf", ".xpi"}:
            copy(path.relative_to(source), plugin / path.relative_to(source / "plugin"))
for name in ("package.json", "package-lock.json", "tsconfig.json", "zotero-plugin.config.ts", "eslint.config.mjs", ".gitignore", ".prettierignore"):
    copy("plugin/" + name, plugin / name)
for destination in (host / "LICENSE", plugin / "LICENSE"):
    copy("LICENSE", destination)
revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
print(f"Vendored reviewed host/plugin source from upstream revision {revision}; local ScholarSplit modifications included.")
