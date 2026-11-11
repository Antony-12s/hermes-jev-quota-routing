"""Tests for the Jev route-map builder. Offline; uses temp files only."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_map import (  # noqa: E402
    build_html, effective_mode, live_events, load_audit, map_data, record_call, remember_prompt, run_map,
    tiers_for_model,
)

CONFIG = {
    "mode": "auto",
    "enabled": True,
    "routes": {"premium": [{"provider": "anthropic", "model": "claude-opus-5-5", "thinkingLevel": "high"}]},
    "kindModels": {"debug": [
        {"provider": "openai-codex", "model": "gpt-6.1-sol", "thinkingLevel": "high"},
        {"provider": "anthropic", "model": "claude-opus-5-5", "thinkingLevel": "high"},
    ]},
}


class LiveCallTests(unittest.TestCase):
    def test_defaults_follow_the_install_home_not_the_developer_home(self):
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, '-c', 'import jev_map; print(jev_map.DEFAULT_CONFIG)'],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'HERMES_HOME': home}, text=True, capture_output=True, check=True,
            )
            self.assertEqual(result.stdout.strip(), str(Path(home) / 'jev-routing/main.json'))

    def test_record_call_keeps_prompt_and_real_model_and_caps_log(self):
        from jev_map import LIVE_LIMIT
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "live.jsonl"
            remember_prompt("sess-abc123", "  Build   a plugin  please  ")
            event = record_call("sess-abc123", "anthropic", "claude-opus-5-5", path=log,
                                now=datetime(2026, 11, 4, 6, 0, tzinfo=ZoneInfo("UTC")))
            self.assertEqual(event["prompt"], "Build a plugin please")
            self.assertEqual(event["model"], "claude-opus-5-5")
            self.assertEqual(event["session"], "abc123")
            for i in range(LIVE_LIMIT + 5):
                record_call("s", "p", f"m{i}", path=log)
            self.assertEqual(len(live_events(log, limit=500)), LIVE_LIMIT)

    def test_main_call_records_its_step_in_the_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "live.jsonl"
            remember_prompt("sess-step01", "Check logs")
            ev = record_call("sess-step01", "anthropic", "claude-haiku-5-5", path=log, step=2)
            self.assertEqual(ev["step"], 2)
            aux = record_call("sess-step01", "anthropic", "claude-haiku-5-5", path=log,
                              kind="aux:title_generation", step=2)
            self.assertIsNone(aux["step"])

    def test_aux_call_is_labelled_and_never_carries_the_users_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "live.jsonl"
            remember_prompt("sess-zzz999", "Build a plugin please")
            aux = record_call("sess-zzz999", "anthropic", "claude-haiku-5-5", path=log,
                              kind="aux:title_generation", seconds=0.42, tokens=268)
            self.assertEqual(aux["kind"], "aux:title_generation")
            self.assertEqual(aux["prompt"], "")
            self.assertEqual((aux["seconds"], aux["tokens"]), (0.42, 268))

    def test_usage_tokens_sums_input_and_output_or_none(self):
        from jev_map import usage_tokens
        self.assertEqual(usage_tokens({"input_tokens": 10, "output_tokens": 5}), 15)
        self.assertIsNone(usage_tokens({}))
        self.assertIsNone(usage_tokens(None))

    def test_classify_main_review_and_subagent(self):
        from jev_map import classify_call, remember_child, remember_turn
        remember_turn("cls-main-turn")
        self.assertEqual(classify_call("cls-sess", "cls-main-turn", "desktop"), ("main", {}))
        self.assertEqual(classify_call("cls-sess", "cls-fork-turn", "desktop"), ("review", {}))
        self.assertEqual(classify_call("cls-sess", "", "desktop"), ("main", {}))
        remember_child("cls-child-1", "cls-main-turn", "Check tree", "leaf")
        kind, extra = classify_call("cls-child-1", "cls-child-turn", "subagent")
        self.assertEqual(kind, "sub")
        self.assertEqual(extra["parent_turn"], "cls-main-turn")
        self.assertEqual(extra["goal"], "Check tree")
        self.assertEqual(classify_call("cls-unknown", "cls-x", "subagent")[0], "sub")

    def test_configured_effort_reads_agent_and_delegation_sections(self):
        from jev_map import configured_effort
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("agent:\n  reasoning_effort: medium\ndelegation:\n  reasoning_effort: high\n", encoding="utf-8")
            self.assertEqual(configured_effort("main", cfg), "medium")
            self.assertEqual(configured_effort("review", cfg), "medium")
            self.assertEqual(configured_effort("sub", cfg), "high")
            self.assertIsNone(configured_effort("main", Path(tmp) / "missing.yaml"))

    def test_live_events_newest_first_and_skip_torn_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "live.jsonl"
            log.write_text('{"model":"a"}\n{torn\n{"model":"b"}\n', encoding="utf-8")
            self.assertEqual([e["model"] for e in live_events(log)], ["b", "a"])

    def test_tiers_for_model_matches_first_target_only(self):
        self.assertEqual(tiers_for_model("claude-opus-5-5", CONFIG), ["premium"])
        self.assertEqual(tiers_for_model("nope", CONFIG), [])


class JevMapTests(unittest.TestCase):
    def test_effective_mode_prefers_config_yaml_over_main_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            yml = Path(tmp) / "config.yaml"
            yml.write_text("plugins:\n  entries:\n    hermes-jev-routing:\n      settings:\n        mode: shadow\n",
                           encoding="utf-8")
            self.assertEqual(effective_mode(yml), ("shadow", "config.yaml"))

    def test_effective_mode_falls_back_when_config_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode, source = effective_mode(Path(tmp) / "nope.yaml")
            self.assertEqual(mode, "shadow")
            self.assertIn("default", source)

    def test_html_shows_effective_mode_not_main_json_mode(self):
        page = build_html(CONFIG, [], mode=("shadow", "config.yaml"))
        self.assertIn("Effective mode: <b>shadow</b>", page)
        self.assertNotIn("auto", page)

    def test_html_shows_last_refresh_time_in_bangkok(self):
        refreshed = datetime(2026, 11, 4, 6, 30, tzinfo=ZoneInfo("UTC"))
        page = build_html(CONFIG, [], updated_at=refreshed, mode=("shadow", "config.yaml"))
        self.assertIn("Updated: 4 Nov 2026 13:30 ICT", page)

    def test_html_lists_tiers_and_first_target_with_fallback_count(self):
        page = build_html(CONFIG, [])
        self.assertIn("premium", page)
        self.assertIn("anthropic / claude-opus-5-5 (high)", page)
        self.assertIn("+1 fallback", page)
        self.assertIn("No routes yet", page)

    def test_html_escapes_untrusted_values(self):
        page = build_html({}, [], mode=("<script>", "config.yaml"))
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_audit_keeps_last_rows_and_skips_torn_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            lines = [json.dumps({"label": f"r{i}"}) for i in range(5)] + ["{torn"]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            rows = load_audit(path, limit=2)
            self.assertEqual([r["label"] for r in rows], ["r3", "r4"])

    def test_run_map_writes_widget_and_returns_preview_directive(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "main.json"
            cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
            out = Path(tmp) / "map.html"
            reply = run_map(cfg, Path(tmp) / "none.jsonl", out)
            self.assertEqual(reply, f'::preview{{file="{out}"}}')
            self.assertIn("claude-opus-5-5", out.read_text(encoding="utf-8"))

    def test_run_map_reports_bad_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "main.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertTrue(run_map(bad, Path(tmp) / "x", Path(tmp) / "m.html").startswith("Could not read Jev config"))


    def test_map_data_returns_mode_source_and_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "main.json"
            cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
            yml = Path(tmp) / "config.yaml"
            yml.write_text("plugins:\n  entries:\n    hermes-jev-routing:\n      settings:\n        mode: shadow\n",
                           encoding="utf-8")
            data = map_data(cfg, Path(tmp) / "none.jsonl", yml)
            self.assertTrue(data["ok"])
            self.assertEqual((data["mode"], data["modeSource"]), ("shadow", "config.yaml"))
            self.assertIn("premium", data["routes"])
            self.assertEqual(data["audit"], [])

    def test_map_data_reports_bad_config_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "main.json"
            bad.write_text("{not json", encoding="utf-8")
            data = map_data(bad, Path(tmp) / "x", Path(tmp) / "y")
            self.assertFalse(data["ok"])


    def test_map_data_tags_live_rows_with_matching_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "main.json"
            cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
            log = Path(tmp) / "live.jsonl"
            log.write_text(json.dumps({"model": "claude-opus-5-5", "prompt": "x"}) + "\n", encoding="utf-8")
            data = map_data(cfg, Path(tmp) / "none.jsonl", Path(tmp) / "y.yaml", live_path=log)
            self.assertEqual(data["live"][0]["tiers"], ["premium"])


if __name__ == "__main__":
    unittest.main()
