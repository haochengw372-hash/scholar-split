#!/bin/zsh
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
install_root="${SCHOLAR_SPLIT_INSTALL_ROOT:-${HOME}/Library/Application Support/ScholarSplit}"
extension_dir="${install_root}/extension"
open_after_install=true

if [[ "${1:-}" == "--no-open" ]]; then
  open_after_install=false
fi

mkdir -p "${extension_dir}/icons"

for file in background.js lib.js manifest.json sidepanel.css sidepanel.html sidepanel.js viewer.css viewer.html viewer.js; do
  install -m 0644 "${repo_root}/${file}" "${extension_dir}/${file}"
done

for icon in "${repo_root}"/icons/icon-*.png; do
  install -m 0644 "${icon}" "${extension_dir}/icons/$(basename "${icon}")"
done

health="$(curl -fsS --max-time 3 http://127.0.0.1:8890/health 2>/dev/null || true)"
if [[ "${health}" == *'readingGuideV1'* && "${health}" == *'serverDeepSeekProfileV1'* ]]; then
  service_status="兼容本地服务已运行"
elif [[ -n "${health}" ]]; then
  service_status="检测到本地服务，但缺少 ScholarSplit 所需能力"
else
  service_status="未检测到兼容本地服务"
fi

echo "ScholarSplit 已准备到：${extension_dir}"
echo "服务状态：${service_status}"
echo "下一步：在 chrome://extensions 中加载上述目录。"

if [[ "${open_after_install}" == true && "$(uname -s)" == "Darwin" ]]; then
  open "${extension_dir}"
  open -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
fi
