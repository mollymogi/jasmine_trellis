#!/usr/bin/env bash
# Point Tailscale Serve at the loopback memory gateway. Default is --dry-run.
#
# --apply is explicit. It is the only path that runs:
#   tailscale serve --bg --https=443 http://127.0.0.1:18788
# CI and tests call --dry-run only. This script does not change Core's bind
# and does not enroll clients. Windows is not enrolled by this apply.
#
# Before --apply the script checks:
#   - 127.0.0.1:18788 is listening (printed for --dry-run, not required then)
#   - the hub Core environment has JASMINE_TAILNET_CLIENTS set to a non-empty
#     value (JASMINE_CORE_ENV_FILE, or the environ of the process listening
#     on 127.0.0.1:18787). The script's own environment does not count.
#   - `tailscale serve status` has no Funnel and does not already point at
#     18787, :15180, or :18084. "No serve config" passes. The backend does
#     not already have to be 18788.
#   - tailnet_clients has at least one active Linux client. There is no
#     platform column: an active row counts as Linux when its display_name
#     does not indicate Windows ("windows" anywhere, or a "WIN-" prefix).
#     An active Windows name fails the run.
#
# After --apply, status is read again. If the backend is not 127.0.0.1:18788
# or Funnel appears, the script runs `tailscale serve off` and exits non-zero.
#
# Rollback: `tailscale serve off` and stop the gateway. Do not roll back a migrated Core binary.
set -euo pipefail

MODE="dry-run"
DB="${JASMINE_TAILNET_SERVE_DB:-}"
SEEN_MODE=0

usage() {
  sed -n '2,25p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      if [[ "$SEEN_MODE" -eq 1 ]]; then
        echo "pass only one of --dry-run or --apply" >&2
        exit 2
      fi
      MODE="dry-run"
      SEEN_MODE=1
      ;;
    --apply)
      if [[ "$SEEN_MODE" -eq 1 ]]; then
        echo "pass only one of --dry-run or --apply" >&2
        exit 2
      fi
      MODE="apply"
      SEEN_MODE=1
      ;;
    --db)
      DB="${2:-}"
      if [[ -z "$DB" ]]; then
        echo "--db requires a path" >&2
        exit 2
      fi
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

listener_up() {
  if [[ -n "${JASMINE_SERVE_LISTENER_CHECK:-}" ]]; then
    "$JASMINE_SERVE_LISTENER_CHECK"
    return
  fi
  python3 -c 'import socket, sys
s = socket.socket()
s.settimeout(0.4)
try:
    s.connect(("127.0.0.1", 18788))
except OSError:
    sys.exit(1)
finally:
    s.close()'
}

core_env_text() {
  if [[ -n "${JASMINE_CORE_ENV_FILE:-}" ]]; then
    cat -- "$JASMINE_CORE_ENV_FILE"
    return
  fi
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -nP -iTCP:18787 -sTCP:LISTEN -t 2>/dev/null || true)"
  fi
  local pid text=""
  for pid in $pids; do
    if [[ "$(uname -s)" == "Darwin" ]]; then
      text+=$'\n'"$(ps -wwE -p "$pid" -o command= 2>/dev/null || true)"
    else
      text+=$'\n'"$(ps eww -p "$pid" 2>/dev/null || true)"
    fi
  done
  printf '%s' "$text"
}

write_serve_status() {
  set +e
  tailscale serve status >"$TMP/status" 2>&1
  printf '%s' "$?" >"$TMP/status_rc"
  set -e
}

