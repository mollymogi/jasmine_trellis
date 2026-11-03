#!/usr/bin/env python3
"""Bound the CI suite and retain its output without inspecting other processes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading


def main() -> int:
    log = Path(os.environ["RUNNER_TEMP"]) / "test-suite-diagnostics.log"
    env = dict(os.environ, JASMINE_CI_TEST_DIAGNOSTICS="1")
    with log.open("wb") as output:
        child = subprocess.Popen(
            ["bash", "scripts/run-tests.sh", *sys.argv[1:]],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def relay() -> None:
            assert child.stdout is not None
            for line in iter(child.stdout.readline, b""):
                output.write(line)
                output.flush()
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()

        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        try:
            # Leave five minutes of the existing 15-minute job for cleanup and
            # artifact upload. Thread dumps are emitted every minute by Python.
            code = child.wait(timeout=600)
        except subprocess.TimeoutExpired:
            message = b"\nCI diagnostic watchdog: suite exceeded 600 seconds; terminating suite process group.\n"
            output.write(message)
            output.flush()
            sys.stdout.buffer.write(message)
            sys.stdout.buffer.flush()
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            code = 124
        finally:
            # Also stop descendants that survived their parent, so inherited
            # output pipes cannot prevent the diagnostic step from completing.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)
            reader.join(timeout=5)
        return code if code >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
