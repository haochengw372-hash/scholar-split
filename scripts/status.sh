#!/bin/bash
set -euo pipefail
if [[ "$(uname -s)" == Darwin ]]; then
  default_root="${HOME}/Library/Application Support/ScholarSplit"
else
  default_root="${XDG_DATA_HOME:-${HOME}/.local/share}/ScholarSplit"
fi
install_root="${SCHOLAR_SPLIT_INSTALL_ROOT:-${default_root}}"
port="${SCHOLAR_SPLIT_PORT:-8890}"
pid_file="${install_root}/run/server.pid"
owned=false
if [[ -f "$pid_file" ]]; then
  pid="$(<"$pid_file")"
  case "$pid" in ''|*[!0-9]*) ;; *)
    process="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    [[ "$process" != *"${install_root}/server/server.py"* ]] || owned=true ;;
  esac
fi
if [[ "$owned" == true ]]; then
  echo "此安装的服务进程运行中（PID ${pid}）。"
else
  echo "此安装的服务未运行。"
fi
if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 && \
   curl -fsS --max-time 3 "http://127.0.0.1:${port}/api/v1/summary" >/dev/null 2>&1; then
  echo "端口 ${port} 的服务及工作台可访问：http://127.0.0.1:${port}/workspace"
  [[ "$owned" == true ]] || echo "该端口可能是已有安装的服务；本安装不会管理或停止它。"
else
  echo "端口 ${port} 的完整工作台尚不可访问。"
  exit 1
fi
