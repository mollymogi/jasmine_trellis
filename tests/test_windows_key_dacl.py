"""Windows key-file DACL decision. The samples are SIDs; no Windows call is made."""

import os
import tempfile
import unittest
from pathlib import Path

from jasmine_core.hosthook.binding import BindingError, _private_bytes, windows_dacl_allows

USER = "S-1-5-21-111-222-333-1001"
SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
EVERYONE = "S-1-1-0"
USERS = "S-1-5-32-545"
AUTHENTICATED_USERS = "S-1-5-11"
OTHER = "S-1-5-21-111-222-333-1002"


class WindowsDaclDecision(unittest.TestCase):
    def test_current_user_and_system_pass_and_administrators_fail(self) -> None:
        self.assertTrue(windows_dacl_allows(
            owner_sid=USER, user_sid=USER, trustees=(USER, SYSTEM)))
        self.assertFalse(windows_dacl_allows(
            owner_sid=USER, user_sid=USER, trustees=(USER, SYSTEM, ADMINISTRATORS)))
        # Inherited or not, any other trustee fails. A DENY ACE is not required.
        for sid in (EVERYONE, USERS, AUTHENTICATED_USERS, OTHER):
            self.assertFalse(windows_dacl_allows(
                owner_sid=USER, user_sid=USER, trustees=[USER, SYSTEM, sid]), sid)
        self.assertFalse(windows_dacl_allows(owner_sid=USER, user_sid=USER, trustees=None))
        self.assertFalse(windows_dacl_allows(owner_sid=OTHER, user_sid=USER, trustees=(USER, SYSTEM)))


class PosixPrivateBytes(unittest.TestCase):
    def test_mode_0600_and_the_same_uid_are_still_required(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode check")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key"
            path.write_bytes(b"token\n")
            os.chmod(path, 0o600)
            self.assertEqual(_private_bytes(path, "API key file"), b"token\n")
            self.assertEqual(path.stat().st_uid, os.getuid())
            os.chmod(path, 0o644)
            with self.assertRaises(BindingError) as caught:
                _private_bytes(path, "API key file")
            self.assertIn("0600", str(caught.exception))
