"""Quota fallback for the main chat: Claude -> warn -> Codex after 2 min -> wait.

The gate itself lives in the typesafe-jev-delegation plugin (quota_gate.py) so main chat and
subagents use one rule set. This module turns a gate reading into a per-turn action:

- ``plan_turn``: before a main-chat turn, what to do (nothing, offer a switch, switch back,
  warn, or block the turn with a wait time).
- ``tool_block``: during a turn, whether to refuse new tool calls so the model wraps up before
  the provider cuts it off (the turn hook runs once per turn, tool hooks run on every call).
"""

from __future__ import annotations

import importlib.util
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

OFFER_SECONDS = 120
CODEX_PICK = {"provider": "openai-codex", "model": "gpt-6.1-sol", "thinking": "high"}
CLAUDE_DEFAULT = {"provider": "anthropic", "model": "claude-opus-5-5", "thinking": "high"}
WRAPUP_ALLOWED = frozenset({"todo", "todo_list"})
_GATE_TTL = 20.0
_gate_cache: tuple[float, Any] = (0.0, None)
_module = None


def gate_module():
    """quota_gate from the sibling delegation plugin, loaded by path.
    shortcut: path import across plugins, move into a shared package if a third user appears."""
    global _module
    if _module is not None:
        return _module
    try:
        from hermes_constants import get_hermes_home
        from pathlib import Path

        path = Path(get_hermes_home()) / "plugins" / "typesafe-jev-delegation" / "quota_gate.py"
    except Exception:
        from pathlib import Path

        path = Path.home() / ".hermes" / "plugins" / "typesafe-jev-delegation" / "quota_gate.py"
    spec = importlib.util.spec_from_file_location("jev_quota_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["jev_quota_gate"] = module  # dataclasses look the module up while building Gate
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    _module = module
    return module


def current_gate(now: Optional[float] = None):
    global _gate_cache
    now = time.time() if now is None else now
    stamp, gate = _gate_cache
    if gate is None or now - stamp > _GATE_TTL:
        gate = gate_module().evaluate()
        _gate_cache = (now, gate)
    return gate


def provider_of(model: str) -> str:
    name = str(model or "").lower()
    if name.startswith("claude"):
        return "anthropic"
    if name.startswith("gpt") or "codex" in name:
        return "openai-codex"
    return ""


@dataclass(frozen=True)
class Plan:
    action: str = "none"          # none | offer | switch | warn | block
    message: str = ""
    pick: Optional[dict] = None
    deadline: float = 0.0         # epoch seconds for an offer's auto-accept
    state: Optional[dict] = None  # new shared state to write, None = unchanged


def plan_turn(model: str, gate, state: dict, slot: dict, now: Optional[float] = None) -> Plan:
    """What the main chat does before this turn. ``slot`` is per session and may be mutated."""
    qg = gate_module()
    now = time.time() if now is None else now
    provider = provider_of(model)
    if provider == "anthropic":
        if gate.claude_usable:
            slot.pop("declined", None)
            new = {"mode": "claude"} if state.get("mode") not in (None, "", "claude") else None
            if gate.claude_low_spare and not slot.get("spare_noted"):
                slot["spare_noted"] = True
                return Plan("warn", f"This Claude account has {gate.claude_left:.0f}% left ({gate.claude_label}), "
                                    "but another account in the pool has room. Switching accounts automatically.", state=new)
            return Plan(state=new)
        left = f"{gate.claude_left:.0f}%" if gate.claude_left is not None else "?"
        if gate.codex_usable:
            if slot.get("declined") and not gate.claude_floor:
                return Plan("warn", f"Claude has {left} left ({gate.claude_label}). You chose to stay on Claude.")
            pending = state.get("mode") == "pending" and state.get("deadline", 0) > now
            deadline = float(state["deadline"]) if pending else now + OFFER_SECONDS
            msg = (f"Claude has {left} left ({gate.claude_label}). Switching to Codex "
                   f"in {max(0, int(deadline - now))} seconds unless you answer.")
            return Plan("offer", msg, dict(CODEX_PICK), deadline,
                        state={"mode": "pending", "deadline": deadline, "from": model})
        if gate.claude_floor:
            return Plan("block", qg.wait_message(gate), state={"mode": "wait",
                        "until": gate.resume_at.timestamp() if gate.resume_at else 0})
        return Plan("warn", f"Claude has {left} left ({gate.claude_label}) and Codex is unavailable. "
                            "Keep tasks short and save work often.")
    if provider == "openai-codex":
        back = dict(CLAUDE_DEFAULT)
        previous = str(state.get("from") or "")
        if provider_of(previous) == "anthropic":
            back["model"] = previous
        if gate.claude_recovered and not gate.claude_floor and state.get("mode") in ("codex", "pending"):
            return Plan("switch", f"Claude is back ({gate.claude_left or 100:.0f}% left). Switching back to Claude.",
                        back, state={"mode": "claude"})
        if gate.codex_floor:
            if gate.claude_usable or not gate.claude_floor:
                return Plan("switch", "Codex is nearly out. Switching back to Claude.", back, state={"mode": "claude"})
            return Plan("block", qg.wait_message(gate), state={"mode": "wait",
                        "until": gate.resume_at.timestamp() if gate.resume_at else 0})
        if state.get("mode") != "codex":
            return Plan(state={"mode": "codex", "from": state.get("from") or ""})
    return Plan()


def tool_block(tool_name: str, model: str, gate) -> Optional[str]:
    """Refusal text when the session's provider hit the hard floor mid-turn, else None."""
    if tool_name in WRAPUP_ALLOWED:
        return None
    provider = provider_of(model)
    floor = (provider == "anthropic" and gate.claude_floor) or (provider == "openai-codex" and gate.codex_floor)
    if not floor:
        return None
    return ("Quota nearly out (under 3%): do not call more tools. Stop here and tell the user "
            "what is done, what is left, and which files were changed, so work can resume after the reset.")
