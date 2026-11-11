"""Per-session Jev on/off, published to the Desktop toggle button.

Off means the user's own model pick is left alone (no Jev rewrite). The quota fallback still runs
either way, because it is checked before Jev routing in on_pre_llm_call.
"""

from __future__ import annotations

TOGGLE_EVENT = "toggle"


def jev_enabled(slot: dict) -> bool:
    """Jev is on unless this session turned it off with /jev-routing off."""
    return slot.get("enabled") is not False


def toggle_payload(session_id: str, slot: dict) -> dict:
    return {"session_id": str(session_id or ""), "enabled": jev_enabled(slot)}


def publish_toggle(session_id: str, slot: dict) -> None:
    """Best effort: a missing Desktop never changes a route."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event

        broadcast_plugin_event("hermes-jev-routing", TOGGLE_EVENT, toggle_payload(session_id, slot))
    except Exception:
        return
