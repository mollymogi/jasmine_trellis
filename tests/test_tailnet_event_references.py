"""Tailnet admission confines event references before any event write."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jasmine_core import errors
from jasmine_core.api.handlers import admit_workspace_alias


class EventReferences(unittest.TestCase):
    def test_session_and_task_must_match_admitted_host_and_project(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sessions (session_id TEXT, host_id TEXT, project_id TEXT)")
        conn.execute("CREATE TABLE tasks (task_id TEXT, project_id TEXT)")
        conn.executemany("INSERT INTO sessions VALUES (?, ?, ?)",
                         [("own", "A", "P"), ("foreign", "B", "P"), ("other", "A", "Q")])
        conn.executemany("INSERT INTO tasks VALUES (?, ?)", [("own", "P"), ("other", "Q")])
        core = SimpleNamespace(tailnet_request=True, conn=conn)
        principal = SimpleNamespace(bound_host_id="A")
        route = SimpleNamespace(checks_alias="project")
        for references, error in [
            ({"session_id": "foreign"}, errors.HostMismatch),
            ({"session_id": "other"}, errors.ProjectNotAliased),
            ({"task_id": "other"}, errors.ProjectNotAliased),
            ({"session_id": "missing"}, errors.NotFound),
            ({"task_id": "missing"}, errors.NotFound),
            ({"session_id": "own", "task_id": "own"}, None),
            ({}, None),
        ]:
            with self.subTest(references=references), patch(
                    "jasmine_core.workspace_alias.project_confirmed", return_value=True):
                body = {"host_id": "A", "project_id": "P", **references}
                request = SimpleNamespace(body=b"json", method="POST", path="/v1/events",
                                          json_body=lambda: body)
                if error:
                    with self.assertRaises(error):
                        admit_workspace_alias(core, request, principal, route)
                else:
                    admit_workspace_alias(core, request, principal, route)
