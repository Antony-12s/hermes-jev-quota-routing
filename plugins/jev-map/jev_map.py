"""Build the Jev route-map widget: routing tiers, kind models, and recent delegation picks."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
DEFAULT_CONFIG = HOME / "jev-routing" / "main.json"
DEFAULT_CONFIG_YAML = HOME / "config.yaml"
DEFAULT_AUDIT = HOME / "jev-routing" / "audit.jsonl"
# JEV_MAP_LIVE_LOG lets a test point the live log at a temp file before the plugin loads.
DEFAULT_LIVE = Path(os.environ.get("JEV_MAP_LIVE_LOG") or HOME / "jev-map" / "live.jsonl")
OUT_FILE = HOME / "jev-map" / "map.html"
AUDIT_LIMIT = 10
LIVE_LIMIT = 50
PROMPT_CHARS = 60

# session_id -> the latest user prompt, set by pre_llm_call and read when the model call lands.
_PROMPTS: dict[str, str] = {}


def remember_prompt(session_id: object, prompt: object) -> None:
    _PROMPTS[str(session_id or "")] = " ".join(str(prompt or "").split())[:PROMPT_CHARS]


def usage_tokens(usage: object) -> int | None:
    """Input + output tokens from a provider usage dict, or None when the provider sent none."""
    if not isinstance(usage, dict):
        return None
    total = sum(int(usage.get(key) or 0) for key in ("input_tokens", "output_tokens"))
    return total or None


# Main turns seen at pre_llm_call. A background review fork never fires pre_llm_call, so a model call
# whose turn was never seen here is a review. Subagent children are found by their session id.
_MAIN_TURNS: dict[str, None] = {}
_CHILDREN: dict[str, dict] = {}
_MEMORY = 500


def _bounded_set(store: dict, key: str, value: object = None) -> None:
    store[key] = value
    while len(store) > _MEMORY:
        store.pop(next(iter(store)))


def remember_turn(turn_id: object) -> None:
    if turn_id:
        _bounded_set(_MAIN_TURNS, str(turn_id))


def remember_child(child_session_id: object, parent_turn_id: object, goal: object, role: object) -> None:
    child = str(child_session_id or "")
    if child:
        info = {"parent_turn": str(parent_turn_id or ""), "goal": " ".join(str(goal or "").split())[:PROMPT_CHARS],
                "role": str(role or "")}
        _bounded_set(_CHILDREN, child, info)


def child_info(session_id: object) -> dict:
    return _CHILDREN.get(str(session_id or ""), {})


def configured_effort(kind: str = "main", config_yaml: Path | None = None) -> str | None:
    """Reasoning effort set in config.yaml for this kind of call (subagents read ``delegation``, the rest
    ``agent``). This is the configured value, not the value sent on the wire."""
    section = "delegation" if kind == "sub" else "agent"
    try:
        import yaml
        path = Path(config_yaml) if config_yaml is not None else DEFAULT_CONFIG_YAML
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        value = (data.get(section) or {}).get("reasoning_effort")
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def classify_call(session_id: object, turn_id: object, platform: object) -> tuple[str, dict]:
    """(kind, extra) for one model call: 'sub' child, 'main' turn, or 'review' background fork."""
    child = child_info(session_id)
    if child or str(platform or "") == "subagent":
        return "sub", child
    if not turn_id or str(turn_id) in _MAIN_TURNS:
        return "main", {}
    return "review", {}


def record_call(session_id: object, provider: object, model: object,
                path: Path | None = None, now: datetime | None = None, *,
                kind: str = "main", seconds: object = None, tokens: int | None = None,
                step: int | None = None, **extra) -> dict:
    """Append one real model call to the live log. ``kind`` is ``main`` for a turn, ``aux:<task>`` for
    an auxiliary call (title, suggestion, ...); only a main turn carries the user's prompt. ``step`` is
    the request's place inside its turn (1 = first model call, 2 = after the first tool round, ...)."""
    path = Path(path) if path is not None else DEFAULT_LIVE
    stamp = (now or datetime.now(ZoneInfo("Asia/Bangkok"))).isoformat(timespec="seconds")
    key = str(session_id or "")
    event = {
        "ts": stamp,
        "session": key[-6:],
        "kind": kind,
        "step": step if kind == "main" else None,
        "prompt": _PROMPTS.get(key, "") if kind == "main" else "",
        "provider": str(provider or ""),
        "model": str(model or ""),
        "seconds": round(float(seconds), 2) if isinstance(seconds, (int, float)) else None,
        "tokens": tokens,
    }
    event.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = (lines + [json.dumps(event, ensure_ascii=False)])[-LIVE_LIMIT:]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return event


def live_events(path: Path | None = None, limit: int = 20) -> list[dict]:
    """Newest-first call events; a torn line is skipped, not fatal."""
    try:
        lines = (Path(path) if path is not None else DEFAULT_LIVE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(events))


