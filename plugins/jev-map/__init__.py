"""Hermes plugin: /jev-map widget plus a live log of real model calls for the desktop pane."""

from __future__ import annotations

try:
    from .jev_map import (
        child_info, classify_call, configured_effort, record_call, remember_child, remember_prompt,
        remember_turn, run_map, usage_tokens,
    )
except ImportError:
    from jev_map import (  # type: ignore[no-redef]
        child_info, classify_call, configured_effort, record_call, remember_child, remember_prompt,
        remember_turn, run_map, usage_tokens,
    )


def _command(raw: str = "", **_kwargs) -> str:
    return run_map()


def _refresh_on_turn(**_kwargs) -> None:
    # Hooks must never break a turn: swallow errors, the widget is best-effort.
    try:
        run_map()
    except Exception:
        pass


def _on_pre_llm_call(**kwargs) -> None:
    try:
        remember_prompt(kwargs.get("session_id"), kwargs.get("user_message"))
        remember_turn(kwargs.get("turn_id"))
    except Exception:
        pass


def _on_post_api_request(**kwargs) -> None:
    # One event per model call. Main turn, background review fork, or subagent child: labelled apart.
    try:
        session_id = kwargs.get("session_id")
        kind, extra = classify_call(session_id, kwargs.get("turn_id"), kwargs.get("platform"))
        step = kwargs.get("api_call_count")
        record_call(session_id, kwargs.get("provider"),
                    kwargs.get("response_model") or kwargs.get("model"),
                    kind=kind, seconds=kwargs.get("api_duration"),
                    tokens=usage_tokens(kwargs.get("usage")),
                    step=step if kind == "main" and isinstance(step, int) else None,
                    effort=kwargs.get("reasoning_effort") or configured_effort(kind), **extra)
    except Exception:
        pass


def _on_post_auxiliary_call(**kwargs) -> None:
    # Title, suggestion and other side calls: same log, labelled so they never pass as a turn.
    try:
        task = str(kwargs.get("aux_task") or "aux")
        record_call(kwargs.get("session_id"), kwargs.get("provider"),
                    kwargs.get("response_model") or kwargs.get("model"),
                    kind=f"aux:{task}", seconds=kwargs.get("api_duration"),
                    tokens=usage_tokens(kwargs.get("usage")))
    except Exception:
        pass


def _on_subagent_start(**kwargs) -> None:
    try:
        child = kwargs.get("child_session_id")
        remember_child(child, kwargs.get("parent_turn_id"), kwargs.get("child_goal"), kwargs.get("child_role"))
        record_call(child, "", "", kind="sub-start", parent_turn=str(kwargs.get("parent_turn_id") or ""),
                    goal=child_info(child).get("goal", ""))
    except Exception:
        pass


def _on_subagent_stop(**kwargs) -> None:
    try:
        child = kwargs.get("child_session_id")
        duration = kwargs.get("duration_ms")
        record_call(child, "", "", kind="sub-done", parent_turn=str(kwargs.get("parent_turn_id") or ""),
                    goal=child_info(child).get("goal", ""), status=str(kwargs.get("child_status") or ""),
                    duration_ms=duration if isinstance(duration, (int, float)) else None)
    except Exception:
        pass


def register(ctx) -> None:
    ctx.register_command(
        "jev-map",
        _command,
        description="Show Jev route map and recent delegation picks as a widget",
    )
    ctx.register_hook("post_llm_call", _refresh_on_turn)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("post_auxiliary_call", _on_post_auxiliary_call)
    ctx.register_hook("subagent_start", _on_subagent_start)
    ctx.register_hook("subagent_stop", _on_subagent_stop)
