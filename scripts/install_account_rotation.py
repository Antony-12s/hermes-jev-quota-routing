"""Install the owning account-pool plugin and its Linux user timer.

Use the existing unit names, back up replaced units, and never modify credentials
or other profiles. Unsupported platforms keep routing but get an explicit warning.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


UNIT = 'hermes-account-balance'


def main():
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser().resolve()
    if home.parent.name == 'profiles':
        print('Account rotation not configured: run this setup from the default profile.')
        return
    if sys.platform != 'linux' or not shutil.which('systemctl'):
        print('Account rotation not configured: automatic setup requires Linux with systemd --user.')
        return
    manager = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True)
    if manager.returncode:
        print('Account rotation not configured: systemd --user is unavailable in this session.')
        return
    hermes = shutil.which('hermes')
    if not hermes:
        raise RuntimeError('hermes CLI not found; install Hermes Agent first.')

    def run(*args):
        subprocess.run(args, check=True)

    def quote(value):
        # systemd specifiers also expand inside quotes; ':' disables ExecStart environment expansion.
        return json.dumps(str(value).replace('%', '%%'), ensure_ascii=False)

    units = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'systemd/user'
    service_path = units / f'{UNIT}.service'
    timer_path = units / f'{UNIT}.timer'
    if any(path.is_symlink() for path in (service_path, timer_path)):
        raise RuntimeError('Existing account-balance units are symlinks; manage them with their original installer.')
    if service_path.exists():
        existing = service_path.read_text()
        # A user has one account-pool timer. Do not redirect another home's shared timer.
        expected_env = f'Environment={quote("HERMES_HOME=" + str(home))}'
        default_home = (Path.home() / '.hermes').resolve()
        other_home = ('Environment=HERMES_HOME=' in existing or 'Environment="HERMES_HOME=' in existing)
        if (other_home and expected_env not in existing) or (not other_home and home != default_home):
            raise RuntimeError('Existing account-balance timer belongs to another Hermes home; leaving it unchanged.')

    if not (home / 'plugins/account-pool/plugin.yaml').exists():
        run(hermes, 'plugins', 'install', 'account-pool', '--enable', '--yes-deps')
    else:
        run(hermes, 'plugins', 'enable', 'account-pool')
    for provider in ('anthropic', 'openai-codex'):
        run(hermes, 'config', 'set', f'credential_pool_strategies.{provider}', 'fill_first')

    config = {
        service_path: (
            '[Unit]\nDescription=Balance Hermes accounts before quota exhaustion\n\n'
            '[Service]\nType=oneshot\n'
            f'Environment={quote("HERMES_HOME=" + str(home))}\n'
            f'ExecStart=:{quote(hermes)} account all balance --threshold 95\n'
            'TimeoutStartSec=600\n'
        ),
        timer_path: (
            '[Unit]\nDescription=Check Hermes account quota every minute\n\n'
            '[Timer]\nOnStartupSec=1min\nOnUnitActiveSec=1min\nAccuracySec=5s\n\n'
            '[Install]\nWantedBy=timers.target\n'
        ),
    }
    existing_paths = [path for path in config if path.exists()]
    if existing_paths:
        backup_root = home / 'backups'
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='account-rotation-', dir=backup_root))
        for path in existing_paths:
            shutil.copy2(path, backup / path.name)
        print(f'Previous account-balance units backed up to {backup}')
    units.mkdir(parents=True, exist_ok=True)
    for path, content in config.items():
        path.write_text(content)
    run('systemctl', '--user', 'daemon-reload')
    run('systemctl', '--user', 'enable', '--now', f'{UNIT}.timer')
    run('systemctl', '--user', 'restart', f'{UNIT}.timer')
    run('systemctl', '--user', 'start', f'{UNIT}.service')
    run('systemctl', '--user', 'is-active', '--quiet', f'{UNIT}.timer')
    print('Account rotation enabled: check every minute; bench at 95% used (5% left) when a spare is usable.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Account rotation setup failed: {error}', file=sys.stderr)
        sys.exit(1)
