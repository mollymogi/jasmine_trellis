"""Deterministic digests of long tool output: logs, documents, JSON (ADR 0038).

An agent that reads a 12 000-line test log, a long design document or a JSON
dump pays for every byte in its context window, although what it needs is the
failure, the outline or the shape. A digest keeps that and leaves the rest in a
raw file the agent can still open by line number.

Nothing here calls a model. The same input always gives the same digest, so a
digest can be trusted to say what is in the raw file and never to say more.
Images and other binary input are described (type, size), never interpreted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import canonical

#: Characters of digest printed by default. Input at or under this size is
#: passed through whole: a digest of something already short only loses detail.
DEFAULT_BUDGET = 4000
#: Longest single line a digest prints; a minified bundle is one line.
LINE_LIMIT = 240
KINDS = ("log", "markdown", "json", "text", "binary")
DIGEST_DIR_ENV = "JASMINE_CORE_DIGEST_DIR"

_EXTENSIONS = {"log": ".log", "markdown": ".md", "json": ".json", "text": ".txt", "binary": ".bin"}

_ERROR_RE = re.compile(
    r"(?i)\b(\w*error|errors|\w*exception|fatal|failed|failure|fail|panic|denied|refused|"
    r"timed out|timeout|segmentation fault|not found)\b")
#: Checked before errors: `ResourceWarning: ... traceback` is a warning.
_WARN_RE = re.compile(r"(?i)\b(warn|warning|warnings|\w+warning|deprecated)\b")
#: A test runner's line for a test that passed. Its docstring may well say
#: "failure"; the verdict at the end of the line is what counts.
_PASSED_RE = re.compile(r"(?:\.\.\.|\s)\s*(?:ok|skipped\b.*|expected failure|PASSED|passed)\s*$")
#: Lines a test runner or build tool prints as its verdict.
_SUMMARY_RE = re.compile(
    r"(?i)^\s*(ran \d+ tests?\b|failed\s*\(|=+ .*\b(passed|failed|error)\b.*=+$|"
    r"tests?:\s+\d+|test files?\s+\d+|\d+ (passed|failed)\b|build (succeeded|failed)|"
    r"exit (code|status)\b|npm err! code)")
#: unittest's verdict, upper case only: a bare `ok` is one passing test.
_UNITTEST_OK_RE = re.compile(r"^OK(?: \(.*\))?$")
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b")
_LEVEL_RE = re.compile(r"(?i)\b(debug|info|notice|warn|warning|error|fatal|critical|trace)\b")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX_RE = re.compile(r"\b(?:0x[0-9a-f]+|[0-9a-f]{8,})\b", re.I)
_NUM_RE = re.compile(r"\d+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]+\)|https?://\S+")
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class Digest:
    """One input, digested. `text` is what the agent reads."""

    kind: str
    bytes: int
    lines: int
    sha256: str
    passthrough: bool
    text: str
    raw_path: str | None = None
    exit_code: int | None = None
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "bytes": self.bytes, "lines": self.lines, "sha256": self.sha256,
                "passthrough": self.passthrough, "raw_path": self.raw_path,
                "exit_code": self.exit_code, "source": self.source, "digest": self.text}


@dataclass
class _Section:
    title: str
    lines: list[str]
    priority: int  # lower is kept first when the budget runs out
    from_end: bool = False  # a tail keeps its last lines, not its first
    omitted_note: str = "see the raw file"
    kept: list[str] = field(default_factory=list)


# -- detection ---------------------------------------------------------------


def _binary_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if b"\x00" in data[:8192]:
        return "application/octet-stream"
    return None


def _image_size(data: bytes, mime: str) -> tuple[int, int] | None:
    try:
        if mime == "image/png" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif" and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
    except struct.error:
        return None
    return None


def _is_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
        return True
    except ValueError:
        return _jsonl_records(stripped) is not None


def _jsonl_records(text: str) -> list[Any] | None:
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            return None
    return records if len(records) > 1 else None


def detect_kind(text: str, name: str | None = None) -> str:
    """Pick the digest for a decoded text. A file extension wins over content."""
    suffix = Path(name).suffix.lower() if name else ""
    if suffix in (".json", ".jsonl", ".ndjson") and _is_json(text):
        return "json"
    if suffix in (".md", ".markdown", ".mdx"):
        return "markdown"
    if suffix in (".log", ".out", ".err"):
        return "log"
    if _is_json(text):
        return "json"
    lines = text.splitlines()
    headings = sum(1 for line in lines if _HEADING_RE.match(line))
    if headings >= 2:
        return "markdown"
    sample = lines[:2000]
    if sample:
        stamped = sum(1 for line in sample if _TIMESTAMP_RE.search(line) or _LEVEL_RE.match(line.strip()))
        signals = sum(1 for line in sample if _ERROR_RE.search(line) or "Traceback (most recent" in line)
        if stamped / len(sample) >= 0.2 or signals / len(sample) >= 0.05:
            return "log"
    return "text"


# -- shared helpers ----------------------------------------------------------


def _clip(line: str, limit: int = LINE_LIMIT) -> str:
    line = line.rstrip("\n")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _normalize(line: str) -> str:
    """The line with what varies between repeats (times, ids, numbers) erased."""
    line = _TIMESTAMP_RE.sub("<t>", line)
    line = _UUID_RE.sub("<id>", line)
    line = _HEX_RE.sub("<hex>", line)
    return _NUM_RE.sub("#", line).strip()


def _grouped(numbered: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """(first line number, count, line) per distinct normalized line, first seen first."""
    groups: dict[str, list[Any]] = {}
    for number, line in numbered:
        key = _normalize(line)
        if key in groups:
            groups[key][1] += 1
        else:
            groups[key] = [number, 1, line]
    return [tuple(group) for group in groups.values()]  # type: ignore[misc]


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"  # pragma: no cover


def _fit(header: list[str], sections: list[_Section], budget: int) -> str:
    """Keep whole lines, best sections first, until the budget is spent.

    Sections render in the order given; `priority` only decides who gets the
    room. A section that loses lines says how many, so a short digest never
    reads as a complete one.
    """
    def render() -> str:
        out = list(header)
        for section in sections:
            if not section.lines:
                continue
            if not section.kept:
                out.append(f"-- {section.title}: {len(section.lines)} line(s) omitted, {section.omitted_note}")
                continue
            out.append(f"-- {section.title}")
            missing = len(section.lines) - len(section.kept)
            note = f"   … {missing} {'earlier' if section.from_end else 'more'}, {section.omitted_note}"
            if missing and section.from_end:
                out.append(note)
            out.extend(section.kept)
            if missing and not section.from_end:
                out.append(note)
        return canonical.redact_text("\n".join(out))

    for section in sections:
        section.kept.clear()
    rendered = render()
    if len(rendered) > budget:
        raise ValueError(f"digest budget too small for metadata and omission notices (need {len(rendered)} characters)")
    for section in sorted(sections, key=lambda s: s.priority):
        for line in (reversed(section.lines) if section.from_end else section.lines):
            if section.from_end:
                section.kept.insert(0, line)
            else:
                section.kept.append(line)
            candidate = render()
            if len(candidate) > budget:
                section.kept.pop(0 if section.from_end else -1)
                break
            rendered = candidate
    return rendered


# -- per kind ----------------------------------------------------------------


def _tracebacks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line indexes of each Python traceback, end inclusive."""
    spans = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("Traceback (most recent call last)"):
            start = i
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                i += 1
            end = min(i, len(lines) - 1)  # the exception line itself
            spans.append((start, end))
        i += 1
    return spans


