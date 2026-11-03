"""Cloud-only full-suite runner with stack evidence before the job deadline.

Uses the same unittest discovery arguments as run-tests.sh. No environment,
locals, credentials, or host-wide process information is dumped.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    output = Path(os.environ["RUNNER_TEMP"]) / "suite-diagnostics"
    output.mkdir(parents=True, exist_ok=True)
    # Ten minutes leaves five minutes of the existing 15-minute job budget
    # for cleanup and the always-run artifact upload after a stalled suite.
    child_code = """
import faulthandler
import runpy
import sys
with open(sys.argv[1], 'w', buffering=1) as stacks:
    faulthandler.enable(file=stacks)
    faulthandler.dump_traceback_later(60, repeat=True, file=stacks)
    sys.argv = ['unittest', 'discover', '-s', 'tests', '-t', 'tests', '-v']
    try:
        runpy.run_module('unittest', run_name='__main__')
    finally:
        faulthandler.cancel_dump_traceback_later()
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root / "tests")))}
    with (output / "suite.log").open("w", buffering=1) as log:
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", child_code, str(output / "stacks.log")],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            result = child.wait(timeout=600)
        except subprocess.TimeoutExpired:
            print("Suite watchdog: 600-second deadline reached; terminating suite process group.",
                  file=log, flush=True)
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            # Descendants may survive their parent; bound their cleanup too.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=10)
            result = 124
    # Keep ordinary cloud output available as well as the persisted artifact.
    with (output / "suite.log").open() as log:
        for line in log:
            print(line, end="")
    return result if result >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
