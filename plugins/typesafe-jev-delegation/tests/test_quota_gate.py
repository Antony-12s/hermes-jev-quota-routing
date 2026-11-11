"""Offline tests for quota_gate and the jev_routes quota fallback. No real files or network."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("HERMES_AGENT_ROOT", Path.home() / ".hermes" / "hermes-agent"))
sys.path[:0] = [str(ROOT), str(CORE)]

from agent.delegation_provider import DelegationRouteError  # noqa: E402

import jev_routes  # noqa: E402
import quota_gate as qg  # noqa: E402

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
SOON = (NOW + timedelta(hours=2)).isoformat()
LATER = (NOW + timedelta(hours=5)).isoformat()
WEEK = (NOW + timedelta(days=3)).isoformat()


def cache(claude_used=(18, 16), codex=None, fetched=NOW):
    session, week = claude_used
    return {
        "fetched_at": fetched.isoformat(),
        "providers": {
            "anthropic": {"windows": [
                {"label": "Current session", "used_percent": session, "reset_at": SOON},
                {"label": "Current week", "used_percent": week, "reset_at": WEEK},
            ]},
            "openai-codex": {"accounts": codex or []},
        },
    }


def acct(label, *windows):
    return {"label": label, "windows": [{"label": n, "used_percent": u, "reset_at": r} for n, u, r in windows]}


def auth(claude=1, codex=()):
    pool = {"anthropic": [{"id": f"c{i}", "label": f"c{i}", "last_status": "ok"} for i in range(claude)],
            "openai-codex": [dict(entry) for entry in codex]}
    return {"credential_pool": pool}


def live(label):
    return {"label": label, "last_status": "ok"}


def parked(label):
    return {"label": label, "last_status": "exhausted", "last_error_reason": "parked"}


def exhausted(label, until):
    return {"label": label, "last_status": "exhausted", "last_error_reason": "usage_limit_reached",
            "last_error_reset_at": datetime.fromisoformat(until).timestamp()}


class EvaluateTest(unittest.TestCase):
    def test_healthy_claude_is_usable(self):
        gate = qg.evaluate(NOW, cache=cache(), auth=auth())
        self.assertEqual(gate.claude_left, 82)
        self.assertEqual(gate.claude_label, "session")
        self.assertTrue(gate.claude_usable)

    def test_warn_line_is_inclusive(self):
        gate = qg.evaluate(NOW, cache=cache((90, 10)), auth=auth())
        self.assertEqual(gate.claude_left, 10)
        self.assertFalse(gate.claude_usable)
        self.assertFalse(gate.claude_floor)
        gate = qg.evaluate(NOW, cache=cache((89.9, 10)), auth=auth())
        self.assertTrue(gate.claude_usable)

    def test_floor(self):
        gate = qg.evaluate(NOW, cache=cache((97, 10)), auth=auth())
        self.assertTrue(gate.claude_floor)

    def test_second_claude_account_counts_as_spare(self):
        gate = qg.evaluate(NOW, cache=cache((97, 10)), auth=auth(claude=2))
        self.assertTrue(gate.claude_usable)
        self.assertFalse(gate.claude_floor)
        self.assertTrue(gate.claude_low_spare)

    def test_window_past_reset_counts_full(self):
        data = cache((100, 10))
        data["providers"]["anthropic"]["windows"][0]["reset_at"] = (NOW - timedelta(minutes=1)).isoformat()
        gate = qg.evaluate(NOW, cache=data, auth=auth())
        self.assertEqual(gate.claude_left, 90)

    def test_stale_cache_is_unknown_and_fails_open(self):
        gate = qg.evaluate(NOW, cache=cache((100, 100), fetched=NOW - timedelta(hours=1)), auth=auth())
        self.assertIsNone(gate.claude_left)
        self.assertTrue(gate.claude_usable)
        self.assertIn("quota cache stale or missing", gate.notes)

    def test_codex_ignores_parked_and_exhausted_accounts(self):
        codex = [acct("free", ("Session", 38, LATER)),
                 acct("plus1", ("Session", 100, SOON), ("Weekly", 34, WEEK)),
                 acct("plus2", ("Session", 0, LATER), ("Weekly", 100, WEEK))]
        gate = qg.evaluate(NOW, cache=cache(codex=codex),
                           auth=auth(codex=[parked("free"), exhausted("plus1", SOON), parked("plus2")]))
        self.assertEqual(gate.codex_accounts, 0)
        self.assertFalse(gate.codex_usable)
        self.assertTrue(gate.codex_floor)

    def test_codex_best_account_wins(self):
        codex = [acct("a", ("Session", 95, SOON)), acct("b", ("Session", 40, LATER), ("Weekly", 20, WEEK))]
        gate = qg.evaluate(NOW, cache=cache(codex=codex), auth=auth(codex=[live("a"), live("b")]))
        self.assertEqual(gate.codex_accounts, 2)
        self.assertEqual(gate.codex_left, 60)
        self.assertTrue(gate.codex_usable)

    def test_resume_at_is_earliest_recovery(self):
        codex = [acct("plus1", ("Session", 100, SOON), ("Weekly", 34, WEEK))]
        gate = qg.evaluate(NOW, cache=cache((95, 10), codex=codex), auth=auth(codex=[exhausted("plus1", SOON)]))
        self.assertEqual(gate.resume_at, datetime.fromisoformat(SOON))
        self.assertIn("รอถึง", qg.wait_message(gate))

    def test_recovered_needs_25_percent(self):
        self.assertFalse(qg.evaluate(NOW, cache=cache((76, 10)), auth=auth()).claude_recovered)
        self.assertTrue(qg.evaluate(NOW, cache=cache((75, 10)), auth=auth()).claude_recovered)


OPTIONS = {
    "fast": {"route": {"provider": "anthropic", "model": "claude-haiku-5-5", "reasoning_effort": "low"}},
    "deep": {"route": {"provider": "anthropic", "model": "claude-opus-5-5", "reasoning_effort": "high"}},
    "codex": {"route": {"provider": "openai-codex", "model": "gpt-6.1-sol", "reasoning_effort": "high"}},
}


def gate(**kw):
    return qg.Gate(**kw)


class SubRouteGateTest(unittest.TestCase):
    def run_gate(self, g, route, mode=""):
        with mock.patch.object(qg, "evaluate", return_value=g), \
                mock.patch.object(qg, "read_state", return_value={"mode": mode}):
            return jev_routes.JevRoutesProvider._quota_gate(route, "x", OPTIONS)

    def test_keeps_claude_when_healthy(self):
        self.assertIsNone(self.run_gate(gate(claude_left=80, codex_accounts=1), OPTIONS["fast"]["route"]))

    def test_main_on_codex_moves_claude_pick(self):
        out = self.run_gate(gate(claude_left=80, codex_accounts=1), OPTIONS["fast"]["route"], mode="codex")
        self.assertEqual(out[1], "codex")

    def test_warning_band_keeps_claude(self):
        self.assertIsNone(self.run_gate(gate(claude_left=8, codex_accounts=1), OPTIONS["fast"]["route"]))

    def test_floor_moves_to_codex(self):
        out = self.run_gate(gate(claude_left=2, codex_accounts=1, codex_left=60), OPTIONS["deep"]["route"])
        self.assertEqual(out[1:], ("codex", "claude_low"))

    def test_both_empty_refuses_with_wait(self):
        with self.assertRaises(DelegationRouteError) as caught:
            self.run_gate(gate(claude_left=2, codex_accounts=0), OPTIONS["deep"]["route"])
        self.assertIn("รอถึง", str(caught.exception))

    def test_codex_pick_falls_back_to_deep(self):
        out = self.run_gate(gate(claude_left=80, codex_accounts=0), OPTIONS["codex"]["route"])
        self.assertEqual(out[1:], ("deep", "codex_low"))

    def test_codex_pick_kept_when_codex_has_room(self):
        self.assertIsNone(self.run_gate(gate(claude_left=80, codex_accounts=1, codex_left=50), OPTIONS["codex"]["route"]))

    def test_unreadable_gate_keeps_pick(self):
        with mock.patch.object(qg, "evaluate", side_effect=RuntimeError("boom")):
            self.assertIsNone(jev_routes.JevRoutesProvider._quota_gate(OPTIONS["fast"]["route"], "fast", OPTIONS))


if __name__ == "__main__":
    unittest.main()
