"""Exercise account-rotation setup with isolated homes and fake external CLIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('install_account_rotation.py')
INSTALLER = SCRIPT.parents[1] / 'install.sh'


class RotationInstallTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.home = self.root / 'home'
        self.hermes_home = self.home / '.hermes'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'commands.jsonl'
        for name in ('hermes', 'systemctl'):
            executable = self.bin / name
            executable.write_text(
                f'#!{sys.executable}\n'
                'import json, os, sys\n'
                'from pathlib import Path\n'
                'with open(os.environ["COMMAND_LOG"], "a") as log:\n'
                '    log.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + "\\n")\n'
                'if Path(sys.argv[0]).name == "systemctl" and os.environ.get("NO_MANAGER"):\n'
                '    sys.exit(1)\n'
                'args = sys.argv[1:]\n'
                'if args[:1] == ["--profile"]:\n'
                '    args = args[2:]\n'
                'if args[:2] == ["plugins", "install"] and os.environ.get("FAIL_INSTALL"):\n'
                '    sys.exit(1)\n'
                'if args == ["--user", "start", "hermes-account-balance.service"] and os.environ.get("FAIL_BALANCE"):\n'
                '    sys.exit(1)\n'
            )
            executable.chmod(0o755)
        self.env = {
            **os.environ, 'HOME': str(self.home), 'HERMES_HOME': str(self.hermes_home),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'PATH': f'{self.bin}:{os.environ["PATH"]}', 'COMMAND_LOG': str(self.log),
        }
        self.env.pop('NO_MANAGER', None)
        self.env.pop('FAIL_INSTALL', None)
        self.units = self.home / '.config/systemd/user'

    def run_setup(self):
        return subprocess.run([sys.executable, str(SCRIPT)], env=self.env, text=True,
                              capture_output=True, timeout=20)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_installs_dependency_and_starts_one_minute_timer_at_95_percent(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        service = (self.units / 'hermes-account-balance.service').read_text()
        timer = (self.units / 'hermes-account-balance.timer').read_text()
        self.assertIn('account all balance --threshold 95', service)
        self.assertIn('OnUnitActiveSec=1min', timer)
        self.assertIn('AccuracySec=5s', timer)
        commands = self.commands()
        self.assertIn(['hermes', '--profile', 'default', 'plugins', 'install', 'account-pool', '--enable', '--yes-deps'], commands)
        self.assertIn(['systemctl', '--user', 'enable', '--now', 'hermes-account-balance.timer'], commands)
        self.assertIn(['systemctl', '--user', 'start', 'hermes-account-balance.service'], commands)
        self.assertIn(['systemctl', '--user', 'is-active', '--quiet', 'hermes-account-balance.timer'], commands)

    def test_existing_plugin_is_enabled_without_reinstallation(self):
        plugin = self.hermes_home / 'plugins/account-pool/plugin.yaml'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('name: account-pool\n')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['hermes', '--profile', 'default', 'plugins', 'enable', 'account-pool'], self.commands())
        self.assertFalse(any(command[3:5] == ['plugins', 'install'] for command in self.commands()))

    def test_rerun_backs_up_old_units(self):
        self.units.mkdir(parents=True)
        service = self.units / 'hermes-account-balance.service'
        service.write_text('old service\n')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list((self.hermes_home / 'backups').glob('account-rotation-*/hermes-account-balance.service'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'old service\n')
        self.assertEqual(self.run_setup().returncode, 0)
        self.assertEqual(len(list(self.units.glob('*.timer'))), 1)

    def test_failed_dependency_does_not_write_units(self):
        self.env['FAIL_INSTALL'] = '1'
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.units.exists())

    def test_unavailable_user_manager_is_reported_without_installing(self):
        self.env['NO_MANAGER'] = '1'
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('not configured', result.stdout.lower())
        self.assertFalse(self.units.exists())
        self.assertFalse(any(command[0] == 'hermes' for command in self.commands()))

    def test_custom_home_and_executable_paths_are_escaped(self):
        self.env['HERMES_HOME'] = str(self.home / 'custom space%home')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        service = (self.units / 'hermes-account-balance.service').read_text()
        self.assertIn('Environment="HERMES_HOME=', service)
        self.assertIn('custom space%%home', service)
        self.assertIn(f'ExecStart=:{json.dumps(str(self.bin / "hermes"))}', service)

    def test_named_profiles_do_not_change_the_shared_timer(self):
        self.env['HERMES_HOME'] = str(self.home / '.hermes/profiles/work')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('default profile', result.stdout)
        self.assertFalse(self.units.exists())

    def test_sticky_profile_cannot_redirect_commands_or_timer(self):
        self.hermes_home.mkdir(parents=True)
        (self.hermes_home / 'active_profile').write_text('work\n')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [command for command in self.commands() if command[0] == 'hermes']
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(command[1:3], ['--profile', 'default'])
        service = (self.units / 'hermes-account-balance.service').read_text()
        self.assertIn('--profile default account all balance --threshold 95', service)

    def test_main_installer_pins_the_intended_profile(self):
        self.hermes_home.mkdir(parents=True)
        (self.hermes_home / 'active_profile').write_text('work\n')
        result = subprocess.run(['bash', str(INSTALLER)], env=self.env, text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in self.commands():
            if command[0] == 'hermes':
                self.assertEqual(command[1:3], ['--profile', 'default'])

    def test_native_custom_home_units_can_be_reinstalled(self):
        custom = self.home / 'custom'
        self.env['HERMES_HOME'] = str(custom)
        self.units.mkdir(parents=True)
        (self.units / 'hermes-account-balance.service').write_text(
            f'[Service]\nEnvironment=HERMES_HOME={custom}\nExecStart=/bin/true\n')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_balance_failure_keeps_instructions_and_warns(self):
        self.env['FAIL_BALANCE'] = '1'
        result = subprocess.run(['bash', str(INSTALLER)], env=self.env, text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('first balance run failed', result.stdout.lower())
        self.assertIn('Still to do by hand', result.stdout)

    def test_unchanged_units_do_not_create_more_backups(self):
        self.assertEqual(self.run_setup().returncode, 0)
        self.assertEqual(self.run_setup().returncode, 0)
        self.assertFalse((self.hermes_home / 'backups').exists())

    def test_other_home_units_are_not_overwritten(self):
        self.units.mkdir(parents=True)
        service = self.units / 'hermes-account-balance.service'
        original = 'Environment="HERMES_HOME=/another/home"\n'
        service.write_text(original)
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(service.read_text(), original)
        self.assertFalse(any(command[0] == 'hermes' for command in self.commands()))

    def test_main_installer_configures_rotation(self):
        result = subprocess.run(['bash', str(INSTALLER)], env=self.env, text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.units / 'hermes-account-balance.timer').exists(), result.stdout)


if __name__ == '__main__':
    unittest.main()
