#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" == Darwin ]]; then
  default_root="${HOME}/Library/Application Support/ScholarSplit"
else
  default_root="${XDG_DATA_HOME:-${HOME}/.local/share}/ScholarSplit"
fi
install_root="${SCHOLAR_SPLIT_INSTALL_ROOT:-${default_root}}"
open_after_install=true
start_after_install=true
skip_deps=false
legacy=false
skip_xpi=false
xpi="${SCHOLAR_SPLIT_XPI:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-open) open_after_install=false ;;
    --no-start) start_after_install=false ;;
    --skip-deps) skip_deps=true ;;
    --legacy) legacy=true ;;
    --skip-xpi) skip_xpi=true ;;
    --xpi) [[ $# -ge 2 ]] || { echo "--xpi 需要路径。" >&2; exit 2; }; xpi="$2"; shift ;;
    -h|--help)
      echo "用法：./scripts/install.sh [--no-open] [--no-start] [--legacy] [--xpi FILE] [--skip-deps] [--skip-xpi]"
      echo "默认安装独立服务与 PDF2zh-next；--legacy 另装经典引擎。"
      echo "--skip-deps / --skip-xpi 是显式跳过选项，不代表完整服务或 Zotero 插件已安装。"
      exit 0 ;;
    *) echo "未知选项：$1" >&2; exit 2 ;;
  esac
  shift
done

if [[ "$skip_xpi" == false && -z "$xpi" ]]; then
  for candidate in "${repo_root}"/artifacts/ScholarSplit-Zotero-*.xpi "${repo_root}"/integrations/zotero/plugin/build/*.xpi "${repo_root}"/integrations/zotero/plugin/.scaffold/build/*.xpi; do
    [[ ! -f "$candidate" ]] || xpi="$candidate"
  done
fi
if [[ "$skip_xpi" == false && ( -z "$xpi" || ! -f "$xpi" ) ]]; then
  echo "未找到 Zotero XPI。请使用完整发布 ZIP，或先运行：" >&2
  echo "(cd integrations/zotero/plugin && npm ci --ignore-scripts --no-audit --no-fund && npm test && npm run build)" >&2
  echo "也可用 --xpi /path/to/ScholarSplit-Zotero.xpi 指定已构建安装包。" >&2
  exit 1
fi

if [[ "$skip_deps" == false ]]; then
  command -v uv >/dev/null || {
    echo "需要 uv 和 Python 3.12。请先按 https://docs.astral.sh/uv/getting-started/installation/ 安装 uv。" >&2
    exit 1
  }
  python_runtime="$(uv python find 3.12 2>/dev/null)" || {
    echo "未找到 Python 3.12。请运行 uv python install 3.12，然后重试。" >&2
    exit 1
  }
else
  command -v python3 >/dev/null || { echo "需要 Python 3 才能复制安装文件。" >&2; exit 1; }
  python_runtime="$(command -v python3)"
fi

"$python_runtime" - "$repo_root" "$install_root" "$xpi" <<'PY'
import json
import shutil
import sys
import zipfile
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
xpi = Path(sys.argv[3]) if sys.argv[3] else None
host = source / "integrations/server/host"
if not (host / "server.py").is_file():
    raise SystemExit("安装包缺少完整翻译后端，请使用完整 ScholarSplit 发布包。")
target.mkdir(parents=True, exist_ok=True)
extension = target / "extension"
extension.mkdir(exist_ok=True)
for name in ("background.js", "lib.js", "manifest.json", "sidepanel.css", "sidepanel.html", "sidepanel.js", "viewer.css", "viewer.html", "viewer.js"):
    shutil.copy2(source / name, extension / name)
shutil.copytree(source / "icons", extension / "icons", dirs_exist_ok=True)
server = target / "server"
server.mkdir(exist_ok=True)
for item in host.iterdir():
    if item.is_file() and (item.suffix == ".py" or item.name in {"LICENSE", "index.html", "favicon.svg", "bo.mp3", "requirements.txt", "requirements-next.txt", "requirements-legacy.txt"}):
        shutil.copy2(item, server / item.name)
(server / "utils").mkdir(exist_ok=True)
for item in (host / "utils").glob("*.py"):
    shutil.copy2(item, server / "utils" / item.name)
(server / "tools").mkdir(exist_ok=True)
for item in (host / "tools").glob("*.swift"):
    shutil.copy2(item, server / "tools" / item.name)
(server / "config").mkdir(exist_ok=True)
for item in (host / "config").glob("*.example"):
    shutil.copy2(item, server / "config" / item.name)
integration = server / "scholarsplit"
integration.mkdir(exist_ok=True)
for item in (source / "integrations/server").glob("*.py"):
    shutil.copy2(item, integration / item.name)
shutil.copytree(source / "dashboard", integration / "dashboard", dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests", "node_modules"))
(target / "scripts").mkdir(exist_ok=True)
for name in ("start.sh", "stop.sh", "status.sh"):
    destination = target / "scripts" / name
    shutil.copy2(source / "scripts" / name, destination)
    destination.chmod(0o755)
if xpi:
    with zipfile.ZipFile(xpi) as addon:
        version = json.loads(addon.read("manifest.json"))["version"]
    if "/" in version or "\\" in version:
        raise SystemExit("XPI 版本格式无效。")
    (target / "zotero").mkdir(exist_ok=True)
    shutil.copy2(xpi, target / "zotero" / f"ScholarSplit-Zotero-{version}.xpi")
for name in ("README.md", "AGENT_INSTALL.md", "LICENSE", "NOTICE", "CITATION.cff", "THIRD_PARTY_NOTICES.md"):
    if (source / name).is_file():
        shutil.copy2(source / name, target / name)
PY

if [[ "$skip_deps" == false ]]; then
  server_root="${install_root}/server"
  for environment in .venv zotero-pdf2zh-next-venv; do
    [[ -x "${server_root}/${environment}/bin/python" ]] || uv venv --python "$python_runtime" "${server_root}/${environment}"
  done
  uv pip install --python "${server_root}/.venv/bin/python" -r "${server_root}/requirements.txt"
  uv pip install --python "${server_root}/zotero-pdf2zh-next-venv/bin/python" -r "${server_root}/requirements-next.txt"
  if [[ "$legacy" == true ]]; then
    [[ -x "${server_root}/zotero-pdf2zh-venv/bin/python" ]] || uv venv --python "$python_runtime" "${server_root}/zotero-pdf2zh-venv"
    uv pip install --python "${server_root}/zotero-pdf2zh-venv/bin/python" -r "${server_root}/requirements-legacy.txt"
  fi
fi

echo "ScholarSplit 已安装到：${install_root}"
echo "已有配置、配对令牌、文献与任务数据保持不变。"
echo "Chrome 扩展目录：${install_root}/extension"
if [[ "$skip_xpi" == true ]]; then
  echo "已显式跳过 Zotero 安装包；Chrome 和服务源文件已复制。"
else
  echo "Zotero 安装包目录：${install_root}/zotero；请在 Zotero 中从文件安装。"
fi
if [[ "$skip_deps" == true ]]; then
  echo "已跳过依赖安装；未验证翻译引擎可用性。"
fi
if [[ "$start_after_install" == true ]]; then
  SCHOLAR_SPLIT_INSTALL_ROOT="$install_root" "${install_root}/scripts/start.sh"
fi
if [[ "$open_after_install" == true && "$(uname -s)" == Darwin ]]; then
  open "${install_root}/extension"
  open -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
fi
