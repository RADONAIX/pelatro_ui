#!/usr/bin/env bash
# Start the API detached from the calling shell, so closing the terminal does
# not take the server down with it.
#
#   ./start.sh          start in the background, log to logs/api.log
#   ./start.sh -f       run in the foreground (Ctrl-C to stop)
#   ./start.sh stop     stop a background instance
#   ./start.sh status   show whether it is listening
#
# HOST/PORT come from .env (or the environment, which wins) — change the port in
# one place and run.py, uvicorn and this script all follow.
set -euo pipefail

cd "$(dirname "$0")"

# Read HOST/PORT out of .env without executing it: only KEY=VALUE lines are
# considered, so a stray command in the file can never run here.
if [[ -f .env ]]; then
  while IFS='=' read -r key value; do
    key="${key// /}"
    value="${value%\"}"; value="${value#\"}"
    case "$key" in
      HOST) [[ -z "${HOST:-}" ]] && HOST="$value" ;;
      PORT) [[ -z "${PORT:-}" ]] && PORT="$value" ;;
    esac
  done < <(grep -E '^[A-Z_]+=' .env || true)
fi

PY="${PY:-.venv/bin/python}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8001}"
# Per-port, so running a second instance on another port cannot
# orphan the first one's pid file.
PIDFILE=".uvicorn-${PORT}.pid"
LOGFILE="logs/api.log"

listening() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }

case "${1:-}" in
  stop)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
      echo "stopped"
    elif pkill -f "uvicorn app.main:app" 2>/dev/null; then
      echo "stopped"
    else
      echo "not running"
    fi
    exit 0
    ;;
  status)
    if listening; then
      echo "listening on port $PORT"
      lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | tail -n +2
    else
      echo "not listening on port $PORT"
      exit 1
    fi
    exit 0
    ;;
esac

if [[ ! -x "$PY" ]]; then
  echo "No virtualenv at $PY — run:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Fail early and clearly rather than deep inside a uvicorn traceback.
if ! "$PY" -c "import fastapi, sqlalchemy, psycopg2" 2>/dev/null; then
  echo "$PY is missing dependencies — run:" >&2
  echo "  $PY -m pip install -r requirements.txt" >&2
  exit 1
fi

if [[ "${1:-}" == "-f" ]]; then
  echo "Starting on http://${HOST}:${PORT} (foreground, --reload)"
  # Always `python -m uvicorn`: a bare `uvicorn` can resolve to the system
  # Python's script, which has no dependencies installed.
  exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
fi

if listening; then
  echo "Port $PORT is already serving — nothing to do. (./start.sh stop to replace it)"
  exit 0
fi

mkdir -p logs
# start_new_session puts the server in its own process group and session, so a
# signal aimed at this shell's group (which is what kills a plain `cmd &`)
# never reaches it.
"$PY" -c "
import subprocess
log = open('$LOGFILE', 'ab')
p = subprocess.Popen(
    ['$PY', '-m', 'uvicorn', 'app.main:app', '--host', '$HOST', '--port', '$PORT'],
    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    start_new_session=True,
)
open('$PIDFILE', 'w').write(str(p.pid))
" >/dev/null

for _ in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "API listening on http://127.0.0.1:${PORT} (pid $(cat "$PIDFILE")) — logs: $LOGFILE"
    exit 0
  fi
  sleep 0.5
done

echo "Failed to start; last lines of $LOGFILE:" >&2
tail -20 "$LOGFILE" >&2
exit 1
