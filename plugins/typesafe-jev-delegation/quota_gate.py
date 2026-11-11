"""Quota gate shared by main-chat routing (hermes-jev-routing) and subagent routing (jev_routes).

Reads two files Hermes already keeps fresh and makes no network call:
- ``quota_cache.json`` (quota plugin, refreshed about every 60 s): usage windows per provider/account.
- ``auth.json`` credential pool: which accounts are in rotation, parked or exhausted.

Policy (chosen by the user 2026-11-10): warn at <= 10 % left, hard floor at <= 3 %, switch back to
Claude only at >= 25 % left. Unknown data never blocks work (fail open).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

WARN_LEFT = 10.0
FLOOR_LEFT = 3.0
BACK_LEFT = 25.0
STALE_SECONDS = 600
CLAUDE, CODEX = "anthropic", "openai-codex"


@dataclass(frozen=True)
class Gate:
    claude_left: Optional[float] = None      # worst window of the visible Claude account; None = unknown
    claude_spare: bool = False                # another Claude account in rotation can take over
    claude_label: str = ""                    # e.g. "session" for the worst window
    codex_left: Optional[float] = None        # best usable Codex account (its worst window); None = unknown
    codex_accounts: int = 0                   # Codex accounts in rotation and not exhausted
    resume_at: Optional[datetime] = None      # earliest time some provider becomes usable again
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def claude_usable(self) -> bool:
        return self.claude_left is None or self.claude_left > WARN_LEFT or self.claude_spare

    @property
    def claude_floor(self) -> bool:
        return self.claude_left is not None and self.claude_left <= FLOOR_LEFT and not self.claude_spare

    @property
    def claude_recovered(self) -> bool:
        return self.claude_left is None or self.claude_left >= BACK_LEFT

    @property
    def claude_low_spare(self) -> bool:
        """The visible account is low but the pool will rotate to another Claude account."""
        return self.claude_left is not None and self.claude_left <= WARN_LEFT and self.claude_spare

    @property
    def codex_usable(self) -> bool:
        if self.codex_accounts <= 0:
            return False
        return self.codex_left is None or self.codex_left > WARN_LEFT

    @property
    def codex_floor(self) -> bool:
        return self.codex_accounts <= 0 or (self.codex_left is not None and self.codex_left <= FLOOR_LEFT)


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _windows(record: Any, now: datetime) -> list[tuple[str, float, Optional[datetime]]]:
    """``(label, percent_left, reset_at)`` per window; a window already past its reset counts as full."""
    rows = []
    for window in (record or {}).get("windows") or []:
        if not isinstance(window, Mapping):
            continue
        used = window.get("used_percent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        reset = _parse_time(window.get("reset_at"))
        left = 100.0 if reset is not None and reset <= now else max(0.0, 100.0 - float(used))
        label = str(window.get("label") or "").lower().replace("current ", "")
        rows.append((label, left, reset))
    return rows


def _ready_at(windows, now: datetime) -> Optional[datetime]:
    """When every window at or under the warn line has reset (None when already fine or unknown)."""
    blocking = [reset for _, left, reset in windows if left <= WARN_LEFT]
    if not blocking:
        return None
    if any(reset is None for reset in blocking):
        return None
    return max(blocking)


def _pool(auth: Mapping[str, Any], provider: str) -> list[dict]:
    entries = ((auth.get("credential_pool") or {}).get(provider)) or []
    return [entry for entry in entries if isinstance(entry, dict)]


def _parked(entry: Mapping[str, Any]) -> bool:
    return entry.get("last_error_reason") == "parked"


def _exhausted_until(entry: Mapping[str, Any], now: datetime) -> Optional[datetime]:
    """Reset time while the pool marks the account exhausted, else None."""
    if entry.get("last_status") != "exhausted" or _parked(entry):
        return None
    reset = _parse_time(entry.get("last_error_reset_at"))
    if reset is not None and reset <= now:
        return None
    return reset or datetime.max.replace(tzinfo=timezone.utc)


def evaluate(now: Optional[datetime] = None, *, cache: Optional[dict] = None, auth: Optional[dict] = None) -> Gate:
    now = now or datetime.now(timezone.utc)
    home = hermes_home()
    cache = _read_json(home / "quota_cache.json") if cache is None else cache
    auth = _read_json(home / "auth.json") if auth is None else auth
    notes: list[str] = []
    fetched = _parse_time(cache.get("fetched_at"))
    fresh = fetched is not None and (now - fetched).total_seconds() <= STALE_SECONDS
    if not fresh:
        notes.append("quota cache stale or missing")
    providers = (cache.get("providers") or {}) if fresh else {}
    resume: list[datetime] = []

    # Claude: the cache shows one account; the pool tells whether another can take over.
    claude_windows = _windows(providers.get(CLAUDE), now)
    claude_left = min((left for _, left, _ in claude_windows), default=None)
    claude_label = min(claude_windows, key=lambda row: row[1])[0] if claude_windows else ""
    live_claude = [e for e in _pool(auth, CLAUDE) if not _parked(e) and _exhausted_until(e, now) is None]
    claude_spare = len(live_claude) >= 2
    if claude_left is not None and claude_left <= WARN_LEFT and not claude_spare:
        ready = _ready_at(claude_windows, now)
        if ready:
            resume.append(ready)
        for entry in _pool(auth, CLAUDE):
            until = _exhausted_until(entry, now)
            if until and until.year < 9999:
                resume.append(until)

    # Codex: every in-rotation account, matched to its cache row by label (email).
    codex_record = providers.get(CODEX) or {}
    rows = {str(a.get("label") or ""): a for a in codex_record.get("accounts") or [] if isinstance(a, Mapping)}
    best: Optional[float] = None
    usable = 0
    for entry in _pool(auth, CODEX):
        if _parked(entry):
            continue
        windows = _windows(rows.get(str(entry.get("label") or "")), now)
        until = _exhausted_until(entry, now)
        if until is not None:
            ready = _ready_at(windows, now)
            candidates = [t for t in (until, ready) if t and t.year < 9999]
            if candidates:
                resume.append(max(candidates))
            continue
        usable += 1
        left = min((l for _, l, _ in windows), default=None)
        if left is not None and left <= WARN_LEFT:
            ready = _ready_at(windows, now)
            if ready:
                resume.append(ready)
        if left is not None and (best is None or left > best):
            best = left
    if usable and best is None and rows:
        notes.append("codex usage unknown for live accounts")

    return Gate(
        claude_left=claude_left,
        claude_spare=claude_spare,
        claude_label=claude_label,
        codex_left=best,
        codex_accounts=usable,
        resume_at=min(resume) if resume else None,
        notes=tuple(notes),
    )


def local_hhmm(moment: Optional[datetime]) -> str:
    """Bangkok-style local time; adds the day when it is not today."""
    if moment is None:
        return "an unknown time"
    local = moment.astimezone()
    today = datetime.now().astimezone().date()
    if local.date() == today:
        return local.strftime("%H:%M")
    return local.strftime("%d/%m %H:%M")


def wait_message(gate: Gate) -> str:
    claude = f"Claude has {gate.claude_left:.0f}% left ({gate.claude_label})" if gate.claude_left is not None else "Claude is nearly out"
    codex = "Codex has no usable account" if gate.codex_accounts <= 0 else f"Codex has {gate.codex_left or 0:.0f}% left"
    return f"Not enough quota: {claude}, {codex}. Wait until {local_hhmm(gate.resume_at)}, then continue."


# --- switch state shared between main chat and subagents -----------------------------------

def state_path() -> Path:
    return hermes_home() / "jev-routing" / "quota-state.json"


def read_state() -> dict:
    return _read_json(state_path())


def write_state(**values: Any) -> None:
    """Best effort; a write failure never changes a route."""
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {**values, "updated": time.time()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
