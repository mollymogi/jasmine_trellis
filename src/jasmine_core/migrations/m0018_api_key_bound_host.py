"""Pin an API key to one host.

NULL stays valid for a loopback request that has no gateway header, so this
migration does not revoke keys already issued on the hub. A request that
presents the gateway header cannot use a NULL pin.
"""

from __future__ import annotations

NAME = "m0018_api_key_bound_host"
VERSION = 18

STATEMENTS = (
    "ALTER TABLE api_keys ADD COLUMN bound_host_id TEXT REFERENCES hosts(host_id)",
)
