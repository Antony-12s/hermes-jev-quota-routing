"""Offline tests for the per-session Jev toggle payload."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jev_toggle  # noqa: E402


class TogglePayloadTest(unittest.TestCase):
    def test_default_is_on(self):
        self.assertEqual(jev_toggle.toggle_payload("s1", {}), {"session_id": "s1", "enabled": True})

    def test_explicit_off(self):
        self.assertFalse(jev_toggle.toggle_payload("s1", {"enabled": False})["enabled"])

    def test_on_after_off(self):
        slot = {"enabled": False}
        slot["enabled"] = True
        self.assertTrue(jev_toggle.jev_enabled(slot))

    def test_none_value_counts_as_on(self):
        self.assertTrue(jev_toggle.jev_enabled({"enabled": None}))

    def test_missing_session_id_becomes_empty_string(self):
        self.assertEqual(jev_toggle.toggle_payload(None, {})["session_id"], "")


if __name__ == "__main__":
    unittest.main()
