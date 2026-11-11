"""Stage both bundled Desktop halves where Hermes actually loads plugin.js.

Package markers keep Hermes' explicit Desktop opt-in intact. Do not edit the
app's localStorage/LevelDB or turn a user's disabled plugin back on.
"""
import json
from pathlib import Path
import shutil
import sys


PLUGINS = ('hermes-jev-routing', 'jev-map')


def install(home: Path, backup: Path):
    home = home.expanduser().resolve()
    for name in PLUGINS:
        source = home / 'plugins' / name / 'desktop/plugin.js'
        if not source.is_file():
            raise RuntimeError(f'Missing bundled Desktop source: {name}')
        target = home / 'desktop-plugins' / name
        if target.is_symlink() or any((target / filename).is_symlink()
                                      for filename in ('plugin.js', '.hermes-package.json')):
            raise RuntimeError(f'Desktop plugin {name} is symlinked; leaving it unchanged.')
    for name in PLUGINS:
        source = home / 'plugins' / name / 'desktop/plugin.js'
        target = home / 'desktop-plugins' / name
        metadata = {'package': name, 'source': str(source.parent),
                    'sourceMtimeMs': source.stat().st_mtime * 1000}
        marker = json.dumps(metadata, indent=2) + '\n'
        changed = ((target / 'plugin.js').exists() and (target / 'plugin.js').read_bytes() != source.read_bytes())
        old_marker = target / '.hermes-package.json'
        changed = changed or (old_marker.exists() and old_marker.read_text(encoding='utf-8') != marker)
        if target.exists() and changed:
            shutil.copytree(target, backup / 'desktop-plugins' / name, dirs_exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / 'plugin.js')
        old_marker.write_text(marker, encoding='utf-8')
        print(f'Staged Desktop plugin: {name}')
    print('After restarting Hermes Desktop, enable BOTH jev-map and hermes-jev-routing in Settings > Plugins.')
    print('Open the Jev map pane to see the live routing monitor; Jev on/off appears beside Send in a chat.')


if __name__ == '__main__':
    try:
        install(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, RuntimeError) as error:
        print(f'Desktop plugin staging failed: {error}', file=sys.stderr)
        sys.exit(1)
