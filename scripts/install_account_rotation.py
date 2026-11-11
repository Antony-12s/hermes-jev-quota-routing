"""Install the owning account-pool plugin and its Linux user timer.

Use the existing unit names, back up replaced units, and never modify credentials
or other profiles. Unsupported platforms keep routing but get an explicit warning.
"""
import json
import os
from pathlib import Path
import shutil
import shlex
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
        existing = service_path.read_text(encoding='utf-8')
        # Accept both the native plugin's unquoted home and this installer's quoted home.
        unit_home = Path.home() / '.hermes'
        for line in existing.splitlines():
            if line.strip().startswith('Environment='):
                for assignment in shlex.split(line.strip().partition('=')[2]):
                    if assignment.startswith('HERMES_HOME='):
                        unit_home = Path(assignment.partition('=')[2].replace('%%', '%'))
        if unit_home.expanduser().resolve() != home:
            raise RuntimeError('Existing account-balance timer belongs to another Hermes home; leaving it unchanged.')

    if not (home / 'plugins/account-pool/plugin.yaml').exists():
        run(hermes, '--profile', 'default', 'plugins', 'install', 'account-pool', '--enable', '--yes-deps')
    else:
        run(hermes, '--profile', 'default', 'plugins', 'enable', 'account-pool')
    for provider in ('anthropic', 'openai-codex'):
        run(hermes, '--profile', 'default', 'config', 'set', f'credential_pool_strategies.{provider}', 'fill_first')

    config = {
        service_path: (
            '[Unit]\nDescription=Balance Hermes accounts before quota exhaustion\n\n'
            '[Service]\nType=oneshot\n'
            f'Environment={quote("HERMES_HOME=" + str(home))}\n'
            f'ExecStart=:{quote(hermes)} --profile default account all balance --threshold 95\n'
            'TimeoutStartSec=600\n'
        ),
        timer_path: (
            '[Unit]\nDescription=Check Hermes account quota every minute\n\n'
            '[Timer]\nOnStartupSec=1min\nOnUnitActiveSec=1min\nAccuracySec=5s\n\n'
            '[Install]\nWantedBy=timers.target\n'
        ),
    }
    changed = {path: content for path, content in config.items()
               if not path.exists() or path.read_text(encoding='utf-8') != content}
    existing_paths = [path for path in changed if path.exists()]
    if existing_paths:
        backup_root = home / 'backups'
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='account-rotation-', dir=backup_root))
        for path in existing_paths:
            shutil.copy2(path, backup / path.name)
        print(f'Previous account-balance units backed up to {backup}')
    units.mkdir(parents=True, exist_ok=True)
    for path, content in changed.items():
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=units, delete=False) as pending:
            pending.write(content)
        os.replace(pending.name, path)
    run('systemctl', '--user', 'daemon-reload')
    run('systemctl', '--user', 'enable', '--now', f'{UNIT}.timer')
    run('systemctl', '--user', 'restart', f'{UNIT}.timer')
    first_run = subprocess.run(['systemctl', '--user', 'start', f'{UNIT}.service'])
    run('systemctl', '--user', 'is-active', '--quiet', f'{UNIT}.timer')
    if first_run.returncode:
        print('WARNING: the first balance run failed; the timer is active and will retry. '
              'Check journalctl --user -u hermes-account-balance.service.')
    print('Account rotation timer enabled: check every minute; bench at 95% used (5% left) when a spare is usable.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Account rotation setup failed: {error}', file=sys.stderr)
        sys.exit(1)
