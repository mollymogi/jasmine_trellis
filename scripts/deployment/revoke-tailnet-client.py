#!/usr/bin/env python3
"""Revoke one enrolled tailnet client on the hub. Writes core.db directly.

Accepts only ``--db``, ``--host-id``, and ``--confirm``. Not an HTTP route.
Sets ``revoked_at`` on every unrevoked ``api_keys`` row whose
``bound_host_id`` is that host. Does not delete rows. Sets
``tailnet_clients.status`` to ``revoked``. Does not reuse or remove the
``hst_`` primary key. Events already stored for that host are left in place.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jasmine_core import auth, clock, db, ids  # noqa: E402


class RevokeError(Exception):
    """Refusal, or keys were revoked but the client row did not update. Safe to print."""


def revoke(database: Path, host_id: str, *, confirmed: bool) -> dict[str, object]:
    if not confirmed:
        raise RevokeError("--confirm is required")
    if not ids.is_id(host_id, "hst"):
        raise RevokeError("--host-id must be a hst_ id")
    if not database.is_file():
        raise RevokeError("database does not exist")
    conn = db.connect(database)
    try:
        row = conn.execute(
            "SELECT status FROM tailnet_clients WHERE host_id = ?", (host_id,)
        ).fetchone()
        if row is None:
            raise RevokeError("not a tailnet client")
        pending = conn.execute(
            "SELECT key_id FROM api_keys WHERE bound_host_id = ? AND revoked_at IS NULL",
            (host_id,),
        ).fetchall()
        gate = auth.Auth(conn)
        for key in pending:
            gate.revoke(key["key_id"])
        with db.transaction(conn):
            changed = conn.execute(
                "UPDATE tailnet_clients SET status = 'revoked', revoked_at = COALESCE(revoked_at, ?)"
                " WHERE host_id = ?",
                (clock.now_rfc3339(), host_id),
            ).rowcount
        if changed != 1:
            raise RevokeError("client status was not updated; retry revoke")
        landed = conn.execute(
            "SELECT status FROM tailnet_clients WHERE host_id = ?", (host_id,)
        ).fetchone()
        if landed is None or landed["status"] != "revoked":
            raise RevokeError("client status was not updated; retry revoke")
    finally:
        conn.close()
    return {"host_id": host_id, "status": "revoked", "keys_revoked": len(pending)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = revoke(args.db, args.host_id, confirmed=args.confirm)
    except RevokeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
