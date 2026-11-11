"""Offline tests for the jev_routes delegation backend. No network: every Jev call goes through an
injected fake transport, and a guard makes any real urllib call fail the test."""

from __future__ import annotations

import io
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("HERMES_AGENT_ROOT", Path.home() / ".hermes" / "hermes-agent"))
sys.path[:0] = [str(ROOT), str(CORE)]

from agent.delegation_provider import ROUTE_KEYS, DelegationProvider  # noqa: E402

import jev_routes  # noqa: E402

FAKE_KEY = "tsk_test_FAKE_0123456789abcdef"
OPTIONS = [
    {"name": "fast", "description": "Quick lookups and small edits", "provider": "openrouter",
     "model": "fast-model", "reasoning_effort": "low", "api_key": "MUST-NOT-COPY", "base_url": "https://evil"},
    {"name": "deep", "description": "Hard debugging and design", "provider": "anthropic",
     "model": "deep-model", "reasoning_effort": "high"},
]
DEFAULT = {"provider": "anthropic", "model": "claude-x", "reasoning_effort": "medium",
           "base_url": "", "api_key": "", "service_tier": "fast", "max_iterations": 50}


def config(options=OPTIONS, **extra):
    return {**DEFAULT, "backends": {"jev_routes": {"options": options, **extra}}}


def answer(choice="deep", confidence=0.9):
    return {"answers": {"route": {"choice": choice, "confidence": confidence}}}


def expected_default():
    return {k: v for k, v in DEFAULT.items() if k in ROUTE_KEYS and k != "service_tier"}


class Recorder:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = [], result, exc

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.exc:
            raise self.exc
        return self.result


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        env = {"HERMES_HOME": str(self.home), "TYPESAFE_API_KEY": FAKE_KEY}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.no_net = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network used"))
        self.no_net.start()

    def tearDown(self):
        self.no_net.stop()
        self.env.stop()
        self.tmp.cleanup()

    def route(self, goal="Fix the flaky retry test in the parser", cfg=None, result=None, exc=None):
        rec = Recorder(answer() if result is None and exc is None else result, exc)
        provider = jev_routes.JevRoutesProvider(transport=rec)
        out = provider.route_task({"goal": goal, "context": "SECRET CONTEXT", "index": 0},
                                  cfg if cfg is not None else config())
        return out, rec

    def audit(self):
        path = self.home / "jev-routing" / "audit.jsonl"
        return path, [json.loads(line) for line in path.read_text().splitlines()]