def _log_sections(lines: list[str]) -> tuple[list[str], list[_Section]]:
    numbered = list(enumerate(lines, 1))
    candidates = [(n, line) for n, line in numbered if not _PASSED_RE.search(line)]
    warnings = [(n, line) for n, line in candidates if _WARN_RE.search(line)]
    errors = [(n, line) for n, line in candidates if not _WARN_RE.search(line) and _ERROR_RE.search(line)]
    summary = [(n, line) for n, line in numbered
               if _SUMMARY_RE.search(line) or _UNITTEST_OK_RE.match(line.strip())]
    error_groups = _grouped(errors)
    warn_groups = _grouped(warnings)

    def fmt(groups: list[tuple[int, int, str]]) -> list[str]:
        return [f"L{n}{f' ×{count}' if count > 1 else ''}  {_clip(line)}" for n, count, line in groups]

    trace_lines: list[str] = []
    spans = _tracebacks(lines)
    for start, end in spans[-2:]:
        block = list(range(start, end + 1))
        if len(block) > 14:  # the frames nearest the error, and the error
            block = [start] + block[-13:]
        trace_lines.append(f"L{start + 1}–L{end + 1}:")
        trace_lines.extend(f"  {_clip(lines[i])}" for i in block)

    repeats = sorted((g for g in _grouped(numbered) if g[1] >= 5 and g[2].strip()),
                     key=lambda g: -g[1])[:8]
    head_n = min(10, len(lines))
    tail_start = max(head_n, len(lines) - 20)
    header = [f"errors: {len(errors)} line(s), {len(error_groups)} distinct · "
              f"warnings: {len(warnings)} line(s), {len(warn_groups)} distinct · "
              f"tracebacks: {len(spans)}"]
    sections = [
        _Section(f"head (L1–L{head_n})", [_clip(line) for line in lines[:head_n]], 5),
        _Section("verdict lines", [f"L{n}  {_clip(line)}" for n, line in summary[-10:]], 0),
        _Section("errors (first line, ×repeats)", fmt(error_groups), 1),
        _Section("last tracebacks", trace_lines, 2),
        _Section("warnings (first line, ×repeats)", fmt(warn_groups), 6),
        _Section("most repeated lines", fmt(repeats), 7),
        _Section(f"tail (L{tail_start + 1}–L{len(lines)})",
                 [_clip(line) for line in lines[tail_start:]], 3, from_end=True),
    ]
    return header, sections


