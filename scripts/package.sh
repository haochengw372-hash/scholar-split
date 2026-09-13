#!/bin/zsh
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(node -p "JSON.parse(require('fs').readFileSync('${repo_root}/manifest.json')).version")"
output_dir="${repo_root}/dist"
archive="${output_dir}/scholar-split-v${version}.zip"

mkdir -p "${output_dir}"
cd "${repo_root}"
zip -q -X "${archive}" background.js lib.js manifest.json sidepanel.css sidepanel.html sidepanel.js viewer.css viewer.html viewer.js icons/icon-*.png
echo "已生成 ${archive}"
