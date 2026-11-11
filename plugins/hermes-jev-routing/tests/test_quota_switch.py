"""Offline tests for the main-chat quota fallback (quota_switch). No real files or network."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import quota_switch as qs  # noqa: E402

qg = qs.gate_module()
NOW = 1_800_000_000.0
RESET = datetime.fromtimestamp(NOW, tz=timezone.utc) + timedelta(hours=2)


def gate(**kw):
    kw.setdefault("claude_label", "session")
    return qg.Gate(**kw)


class PlanTurnTest(unittest.TestCase):
    def plan(self, model, g, state=None, slot=None):
        return qs.plan_turn(model, g, state or {}, slot if slot is not None else {}, now=NOW)

    def test_healthy_claude_does_nothing(self):
        self.assertEqual(self.plan("claude-opus-5-5", gate(claude_left=80, codex_accounts=1)).action, "none")

    def test_low_claude_offers_codex_with_two_minutes(self):
        plan = self.plan("claude-opus-5-5", gate(claude_left=9, codex_accounts=1, codex_left=60))
        self.assertEqual(plan.action, "offer")
        self.assertEqual(plan.pick["provider"], "openai-codex")
        self.assertEqual(plan.deadline, NOW + 120)
        self.assertEqual(plan.state["mode"], "pending")
        self.assertEqual(plan.state["from"], "claude-opus-5-5")

    def test_pending_offer_keeps_its_deadline(self):
        plan = self.plan("claude-opus-5-5", gate(claude_left=9, codex_accounts=1),
                         state={"mode": "pending", "deadline": NOW + 30})
        self.assertEqual(plan.deadline, NOW + 30)

    def test_declined_offer_only_warns_until_floor(self):
        slot = {"declined": True}
        self.assertEqual(self.plan("claude-opus-5-5", gate(claude_left=8, codex_accounts=1), slot=slot).action, "warn")
        self.assertEqual(self.plan("claude-opus-5-5", gate(claude_left=2, codex_accounts=1), slot=slot).action, "offer")

    def test_low_claude_no_codex_warns(self):
        self.assertEqual(self.plan("claude-opus-5-5", gate(claude_left=8, codex_accounts=0)).action, "warn")

    def test_floor_claude_no_codex_blocks_with_wait_time(self):
        plan = self.plan("claude-opus-5-5", gate(claude_left=2, codex_accounts=0, resume_at=RESET))
        self.assertEqual(plan.action, "block")
        self.assertIn("Wait until", plan.message)
        self.assertEqual(plan.state["mode"], "wait")

    def test_spare_account_warns_once(self):
        slot = {}
        g = gate(claude_left=5, claude_spare=True, codex_accounts=0)
        self.assertEqual(self.plan("claude-opus-5-5", g, slot=slot).action, "warn")
        self.assertEqual(self.plan("claude-opus-5-5", g, slot=slot).action, "none")

    def test_codex_switches_back_only_at_25_percent(self):
        state = {"mode": "codex", "from": "claude-opus-4-8"}
        self.assertEqual(self.plan("gpt-6.1-sol", gate(claude_left=20, codex_accounts=1), state).action, "none")
        plan = self.plan("gpt-6.1-sol", gate(claude_left=30, codex_accounts=1), state)
        self.assertEqual(plan.action, "switch")
        self.assertEqual(plan.pick["model"], "claude-opus-4-8")

    def test_codex_floor_returns_to_claude_when_possible(self):
        plan = self.plan("gpt-6.1-sol", gate(claude_left=8, codex_accounts=0), {"mode": "codex"})
        self.assertEqual(plan.action, "switch")

    def test_both_at_floor_blocks(self):
        plan = self.plan("gpt-6.1-sol", gate(claude_left=1, codex_accounts=0, resume_at=RESET), {"mode": "codex"})
        self.assertEqual(plan.action, "block")

    def test_user_picked_codex_records_mode(self):
        plan = self.plan("gpt-6.1-sol", gate(claude_left=80, codex_accounts=1), {"mode": "claude"})
        self.assertEqual(plan.action, "none")
        self.assertEqual(plan.state["mode"], "codex")


class ToolBlockTest(unittest.TestCase):
    def test_blocks_at_claude_floor(self):
        self.assertIsNotNone(qs.tool_block("terminal", "claude-opus-5-5", gate(claude_left=2)))

    def test_allows_todo_at_floor(self):
        self.assertIsNone(qs.tool_block("todo", "claude-opus-5-5", gate(claude_left=2)))

    def test_allows_above_floor_and_with_spare(self):
        self.assertIsNone(qs.tool_block("terminal", "claude-opus-5-5", gate(claude_left=8)))
        self.assertIsNone(qs.tool_block("terminal", "claude-opus-5-5", gate(claude_left=2, claude_spare=True)))

    def test_codex_session_checks_codex(self):
        self.assertIsNotNone(qs.tool_block("terminal", "gpt-6.1-sol", gate(claude_left=80, codex_accounts=0)))
        self.assertIsNone(qs.tool_block("terminal", "gpt-6.1-sol", gate(claude_left=1, codex_accounts=1, codex_left=50)))


if __name__ == "__main__":
    unittest.main()