classify() {
  # $1 phase, $2 listener, $3 status path, $4 env path, $5 status rc
  python3 - "$1" "$2" "$DB" "$MODE" "$3" "$4" "$5" <<'PY'
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

phase, listener, db_path, mode, status_path, env_path, status_rc = sys.argv[1:8]
status = Path(status_path).read_text(encoding="utf-8")
env_text = Path(env_path).read_text(encoding="utf-8") if phase == "pre" else ""
try:
    status_failed = int(status_rc) != 0
except ValueError:
    status_failed = True

forbidden = re.compile(r"(^|[^0-9])(18787|15180|18084)([^0-9]|$)")
backend = re.compile(r"(^|[^0-9])127\.0\.0\.1:18788([^0-9]|$)")


def flag_set(text: str) -> bool:
    """A real assignment only. The name on a command line is not the environ."""
    for token in text.split():
        if not token.startswith("JASMINE_TAILNET_CLIENTS="):
            continue
        value = token.split("=", 1)[1].strip().strip("\"'")
        if value:
            return True
    for match in re.finditer(r"JASMINE_TAILNET_CLIENTS\s*=>\s*(\S+)", text):
        if match.group(1).strip().strip("\"'"):
            return True
    return False


def indicates_windows(name: str) -> bool:
    folded = name.casefold()
    return "windows" in folded or folded.startswith("win-")


def status_failed_read(text: str) -> bool:
    return status_failed and text.strip().lower() != "no serve config"


def status_blocked(text: str) -> list[str]:
    reasons = []
    # A failed read is not "funnel off". Empty output from a failed command
    # stays a failure even though the word funnel is absent.
    if status_failed_read(text):
        reasons.append("tailscale serve status failed")
        return reasons
    if not text.strip():
        reasons.append("tailscale serve status was empty")
        return reasons
    if "funnel" in text.lower():
        reasons.append("tailscale serve status has Funnel")
    if forbidden.search(text):
        reasons.append("tailscale serve status points at 18787, :15180, or :18084")
    return reasons


if phase == "post":
    # Exit 2: status command failed, caller must not turn Serve off.
    # Exit 1: status shows Funnel or a backend other than 127.0.0.1:18788.
    if status_failed_read(status):
        print("tailscale serve status failed", file=sys.stderr)
        sys.exit(2)
    reasons = status_blocked(status)
    if not backend.search(status):
        reasons.append("tailscale serve backend is not 127.0.0.1:18788")
    if reasons:
        for reason in reasons:
            print(reason, file=sys.stderr)
        sys.exit(1)
    print("backend: 127.0.0.1:18788")
    print("funnel: off")
    sys.exit(0)

errors = []
if listener == "up":
    print("listener: 127.0.0.1:18788 listening")
elif mode == "apply":
    errors.append("127.0.0.1:18788 is not listening")
else:
    print("listener: 127.0.0.1:18788 not listening (not required for dry-run)")

if flag_set(env_text):
    print("core_env: JASMINE_TAILNET_CLIENTS set")
else:
    errors.append("hub Core environment does not have JASMINE_TAILNET_CLIENTS set")

blocked = status_blocked(status)
if blocked:
    errors.extend(blocked)
else:
    print("serve_status: no Funnel and no forbidden backend")

if not db_path:
    errors.append("hub database is required (--db) to read tailnet_clients")
else:
    database = Path(db_path)
    if not database.is_file():
        errors.append("hub database does not exist")
    else:
        uri = "file:" + quote(str(database), safe="/") + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            try:
                rows = conn.execute(
                    "SELECT host_id, display_name FROM tailnet_clients"
                    " WHERE status = 'active' ORDER BY host_id"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            rows = None
            errors.append("could not read tailnet_clients")
        if rows is not None:
            windows = [(host_id, name) for host_id, name in rows if indicates_windows(name)]
            linux = [(host_id, name) for host_id, name in rows if not indicates_windows(name)]
            if windows:
                names = ", ".join(name for _, name in windows)
                errors.append(f"this apply does not enroll Windows ({names})")
            elif not linux:
                errors.append("no active Linux client in tailnet_clients")
            else:
                shown = ", ".join(f"{name} ({host_id})" for host_id, name in linux)
                print(f"linux_client: {shown}")
                print("windows: not enrolled")

if errors:
    for reason in errors:
        print(reason, file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PY
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

LISTENER="down"
if listener_up; then
  LISTENER="up"
fi
write_serve_status
core_env_text >"$TMP/env"
STATUS_RC="$(cat "$TMP/status_rc")"

classify pre "$LISTENER" "$TMP/status" "$TMP/env" "$STATUS_RC" && PRE_RC=0 || PRE_RC=$?
if [[ "$PRE_RC" -ne 0 ]]; then
  exit 1
fi

printf 'mode: %s\n' "$MODE"
if [[ "$MODE" == "dry-run" ]]; then
  printf 'dry-run: not applying\n'
  printf 'would_run: tailscale serve --bg --https=443 http://127.0.0.1:18788\n'
  exit 0
fi

if ! tailscale serve --bg --https=443 http://127.0.0.1:18788; then
  echo "tailscale serve failed" >&2
  exit 1
fi
write_serve_status
STATUS_RC="$(cat "$TMP/status_rc")"
classify post up "$TMP/status" "$TMP/env" "$STATUS_RC" && POST_RC=0 || POST_RC=$?
if [[ "$POST_RC" -eq 1 ]]; then
  tailscale serve off || true
  echo "backend was not 127.0.0.1:18788 or Funnel appeared; ran tailscale serve off" >&2
  exit 1
fi
if [[ "$POST_RC" -ne 0 ]]; then
  exit "$POST_RC"
fi
printf 'applied: tailscale serve --bg --https=443 http://127.0.0.1:18788\n'
exit 0