def _markdown_sections(lines: list[str]) -> tuple[list[str], list[_Section]]:
    outline, lead = [], ""
    fence_char, fence_length = "", 0
    fences = links = tables = 0
    pending: tuple[int, str] | None = None  # heading still waiting for its first sentence
    for number, line in enumerate(lines, 1):
        fence = _FENCE_RE.match(line)
        if fence_char:
            if (fence and fence.group(1)[0] == fence_char
                    and len(fence.group(1)) >= fence_length and not fence.group(2).strip()):
                fence_char, fence_length = "", 0
                fences += 1
            continue
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            fence_char, fence_length = fence.group(1)[0], len(fence.group(1))
            continue
        links += len(_LINK_RE.findall(line))
        tables += 1 if _TABLE_RE.match(line) else 0
        heading = _HEADING_RE.match(line)
        if heading:
            if pending:
                outline.append(pending[1])
            indent = "  " * (len(heading.group(1)) - 1)
            pending = (number, f"L{number} {indent}{heading.group(1)} {heading.group(2)}")
            continue
        text = line.strip()
        if not text or text.startswith(("<", "|", "![", "[![", "---")):
            continue
        if not lead and pending is None:
            lead = text
        if pending:
            first = re.split(r"(?<=[.!?。！？])\s", text, maxsplit=1)[0]
            outline.append(_clip(f"{pending[1]} — {first}", LINE_LIMIT))
            pending = None
    if pending:
        outline.append(pending[1])
    header = [f"headings: {len(outline)} · code blocks: {fences} · links: {links} · table rows: {tables}"]
    sections = [
        _Section("lead", [_clip(lead, 400)] if lead else [], 0),
        _Section("outline (line, heading — first sentence)", outline, 1),
    ]
    return header, sections


def _text_sections(lines: list[str]) -> tuple[list[str], list[_Section]]:
    paragraphs = []
    previous_blank = True
    for number, line in enumerate(lines, 1):
        if line.strip() and previous_blank:
            paragraphs.append(f"L{number}  {_clip(line.strip(), 160)}")
        previous_blank = not line.strip()
    head_n = min(15, len(lines))
    tail_start = max(head_n, len(lines) - 10)
    header = [f"paragraphs: {len(paragraphs)}"]
    sections = [
        _Section(f"head (L1–L{head_n})", [_clip(line) for line in lines[:head_n]], 0),
        _Section("paragraph starts", paragraphs[1:] if paragraphs else [], 2),
        _Section(f"tail (L{tail_start + 1}–L{len(lines)})", [_clip(line) for line in lines[tail_start:]], 1,
                 from_end=True),
    ]
    return header, sections


def _shape(value: Any, depth: int = 0, max_depth: int = 3) -> str:
    if isinstance(value, dict):
        if depth >= max_depth:
            return f"{{… {len(value)} keys}}"
        return "{" + ", ".join(f"{k}: {_shape(v, depth + 1, max_depth)}" for k, v in list(value.items())[:12]) + \
            (f", … {len(value) - 12} more" if len(value) > 12 else "") + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return f"[{len(value)} × {_shape(value[0], depth + 1, max_depth)}]"
    if isinstance(value, str):
        return "str" if len(value) <= 40 else f"str({len(value)})"
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    return type(value).__name__