def tiers_for_model(model: str, config: dict) -> list[str]:
    """Tier / kind names whose first target is this model: a match, not a record of Jev's pick."""
    names = []
    for group in ("routes", "kindModels"):
        for name, entries in (config.get(group) or {}).items():
            if entries and entries[0].get("model") == model:
                names.append(name)
    return names


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else "-"))


def target_text(entry: dict) -> str:
    effort = entry.get("thinkingLevel") or entry.get("reasoning_effort") or "-"
    return f"{entry.get('provider', '?')} / {entry.get('model', '?')} ({effort})"


def load_audit(path: Path, limit: int = AUDIT_LIMIT) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # skip a torn line, keep the rest
    return rows[-limit:]


def table(title: str, mapping: dict) -> str:
    rows = []
    for name, entries in mapping.items():
        entries = entries or []
        first = target_text(entries[0]) if entries else "(empty)"
        extra = f" +{len(entries) - 1} fallback" if len(entries) > 1 else ""
        rows.append(f"<tr><td>{esc(name)}</td><td>{esc(first)}{esc(extra)}</td></tr>")
    return f"<h3>{esc(title)}</h3><table>{''.join(rows)}</table>"


def audit_table(rows: list[dict]) -> str:
    if not rows:
        return "<h3>Recent picks (typesafe-jev-delegation)</h3><p>No routes yet</p>"
    body = "".join(
        f"<tr><td>{esc(r.get('timestamp'))}</td><td>{esc(r.get('label'))}</td>"
        f"<td>{esc(r.get('provider'))} / {esc(r.get('model'))}</td>"
        f"<td>{esc(r.get('outcome'))}</td></tr>"
        for r in reversed(rows)
    )
    return f"<h3>Recent picks (typesafe-jev-delegation)</h3><table>{body}</table>"


CSS = """
body{font-family:inherit;margin:0;color:var(--foreground);background:transparent}
h3{margin:12px 0 4px;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:12px}
td{border-bottom:1px solid var(--border);padding:4px 6px;text-align:left}
p{color:var(--muted-foreground);font-size:12px}
"""


def effective_mode(config_yaml: Path = DEFAULT_CONFIG_YAML) -> tuple[str, str]:
    """The mode the runtime uses: config.yaml's plugin entry wins over main.json (the plugin reads ctx.get_config)."""
    try:
        import yaml
        entry = (yaml.safe_load(Path(config_yaml).read_text(encoding="utf-8")) or {}).get("plugins", {}) \
            .get("entries", {}).get("hermes-jev-routing", {}).get("settings", {})
        if isinstance(entry, dict) and entry.get("mode"):
            return str(entry["mode"]), "config.yaml"
    except (OSError, ImportError, AttributeError, TypeError):
        pass
    return "shadow", "default (config.yaml unavailable)"


def build_html(config: dict, audit_rows: list[dict], updated_at: datetime | None = None,
               mode: tuple[str, str] | None = None) -> str:
    updated = (updated_at or datetime.now(ZoneInfo("Asia/Bangkok"))).astimezone(ZoneInfo("Asia/Bangkok"))
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    timestamp = f"{updated.day} {months[updated.month - 1]} {updated:%Y %H:%M} ICT"
    mode_name, mode_source = mode or effective_mode()
    body = (
        f"<p>Updated: {timestamp}</p>"
        f"<p>Effective mode: <b>{esc(mode_name)}</b> (from {esc(mode_source)}). "
        "shadow = log only; do not change the model</p>"
        + table("Routes (tier)", config.get("routes") or {})
        + table("Kind models", config.get("kindModels") or {})
        + audit_table(audit_rows)
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


def map_data(
    config_path: Path = DEFAULT_CONFIG,
    audit_path: Path = DEFAULT_AUDIT,
    config_yaml: Path = DEFAULT_CONFIG_YAML,
    live_path: Path | None = None,
) -> dict:
    """The route map as plain data for the desktop pane (same facts the HTML widget shows)."""
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"ok": False, "error": f"Could not read Jev config: {error}"}
    if not isinstance(config, dict):
        return {"ok": False, "error": "Jev config is not a JSON object"}
    mode, source = effective_mode(config_yaml)
    return {
        "ok": True,
        "mode": mode,
        "modeSource": source,
        "routes": config.get("routes") or {},
        "kindModels": config.get("kindModels") or {},
        "audit": list(reversed(load_audit(audit_path))),
        "live": [dict(event, tiers=tiers_for_model(event.get("model", ""), config))
                 for event in live_events(live_path)],
        "updatedAt": datetime.now(ZoneInfo("Asia/Bangkok")).isoformat(timespec="seconds"),
    }


def run_map(
    config_path: Path = DEFAULT_CONFIG,
    audit_path: Path = DEFAULT_AUDIT,
    out_file: Path = OUT_FILE,
    config_yaml: Path = DEFAULT_CONFIG_YAML,
) -> str:
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return f"Could not read Jev config: {error}"
    if not isinstance(config, dict):
        return "Jev config is not a JSON object"
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(build_html(config, load_audit(audit_path), mode=effective_mode(config_yaml)),
                        encoding="utf-8")
    return f"::preview{{file=\"{out_file}\"}}"