class ContractTests(Base):
    def test_is_a_delegation_provider_with_inert_schema(self):
        p = jev_routes.JevRoutesProvider()
        self.assertIsInstance(p, DelegationProvider)
        self.assertEqual(p.name, "jev_routes")
        self.assertTrue(p.is_available())
        self.assertEqual(p.task_parameters(config()), {})
        self.assertEqual(p.description(config()), "")

    def test_register_hands_core_the_provider(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("jev_plugin", ROOT / "__init__.py",
                                                      submodule_search_locations=[str(ROOT)])
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(mod)
        got = []
        mod.register(type("Ctx", (), {"register_delegation_provider": lambda self, p: got.append(p)})())
        self.assertEqual([p.name for p in got], ["jev_routes"])


class CoreRuntimeTests(Base):
    """Core's own per-task routing step accepts both the routed and the fallback answer."""

    def test_core_route_one_and_effort_check(self):
        try:
            from tools import delegate_tool_routes as core
        except Exception as exc:  # pragma: no cover - core layout changed
            self.skipTest(f"core routing not importable: {exc}")
        for result, label in ((answer("deep"), "deep"), (answer("deep", 0.1), None)):
            provider = jev_routes.JevRoutesProvider(transport=Recorder(result))
            route = core._route_one(provider, {}, 0, {"goal": "Fix the parser", "context": "c"}, config())
            core._check_backend_effort(0, route, config())
            self.assertEqual(route.get("label"), label)
            self.assertNotIn("service_tier", route)
            self.assertNotIn("backends", route)

    def test_real_plugin_manager_discovers_backend_in_temp_home(self):
        """Installed into a throwaway HERMES_HOME only; the user's config is never touched."""
        import shutil
        from hermes_cli import plugins as plugins_mod
        from hermes_cli.plugins import PluginManager
        from agent.delegation_registry import backend_status
        home = self.home / "home" / ".hermes"
        shutil.copytree(ROOT, home / "plugins" / "typesafe-jev-delegation",
                        ignore=shutil.ignore_patterns("tests", "scripts", "__pycache__", ".git"))
        (home / "config.yaml").write_text("plugins:\n  enabled: [typesafe-jev-delegation]\n")
        bundled = self.home / "bundled"
        bundled.mkdir()
        with mock.patch.dict(os.environ, {"HERMES_HOME": str(home), "HOME": str(home.parent)}), \
                mock.patch.object(plugins_mod, "get_bundled_plugins_dir", lambda: bundled):
            PluginManager().discover_and_load()
            provider, reason = backend_status("jev_routes")
        self.assertIsNotNone(provider, reason)
        self.assertEqual(provider.name, "jev_routes")


class RoutingTests(Base):
    def test_allowlisted_choice_returns_only_whitelisted_fields(self):
        out, rec = self.route()
        self.assertEqual(out, {"provider": "anthropic", "model": "deep-model", "reasoning_effort": "high",
                               "label": "deep"})
        out, _ = self.route(result=answer("fast"))
        self.assertEqual(out, {"provider": "openrouter", "model": "fast-model", "reasoning_effort": "low",
                               "label": "fast"})

    def test_request_body_is_goal_only_with_fixed_endpoint_and_model(self):
        _, rec = self.route(goal="  Refactor\x00 the parser  ")
        url, headers, body, timeout = rec.calls[0]
        self.assertEqual(url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(headers["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], {"task": "Refactor the parser"})
        q = body["questions"]["route"]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["criteria"], {"fast": "Quick lookups and small edits",
                                         "deep": "Hard debugging and design"})
        self.assertIn("not", q["instructions"].lower())
        self.assertNotIn("SECRET CONTEXT", json.dumps(body))
        self.assertEqual(timeout, 3.0)

    def test_goal_is_bounded(self):
        _, rec = self.route(goal="x" * 50_000)
        self.assertLessEqual(len(rec.calls[0][2]["state"]["task"]), jev_routes.MAX_GOAL_CHARS)

    def test_fallbacks_preserve_default_route(self):
        cases = {
            "unknown_label": dict(result=answer("Deep")),
            "low_confidence": dict(result=answer("deep", 0.64)),
            "bool_confidence": dict(result=answer("deep", True)),
            "not_mapping": dict(result=["deep"]),
            "no_answers": dict(result={}),
            "timeout": dict(exc=socket.timeout("timed out")),
            "transport": dict(exc=OSError("boom")),
            "parse": dict(exc=ValueError("bad json")),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                out, rec = self.route(**kw)
                self.assertEqual(out, expected_default())
                self.assertEqual(len(rec.calls), 1)

    def test_confidence_threshold_inclusive(self):
        out, _ = self.route(result=answer("deep", 0.65))
        self.assertEqual(out["label"], "deep")


class NoNetworkFallbackTests(Base):
    def assert_baseline_without_call(self, **kw):
        out, rec = self.route(**kw)
        self.assertEqual(out, expected_default())
        self.assertEqual(rec.calls, [])

    def test_missing_key(self):
        os.environ.pop("TYPESAFE_API_KEY")
        self.assert_baseline_without_call()

    def test_empty_goal(self):
        for goal in ("", "   \n", None, 42):
            with self.subTest(goal=goal):
                self.assert_baseline_without_call(goal=goal)

    def test_secret_goals(self):
        for goal in (
            "use -----BEGIN OPENSSH PRIVATE KEY----- abc",
            "set OPENAI_API_KEY=sk-live-whatever and run",
            "call it with Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "the token is sk-abcdefghijklmnopqrstuvwx",
            "github ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "aws AKIAABCDEFGHIJKLMNOP",
            "password = hunter2hunter2",
            f"debug this: {FAKE_KEY}",
        ):
            with self.subTest(goal=goal):
                self.assert_baseline_without_call(goal=goal)

    def test_innocent_mentions_still_route(self):
        out, rec = self.route(goal="Explain how the API key rotation docs describe authorization")
        self.assertEqual(out["label"], "deep")

    def test_invalid_configs(self):
        bad = [
            None, "fast", [], [{"name": "fast"}],
            [{**OPTIONS[1], "reasoning_effort": "turbo"}],
            [OPTIONS[1], dict(OPTIONS[1])],  # duplicate name
            [{**OPTIONS[1], "name": "bad name!"}],
            [{**OPTIONS[1], "model": ""}],
            [{**OPTIONS[1], "provider": 3}],
            [dict(OPTIONS[1], name=f"o{i}") for i in range(jev_routes.MAX_OPTIONS + 1)],
        ]
        for opts in bad:
            with self.subTest(opts=str(opts)[:60]):
                self.assert_baseline_without_call(cfg=config(options=opts))
        for timeout in (0, -1, 30, "3", True):
            with self.subTest(timeout=timeout):
                self.assert_baseline_without_call(cfg=config(timeout_s=timeout))
        self.assert_baseline_without_call(cfg=DEFAULT)

    def test_custom_timeout(self):
        _, rec = self.route(cfg=config(timeout_s=1.5))
        self.assertEqual(rec.calls[0][3], 1.5)


class AuditTests(Base):
    def test_audit_records_decision_without_task_or_key(self):
        self.route(goal="Investigate unique-goal-text crash")
        self.route(result=answer("nope"))
        path, rows = self.audit()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(rows[0]["mode"], "delegation")
        self.assertEqual((rows[0]["outcome"], rows[0]["model"], rows[0]["provider"], rows[0]["reasoning_effort"]),
                         ("routed", "deep-model", "anthropic", "high"))
        self.assertEqual((rows[1]["outcome"], rows[1]["reason"], rows[1]["model"]),
                         ("fallback", "invalid_answer", "claude-x"))
        self.assertIn("timestamp", rows[0])
        raw = path.read_text()
        for leak in ("unique-goal-text", FAKE_KEY, "api_key", "MUST-NOT-COPY", "SECRET CONTEXT"):
            self.assertNotIn(leak, raw)

    def test_audit_failure_does_not_affect_routing(self):
        with mock.patch("os.open", side_effect=PermissionError("ro")):
            out, _ = self.route()
        self.assertEqual(out["label"], "deep")


class TransportTests(Base):
    """The real stdlib client, with urlopen replaced so nothing leaves the process."""

    def fake_response(self, data: bytes, status=200):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.side_effect = lambda n=-1: data if n < 0 else data[:n]
        resp.__enter__.return_value = resp
        return resp

    def test_response_size_limit(self):
        big = b'{"a":"' + b"x" * (jev_routes.MAX_RESPONSE_BYTES + 10) + b'"}'
        opener = mock.MagicMock()
        opener.open.return_value = self.fake_response(big)
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(jev_routes.JevError):
                jev_routes.post_json("https://api.typesafe.ai/v1/systemone", {}, {}, 3.0)

    def test_parses_small_json_and_rejects_non_200(self):
        opener = mock.MagicMock()
        opener.open.return_value = self.fake_response(b'{"ok": 1}')
        with mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(jev_routes.post_json("https://api.typesafe.ai/v1/systemone", {}, {}, 3.0), {"ok": 1})
        opener.open.return_value = self.fake_response(b"{}", status=500)
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(jev_routes.JevError):
                jev_routes.post_json("https://api.typesafe.ai/v1/systemone", {}, {}, 3.0)

    def test_http_error_message_does_not_echo_body(self):
        err = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(f"bad key {FAKE_KEY}".encode()))
        opener = mock.MagicMock()
        opener.open.side_effect = err
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(jev_routes.JevError) as cm:
                jev_routes.post_json("https://api.typesafe.ai/v1/systemone", {}, {}, 3.0)
        self.assertNotIn(FAKE_KEY, str(cm.exception))

    def test_redirects_are_refused(self):
        handler = jev_routes._NoRedirect()
        req = urllib.request.Request("https://api.typesafe.ai/v1/systemone")
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "https://elsewhere"))

    def test_logs_do_not_leak_goal_or_key(self):
        with self.assertLogs(jev_routes.logger, level="DEBUG") as logs:
            jev_routes.logger.debug("probe")
            self.route(goal="leaky-goal-marker", exc=OSError(f"fail {FAKE_KEY}"))
        text = "\n".join(logs.output)
        self.assertNotIn("leaky-goal-marker", text)
        self.assertNotIn(FAKE_KEY, text)


if __name__ == "__main__":
    unittest.main()