def _json_sections(text: str) -> tuple[list[str], list[_Section]]:
    try:
        records: list[Any] | None = None
        value = json.loads(text)
    except ValueError:
        records = _jsonl_records(text) or []
        value = None
    if records is not None:
        keys: dict[str, int] = {}
        for record in records:
            if isinstance(record, dict):
                for key in record:
                    keys[key] = keys.get(key, 0) + 1
        header = [f"json lines: {len(records)} record(s)"]
        sections = [
            _Section("keys (records that have them)", [f"{k}: {n}" for k, n in keys.items()], 1),
            _Section("first record shape", [_clip(_shape(records[0]), 600)] if records else [], 0),
            _Section("first records", [_clip(json.dumps(r, ensure_ascii=False)) for r in records[:3]], 2),
        ]
        return header, sections
    header = [f"json: {type(value).__name__}"]
    shape = _shape(value, max_depth=4)
    sections = [_Section("shape", [_clip(shape, 1200)], 0)]
    if isinstance(value, list):
        sections.append(_Section("first items", [_clip(json.dumps(v, ensure_ascii=False)) for v in value[:3]], 1))
    return header, sections


# -- entry -------------------------------------------------------------------


def default_dir() -> Path:
    chosen = os.environ.get(DIGEST_DIR_ENV)
    return Path(chosen).expanduser() if chosen else Path.home() / ".local" / "share" / "jasmine-core" / "digests"


def _spill(data: bytes, sha: str, kind: str, out_dir: Path) -> Path:
    """Keep the raw input, private to this user, named by its content."""
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = out_dir / f"{sha[:16]}{_EXTENSIONS[kind]}"
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=out_dir)
        tmp = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return path


def digest(data: bytes, *, name: str | None = None, kind: str = "auto", budget: int = DEFAULT_BUDGET,
           out_dir: Path | None = None, spill: bool = True, exit_code: int | None = None) -> Digest:
    """Digest one input. Input within the budget is returned whole."""
    if budget < 1:
        raise ValueError("digest budget must be positive")
    sha = hashlib.sha256(data).hexdigest()
    mime = _binary_type(data)
    text = None
    if mime is None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
    if mime is not None or kind == "binary":
        raw = _spill(data, sha, "binary", out_dir or default_dir()) if spill else None
        size = _image_size(data, mime or "")
        detail = f" {size[0]}×{size[1]}" if size else ""
        body = (f"[digest] kind=binary type={mime or 'unknown'}{detail} bytes={_human_bytes(len(data))} "
                f"sha256={sha[:16]}\n(binary content is described, not read)")
        if raw:
            body += f"\n[raw] {raw}"
        body = canonical.redact_text(body)
        if len(body) > budget:
            raise ValueError(f"digest budget too small for binary metadata (need {len(body)} characters)")
        return Digest("binary", len(data), 0, sha, False, body, str(raw) if raw else None, exit_code, name)

    assert text is not None
    original_length = len(text)
    chosen = detect_kind(text, name) if kind == "auto" else kind
    # Mask complete credentials before clipping or inserting section delimiters.
    # Preserve boundaries so every L-number still refers to the unredacted raw file.
    text = canonical.redact_text(text, preserve_lines=True)
    lines = text.splitlines()
    if original_length <= budget and len(text) <= budget:
        return Digest(chosen, len(data), len(lines), sha, True, text,
                      None, exit_code, name)

    raw = _spill(data, sha, chosen, out_dir or default_dir()) if spill else None
    if chosen == "log":
        header, sections = _log_sections(lines)
    elif chosen == "markdown":
        header, sections = _markdown_sections(lines)
    elif chosen == "json":
        header, sections = _json_sections(text)
    else:
        header, sections = _text_sections(lines)
    top = (f"[digest] kind={chosen} lines={len(lines)} bytes={_human_bytes(len(data))} sha256={sha[:16]}"
           + (f" exit={exit_code}" if exit_code is not None else "")
           + (f" source={_clip(' '.join(name.split()), 120)}" if name else ""))
    lead = [top]
    if raw:
        lead.append(f"[raw] {raw}  (full text; open it by line number for anything cut here)")
    rendered = _fit(lead + header, sections, budget)
    return Digest(chosen, len(data), len(lines), sha, False, rendered,
                  str(raw) if raw else None, exit_code, name)
