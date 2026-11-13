#!/usr/bin/env python3
"""Confirm one host path onto a hub project. Writes core.db directly.

Not exposed through the gateway. A client resolve never reaches this script.
``--create-project`` mints an empty project on the hub and does not read
``--project-id``. A remote_key that already belongs to another project fails
before the alias is written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jasmine_core import db  # noqa: E402
from jasmine_core.workspace_alias import ConfirmError, confirm_workspace  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--remote-key", required=True)
    parser.add_argument("--confirm", action="store_true", required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--create-project", action="store_true")
    parser.add_argument("--actor-id", help="required with --create-project; ignored otherwise")
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        project_id = confirm_workspace(
            conn,
            host_id=args.host_id,
            root=args.root,
            remote_key=args.remote_key,
            project_id=None if args.create_project else args.project_id,
            create_project=args.create_project,
            actor_id=args.actor_id if args.create_project else None,
        )
    except ConfirmError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(project_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
