#!/usr/bin/env python3
"""Confirm hub aliases from project descriptions that still exist on disk.

Reads core.db and local git config only. It does not open the network and it
does not change Event rows. Several projects on one normalized path are
printed and skipped. A remote key that already points at another project is
printed and the remote row is left as it is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jasmine_core import db  # noqa: E402
from jasmine_core.workspace_alias import ConfirmError, backfill_aliases  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--host-id", required=True)
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        conflicts = backfill_aliases(conn, host_id=args.host_id)
    except ConfirmError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()
    for line in conflicts:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
