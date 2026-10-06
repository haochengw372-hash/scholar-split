#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" == Darwin ]]; then
  default_root="${HOME}/Library/Application Support/ScholarSplit"
else
  default_root="${XDG_DATA_HOME:-${HOME}/.local/share}/ScholarSplit"
fi
install_root="${SCHOLAR_SPLIT_INSTALL_ROOT:-${default_root}}"
server_root="${install_root}/server"
port="${SCHOLAR_SPLIT_PORT:-8890}"
case "$port" in ''|*[!0-9]*) echo "端口必须为数字。" >&2; exit 2;; esac
[[ "$port" -ge 1 && "$port" -le 65535 ]] || { echo "端口必须在 1–65535。" >&2; exit 2; }
python="${server_root}/.venv/bin/python"
[[ -x "$python" ]] || { echo "尚未安装服务依赖，请重新运行 install.sh（不要使用 --skip-deps）。" >&2; exit 1; }
[[ -x "${server_root}/zotero-pdf2zh-next-venv/bin/python" ]] || { echo "尚未安装 PDF2zh-next 引擎，请重新运行 install.sh。" >&2; exit 1; }
mkdir -p "${install_root}/run" "${install_root}/logs"
pid_file="${install_root}/run/server.pid"
if [[ -f "$pid_file" ]]; then
  pid="$(<"$pid_file")"
  case "$pid" in ''|*[!0-9]*) ;; *)
    process="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    if [[ "$process" == *"${server_root}/server.py"* ]]; then
      echo "ScholarSplit 已运行（PID ${pid}）。"
      exit 0
    fi ;;
  esac
fi
lock_dir="${install_root}/run/start.lock"
mkdir "$lock_dir" 2>/dev/null || { echo "另一个启动操作正在进行，请稍后重试。" >&2; exit 1; }
trap 'rmdir "$lock_dir"' EXIT
"$python" - "$port" <<'PY'
import socket
import sys
with socket.socket() as listener:
    try:
        listener.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit("该回环端口已被其他服务占用。未停止现有服务；请设置 SCHOLAR_SPLIT_PORT 使用其他端口。")
PY
log_file="${install_root}/logs/server.log"
chmod 700 "${install_root}/run" "${install_root}/logs"
touch "$log_file"
chmod 600 "$log_file"
nohup "$python" -u "${server_root}/server.py" \
  --host 127.0.0.1 --port "$port" --enable_venv true --env_tool uv \
  --skip_install true --check_update false --enable_mirror false \
  >"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"
chmod 600 "$pid_file"
for attempt in {1..100}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "服务启动失败。请查看本机日志：${log_file}" >&2
    exit 1
  fi
  if curl -fsS --max-time 1 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 && \
     curl -fsS --max-time 1 "http://127.0.0.1:${port}/api/v1/summary" >/dev/null 2>&1; then
    echo "ScholarSplit 已启动：http://127.0.0.1:${port}/workspace（PID ${pid}）"
    exit 0
  fi
  sleep 0.2
done
echo "服务进程仍在启动，尚未确认工作台可用。请运行 status.sh；日志：${log_file}" >&2
exit 1
