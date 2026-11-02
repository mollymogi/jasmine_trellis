"""`jasmine-core digest`: long output in, what matters out, the rest on disk (ADR 0038)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from support import SRC  # noqa: F401  (path setup)

from jasmine_core import canonical, cli  # noqa: E402
from jasmine_core import digest as digest_module  # noqa: E402


def _section(text: str, title: str) -> str:
    """The lines of one `-- title` section of a rendered digest."""
    lines = text.split("\n-- ")
    return next(part for part in lines if part.startswith(title))


def _test_log(passing: int = 3000) -> str:
    lines = ["Ran setup on host-7"]
    for i in range(passing):
        # A passing test whose docstring talks about failure is not an error.
        lines.append(f"test_{i} (suite.Case.test_{i})\nA failed install leaves nothing behind. ... ok")
    lines += [
        "ERROR: test_retain (suite.Memory.test_retain)",
        "Traceback (most recent call last):",
        '  File "worker.py", line 42, in tick',
        "    self.retain(job)",
        "ValueError: engine_extracted_no_units",
    ]
    lines += [f"2026-11-02T10:00:{i % 60:02d} worker: connection refused, retrying in {i}s" for i in range(40)]
    lines += ["-" * 70, f"Ran {passing + 1} tests in 12.3s", "", "FAILED (errors=1)"]
    return "\n".join(lines) + "\n"


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-digest-")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "digests"

    def _digest(self, text: str | bytes, **kwargs):
        data = text.encode() if isinstance(text, str) else text
        return digest_module.digest(data, out_dir=self.out, **kwargs)

    def test_short_input_is_printed_whole_and_not_kept(self) -> None:
        result = self._digest("all 12 tests passed\n")
        self.assertTrue(result.passthrough)
        self.assertEqual(result.text, "all 12 tests passed\n")
        self.assertIsNone(result.raw_path)
        self.assertFalse(self.out.exists())

    def test_a_long_test_log_keeps_the_verdict_the_error_and_the_traceback(self) -> None:
        log = _test_log()
        result = self._digest(log, name="tests.log")
        self.assertEqual(result.kind, "log")
        self.assertFalse(result.passthrough)
        self.assertLessEqual(len(result.text), digest_module.DEFAULT_BUDGET)
        self.assertLess(len(result.text), len(log) // 20)
        self.assertIn("FAILED (errors=1)", result.text)
        self.assertIn("Ran 3001 tests", result.text)
        self.assertIn("ValueError: engine_extracted_no_units", result.text)
        # Forty retries are one distinct line with a count, not forty lines.
        self.assertIn("×40", result.text)
        self.assertEqual(_section(result.text, "errors").count("connection refused"), 1)

    def test_passing_tests_that_mention_failure_are_not_errors(self) -> None:
        result = self._digest(_test_log())
        self.assertNotIn("A failed install", _section(result.text, "errors"))
        header = next(line for line in result.text.splitlines() if line.startswith("errors:"))
        # ERROR, the exception line, 40 retries and the FAILED verdict.
        self.assertTrue(header.startswith("errors: 43 line(s), 4 distinct"), header)

    def test_the_raw_input_is_kept_whole_and_private(self) -> None:
        log = _test_log()
        result = self._digest(log)
        raw = Path(result.raw_path)
        self.assertEqual(raw.read_text(), log)
        self.assertEqual(stat.S_IMODE(raw.stat().st_mode), 0o600)
        self.assertIn(str(raw), result.text)
        # Named by content: the same input is kept once.
        again = self._digest(log)
        self.assertEqual(again.raw_path, result.raw_path)
        self.assertEqual(len(list(self.out.iterdir())), 1)

    def test_the_same_input_gives_the_same_digest(self) -> None:
        log = _test_log()
        self.assertEqual(self._digest(log).text, self._digest(log).text)

    def test_a_tail_keeps_the_last_lines(self) -> None:
        result = self._digest(_test_log(), budget=1200)
        self.assertTrue(result.text.rstrip().endswith("FAILED (errors=1)"), result.text[-300:])

    def test_credentials_are_masked_in_the_digest_but_kept_in_the_raw_file(self) -> None:
        token = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
        log = _test_log().replace("Ran setup on host-7", f"ERROR: upstream rejected {token}")
        result = self._digest(log)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", result.text)
        self.assertIn(canonical.REDACTED, result.text)
        self.assertIn(token, Path(result.raw_path).read_text())

    def test_multiline_credentials_are_masked_before_sections_and_clipping(self) -> None:
        material = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
        credentials = [
            "-----BEGIN PRIVATE KEY-----\n" + (material + "\n") * 150
            + "-----END PRIVATE KEY-----",
            'password="\n' + (material + "\n") * 150 + '"',
        ]
        for credential in credentials:
            for kind in ("text", "log", "markdown"):
                with self.subTest(kind=kind, credential=credential[:30]):
                    text = credential + "\n\n# After credential\nERROR: public sentinel\n"
                    result = self._digest(text, kind=kind)
                    self.assertFalse(result.passthrough)
                    self.assertNotIn(material[:16], result.text)
                    self.assertIn(canonical.REDACTED, result.text)
                    self.assertEqual(Path(result.raw_path).read_text(), text)
                    self.assertEqual(result.lines, len(text.splitlines()))
                    if kind == "log":
                        number = text.splitlines().index("ERROR: public sentinel") + 1
                        self.assertIn(f"L{number}  ERROR: public sentinel", result.text)
                    elif kind == "markdown":
                        number = text.splitlines().index("# After credential") + 1
                        self.assertIn(f"L{number} # After credential", result.text)

    def test_line_preserving_redaction_keeps_boundaries_at_eof(self) -> None:
        for separator in ("\n", "\r\n"):
            text = separator.join(["-----BEGIN PRIVATE KEY-----", "QUJDREVGR0g=",
                                   "-----END PRIVATE KEY-----"])
            masked = canonical.redact_text(text, preserve_lines=True)
            self.assertEqual(len(masked.splitlines()), len(text.splitlines()))
            self.assertEqual(masked.count(separator), text.count(separator))
            self.assertNotIn("QUJDREVGR0g=", masked)

    def test_a_document_becomes_an_outline_with_line_numbers(self) -> None:
        body = "Filler sentence about nothing in particular. " * 6
        doc = ["# Trellis", "", "Truth store for agent sessions. More text follows.", ""]
        for n in range(1, 31):
            doc += [f"## Section {n}", "", f"Section {n} explains part {n}. {body}", ""]
        doc += ["```bash", "# not a heading, a shell comment", "```"]
        result = self._digest("\n".join(doc), name="design.md")
        self.assertEqual(result.kind, "markdown")
        self.assertIn("L1 # Trellis — Truth store for agent sessions.", result.text)
        self.assertIn("L5   ## Section 1 — Section 1 explains part 1.", result.text)
        self.assertNotIn("not a heading", result.text)
        self.assertIn("code blocks: 1", result.text)

    def test_markdown_is_detected_without_an_extension(self) -> None:
        text = "\n".join(["# A", "x " * 3000, "## B", "y"])
        self.assertEqual(digest_module.detect_kind(text), "markdown")

    def test_json_is_described_by_its_shape(self) -> None:
        value = {"items": [{"id": f"mem_{i}", "state": "RETAINED", "score": 0.5} for i in range(500)],
                 "count": 500}
        result = self._digest(json.dumps(value))
        self.assertEqual(result.kind, "json")
        self.assertIn("items: [500 × {id: str, state: str, score: float}]", result.text)

    def test_json_lines_report_records_and_keys(self) -> None:
        lines = "\n".join(json.dumps({"seq": i, "type": "user.prompt", "text": "t" * 30}) for i in range(400))
        result = self._digest(lines, name="receipts.jsonl")
        self.assertEqual(result.kind, "json")
        self.assertIn("json lines: 400 record(s)", result.text)
        self.assertIn("type: 400", result.text)

    def test_an_image_is_described_and_never_read(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 800, 600) + b"\x00" * 5000
        result = self._digest(png, name="screenshot.png")
        self.assertEqual(result.kind, "binary")
        self.assertIn("type=image/png 800×600", result.text)
        self.assertEqual(Path(result.raw_path).read_bytes(), png)

    def test_no_spill_keeps_nothing(self) -> None:
        result = self._digest(_test_log(), spill=False)
        self.assertIsNone(result.raw_path)
        self.assertNotIn("[raw]", result.text)
        self.assertFalse(self.out.exists())


class DigestCommand(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-digest-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.out = self.dir / "digests"

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_file(self) -> None:
        path = self.dir / "tests.log"
        path.write_text(_test_log())
        code, out, _ = self._run(["digest", str(path), "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        self.assertIn("kind=log", out)
        self.assertIn("FAILED (errors=1)", out)

    def test_run_digests_the_command_output_and_returns_its_exit_code(self) -> None:
        script = ("import sys\nfor i in range(3000): print(f'step {i} ok-ish')\n"
                  "print('RuntimeError: boom', file=sys.stderr)\nsys.exit(3)")
        code, out, _ = self._run(["digest", "--out-dir", str(self.out), "--json", "--run", "--",
                                  sys.executable, "-c", script])
        self.assertEqual(code, 3)
        payload = json.loads(out)
        self.assertEqual(payload["exit_code"], 3)
        self.assertEqual(payload["kind"], "log")
        self.assertIn("RuntimeError: boom", payload["digest"])
        self.assertIn("RuntimeError: boom", Path(payload["raw_path"]).read_text())

    def test_run_without_a_command_is_refused(self) -> None:
        code, _, err = self._run(["digest", "--run"])
        self.assertEqual(code, 2)
        self.assertIn("needs a command", err)

    def test_an_unreadable_file_is_refused(self) -> None:
        code, _, err = self._run(["digest", str(self.dir / "missing.log")])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_the_digest_directory_comes_from_the_environment(self) -> None:
        path = self.dir / "tests.log"
        path.write_text(_test_log())
        previous = os.environ.get(digest_module.DIGEST_DIR_ENV)
        os.environ[digest_module.DIGEST_DIR_ENV] = str(self.out)
        try:
            code, out, _ = self._run(["digest", str(path)])
        finally:
            if previous is None:
                os.environ.pop(digest_module.DIGEST_DIR_ENV, None)
            else:
                os.environ[digest_module.DIGEST_DIR_ENV] = previous
        self.assertEqual(code, 0)
        self.assertIn(str(self.out), out)
        self.assertEqual(len(list(self.out.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
