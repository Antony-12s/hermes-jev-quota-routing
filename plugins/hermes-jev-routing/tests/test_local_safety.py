"""Local privacy, request-shape and session-isolation regressions."""
import importlib.util
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('jev_local_safety', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)
from jev_local_safety.jev import classify, urllib_transport, JevError
from jev_local_safety.decide import AvailableModel, Decision, RouteTarget
from jev_local_safety.route import rewrite


def decision():
    return Decision(desired_tier='high', tier='high', target=RouteTarget('openai-codex', 'gpt-6.1-sol', thinking_level='high'), model=AvailableModel('openai-codex', 'gpt-6.1-sol'), tier_index=2, demand_score=2, budget_pressure=0, downgraded=False, low_confidence_fallback=False, kind_specialised=False, held=False, reason='test', notes=())


class SafetyTests(unittest.TestCase):
    def setUp(self):
        plugin._LAST.clear()
        plugin._SESSIONS.clear()
        plugin._CTX = None

    def test_zero_history_is_empty(self):
        self.assertEqual(plugin._history([{'role': 'user', 'content': 'PRIVATE HISTORY'}], 0), '')

    def test_unknown_session_never_borrows_last_route(self):
        plugin.remember(decision(), 'auto')
        request = {'model': 'old-model'}
        self.assertIsNone(plugin.on_llm_request(session_id='other-session', request=request, provider='openai-codex'))

    def test_disabled_session_is_not_applied(self):
        plugin.remember(decision(), 'auto')
        plugin._SESSIONS['disabled'] = {'enabled': False, 'decision': decision(), 'routing_mode': 'auto'}
        self.assertIsNone(plugin.on_llm_request(session_id='disabled', request={'model': 'old'}, provider='openai-codex'))

    def test_new_turn_does_not_borrow_previous_sessions_pin(self):
        plugin._LAST.update(mode='auto', pin={'provider': 'openai-codex', 'model': 'wrong-model'})
        plugin.on_pre_llm_call(session_id='fresh', user_message='Hello', turn_id='one')
        self.assertNotIn('pin', plugin._SESSIONS['fresh'])

    def test_child_does_not_invoke_main_router(self):
        with patch.object(plugin, '_pre_llm_call') as classify_hook:
            plugin.on_pre_llm_call(session_id='child', parent_session_id='parent', user_message='Child task')
        classify_hook.assert_not_called()
        self.assertIsNone(plugin.on_llm_request(session_id='child', request={'model': 'old'}, provider='openai-codex'))

    def test_stale_turn_cannot_rewrite_next_turn(self):
        plugin._SESSIONS['one'] = {'decision': decision(), 'routing_mode': 'auto', 'turn_id': 'previous'}
        self.assertIsNone(plugin.on_llm_request(session_id='one', turn_id='next', request={'model': 'old'}, provider='openai-codex'))

    def test_reasoning_object_keeps_wire_shape(self):
        request = {'model': 'old', 'reasoning': {'effort': 'low', 'summary': 'auto'}}
        result = rewrite(request, decision(), 'openai-codex')
        self.assertEqual(result['reasoning'], {'effort': 'high', 'summary': 'auto'})
        self.assertEqual(request['reasoning']['effort'], 'low')

    def test_same_model_does_not_block_effort_routing(self):
        from jev_local_safety.route import BoundModel, sticky_keep
        config = Mock(stickiness=True)
        self.assertFalse(sticky_keep(config, BoundModel('openai-codex', 'gpt-6.1-sol'), decision()))

    def test_classifier_sends_current_task_only(self):
        seen = {}
        def capture(url, headers, body, timeout):
            seen.update(body)
            return {'answers': {'task_kind': {'choice': 'chat', 'confidence': 1}}}
        classify('Explain git branches', api_key='sentinel-key', endpoint='https://api.typesafe.ai/v1/systemone', jev_model='jev-latest', task_kinds={'chat': 'general'}, history='PRIVATE HISTORY', active_model='PRIVATE MODEL', spend={'today': 9}, transport=capture)
        self.assertEqual(seen['state'], {'request': 'Explain git branches'})

    def test_secret_tasks_never_leave_machine(self):
        for prompt in ['password=do-not-transmit', 'Authorization: Bearer do-not-transmit', '-----BEGIN PRIVATE KEY-----', 'My key is sk-do-not-transmit-1234567890', 'sentinel-key']:
            with self.subTest(prompt=prompt):
                transport = Mock()
                with self.assertRaises(JevError):
                    classify(prompt, api_key='sentinel-key', endpoint='https://api.typesafe.ai/v1/systemone', jev_model='jev-latest', task_kinds={'chat': 'general'}, transport=transport)
                transport.assert_not_called()

    def test_credential_shapes_never_leave_machine(self):
        prompts = [
            'OPENAI_API_KEY=abc123xyz',
            'access_token=abc',
            'my password is hunter2',
            'api key: x',
            'AKIAIOSFODNN7EXAMPLE',
            'slack xoxb-1234567890-abcdefghij',
            'google AIzaSyD-abcdefghijklmnopqrstuvwxyz0123',
            'Bearer abcdefghijklmnop',
            'clone https://deploy:hunter2@git.example.com/repo.git',
            'ghp_abcdefghijklmnopqrstuvwxyz0123',
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                transport = Mock()
                with self.assertRaises(JevError):
                    classify(prompt, api_key='sentinel-key', endpoint='https://api.typesafe.ai/v1/systemone', jev_model='jev-latest', task_kinds={'chat': 'general'}, transport=transport)
                transport.assert_not_called()

    def test_authorization_never_follows_redirect(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                seen.append((self.path, self.headers.get('Authorization')))
                if self.path == '/start':
                    self.send_response(302)
                    self.send_header('Location', f'http://localhost:{self.server.server_port}/leak')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaises(JevError):
            urllib_transport(f'http://127.0.0.1:{server.server_port}/start', {'Authorization': 'Bearer sentinel-key'}, {'state': {}}, 2)
        self.assertNotIn('/leak', [path for path, _ in seen])
        self.assertFalse(any(auth and 'sentinel-key' in auth for path, auth in seen if path != '/start'))

    def test_empty_session_never_borrows_last_route(self):
        plugin.remember(decision(), 'auto')
        self.assertIsNone(plugin.on_llm_request(session_id='', request={'model': 'old-model'}, provider='openai-codex'))

    def test_api_key_never_sent_to_configurable_third_party(self):
        transport = Mock()
        with self.assertRaises(JevError):
            classify('Explain branches', api_key='sentinel-key', endpoint='https://attacker.invalid', jev_model='jev-latest', task_kinds={'chat': 'general'}, transport=transport)
        transport.assert_not_called()

    def test_prompt_is_bounded(self):
        seen = {}
        def capture(url, headers, body, timeout):
            seen.update(body)
            return {'answers': {'task_kind': {'choice': 'chat', 'confidence': 1}}}
        classify('x' * 10000, api_key='sentinel-key', endpoint='https://api.typesafe.ai/v1/systemone', jev_model='jev-latest', task_kinds={'chat': 'general'}, transport=capture)
        self.assertLessEqual(len(seen['state']['request']), 4000)

    def test_low_confidence_preserves_original_route(self):
        from jev_local_safety.decide import Analysis
        config = Mock(confidence_threshold=.65, enabled=True, min_prompt_chars=0)
        settings = Mock(history_turns=0, endpoint_env='', endpoint='https://api.typesafe.ai/v1/systemone', jev_model='jev-latest', timeout_ms=3000, task_kinds={'chat': 'general'})
        plugin._CTX = Mock()
        plugin._CTX.get_config.side_effect = lambda key, default=None: {'mode': 'auto', 'config_path': '/not-read.json'}.get(key, default)
        with patch.object(plugin, '_load', return_value=(config, settings)), patch.object(plugin, 'apply_setting_overrides', return_value=(config, settings)), patch.object(plugin, '_spend', return_value=None), patch.object(plugin, 'classify', return_value=Analysis('chat', 1, 1, .2, .1)), patch.object(plugin, 'decide') as decide_mock:
            plugin.on_pre_llm_call(session_id='main', user_message='Explain a git branch', turn_id='one')
        decide_mock.assert_not_called()
        self.assertIsNone(plugin.on_llm_request(session_id='main', turn_id='one', request={'model': 'old'}, provider='openai-codex'))


if __name__ == '__main__':
    unittest.main()
