# ADR 0038: Deterministic digests of long output

Status: implemented on `feat/output-digest`; not yet deployed.
New command; no schema, API or hook change.

Date: 2026-11-02.

## Context

An agent session spends most of its context window on bulk it reads once: a
test run's log (the local suite prints 12 000 lines, 1.1 MB, to say `OK`), a
long design document, a JSON dump from an API. What the agent needs from each
is small: the verdict and the failures, the outline, the shape. The rest is
paid for and then compacted away.

context-mode (npm `context-mode`, ELv2) addresses this with sandboxed
execution, an FTS5 knowledge base and PreToolUse routing that redirects bulky
tool calls. We take one idea from it, not its code: summarise bulky output
before it reaches the context, and keep the original where it can still be
read. Routing (rewriting the agent's tool calls) and session continuity are out
of scope here; both touch the hooks in the user's agent configuration, which
the capture hooks share.

## Decision

1. `jasmine-core digest` reads files, stdin, or the output of a command
   (`--run -- CMD ...`, stdout and stderr together; the command's exit code is
   the command's exit code).
2. Input within the budget (default 4 000 characters) is printed whole. A digest
   of something already short only loses detail.
3. Longer input is digested by kind, detected from the extension and then the
   content:
   - **log**: counts of error, warning and traceback lines; the verdict lines a
     test runner or build tool prints; distinct error and warning lines, each
     with its first line number and a repeat count (times, ids and numbers are
     ignored when grouping); the last two Python tracebacks; the most repeated
     lines; head and tail. A line a test runner marks as passing (`... ok`) is
     never an error, whatever its docstring says.
   - **markdown**: the heading outline with line numbers and the first sentence
     of each section, the lead paragraph, and counts of code blocks, links and
     table rows. Headings inside code fences are not headings.
   - **json**: the shape (keys, types, array lengths); for JSON lines, the record
     count, how many records carry each key, and the first records.
   - **text**: head, tail and the first line of each paragraph.
   - **binary** (images, PDF, archives, anything with NUL bytes): type, size and,
     for PNG and GIF, dimensions. Binary content is described, never read.
4. Sections are filled best-first until the budget is spent. A section that
   lost lines says how many, so a short digest never reads as a complete one.
5. The full input is kept, named by its SHA-256, mode 0600 in a 0700 directory
   (`--out-dir`, else `JASMINE_CORE_DIGEST_DIR`, else
   `~/.local/share/jasmine-core/digests`). The digest prints its path; every
   line reference in the digest is a line number in that file. `--no-spill`
   keeps nothing.
6. The digest passes through `canonical.redact_text`. The raw file is not
   redacted: it is the user's own output, kept only on their machine.
7. No model is called. The same input gives the same digest, so a digest can say
   what is in the raw file and never more.

## Consequences

- Nothing invokes the command on its own. An agent uses it when told to (for
  example in `CLAUDE.md` / `AGENTS.md`: "run long test or build commands as
  `jasmine-core digest --run -- ...`"). Making it automatic needs a PreToolUse
  hook and is a separate decision.
- Image content is not summarised. A model-written description is a later,
  separate decision (who calls which model, and whether the image leaves the
  machine).
- Raw files accumulate. There is no expiry yet; they are content-addressed, so a
  repeated input is kept once.
- No new dependency; standard library only.
