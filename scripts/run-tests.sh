#!/usr/bin/env bash
# Run the P0 test suite with the standard library only (ADR 0001 §2.1).
# Usage: scripts/run-tests.sh [unittest args...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys
if sys.version_info < (3, 11):
    sys.stderr.write("Jasmine Core tests require Python >=3.11; selected Python is %s. Set PYTHON to a supported interpreter.\n" % sys.version.split()[0])
    sys.exit(2)
'

cd "$ROOT"
if [[ "${JASMINE_CI_TEST_DIAGNOSTICS:-}" == "1" ]]; then
    PYTHONPATH="$ROOT/src:$ROOT/tests" "$PY" -u -c '
import faulthandler, runpy, sys, threading, traceback
faulthandler.enable()
# The native timer dump crashed while walking changing server-thread frames
# in cloud CI. Collect referenced Python frames from a Python thread instead.
stop = threading.Event()
stream = sys.__stderr__
def dump_stacks():
    while not stop.wait(60):
        print("CI diagnostic: Python thread snapshots (60-second interval)", file=stream)
        frames = sys._current_frames()
        try:
            for ident, frame in frames.items():
                if ident == threading.get_ident():
                    continue
                print("Thread %s:" % ident, file=stream)
                traceback.print_stack(frame, limit=64, file=stream)
        finally:
            frames.clear()
            frame = None
        stream.flush()
sampler = threading.Thread(target=dump_stacks, daemon=True)
sampler.start()
try:
    runpy.run_module("unittest", run_name="__main__")
finally:
    stop.set()
    sampler.join(timeout=1)
' discover -s tests -t tests -v "$@"
    exit $?
fi
PYTHONPATH="$ROOT/src:$ROOT/tests" "$PY" -m unittest discover -s tests -t tests -v "$@"
