#!/usr/bin/env python3
"""Bound the CI suite and retain its output without inspecting other processes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def main() -> int:
    log = Path(os.environ["RUNNER_TEMP"]) / "test-suite-diagnostics.log"
    env = dict(os.environ, JASMINE_CI_TEST_DIAGNOSTICS="1")
    with log.open("wb") as output:
        def diagnostic(message: str) -> None:
            line = ("CI diagnostic supervisor: " + message + "\n").encode()
            output.write(line)
            output.flush()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

        diagnostic("starting full suite; watchdog deadline is 600 seconds")
        started = time.monotonic()
        child = subprocess.Popen(
            ["bash", "scripts/run-tests.sh", *sys.argv[1:]],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        diagnostic(f"suite launcher started (pid={child.pid}); stdout and stderr captured")

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
            deadline = started + 600
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(child.args, 600)
                try:
                    code = child.wait(timeout=min(60, remaining))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise
                    diagnostic(f"suite still running after {int(time.monotonic() - started)} seconds; "
                               "child thread snapshots should appear separately")
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
        diagnostic(f"suite finished: exit={code}; elapsed={int(time.monotonic() - started)} seconds; "
                   f"output relay alive={reader.is_alive()}")
        return code if code >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
