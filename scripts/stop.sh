#!/bin/bash
set -euo pipefail
if [[ "$(uname -s)" == Darwin ]]; then
  default_root="${HOME}/Library/Application Support/ScholarSplit"
else
  default_root="${XDG_DATA_HOME:-${HOME}/.local/share}/ScholarSplit"
fi
install_root="${SCHOLAR_SPLIT_INSTALL_ROOT:-${default_root}}"
pid_file="${install_root}/run/server.pid"
[[ -f "$pid_file" ]] || { echo "没有由此安装启动的服务。"; exit 0; }
pid="$(<"$pid_file")"
case "$pid" in ''|*[!0-9]*) echo "PID 记录无效；未停止任何进程。" >&2; exit 1;; esac
process="$(ps -p "$pid" -o args= 2>/dev/null || true)"
if [[ "$process" != *"${install_root}/server/server.py"* ]]; then
  echo "记录的进程不是此安装的服务，未停止其他进程。"
  exit 0
fi
kill -TERM "$pid"
for attempt in {1..50}; do
  process_state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
  if ! kill -0 "$pid" 2>/dev/null || [[ "$process_state" == Z* ]]; then
    rm -f "$pid_file"
    echo "ScholarSplit 已停止，文献、配置和任务数据保留。"
    exit 0
  fi
  sleep 0.2
done
echo "已请求停止，但进程尚未退出；未强制终止。" >&2
exit 1
