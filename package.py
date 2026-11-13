#!/usr/bin/env python3
"""Build a portable source bundle, excluding all machine-local state."""
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = ('autodl.py', 'server.py', 'remote_probe.py', 'experiment_names.py',
                 'experiment_history.py',
                 'swanlab_bridge.py', 'swanlab_config.py', 'install.py', 'install-key.exp',
                 'requirements.txt', 'README.md', 'README_EN.md', 'package.py', '.gitignore')


def build(destination=None):
    destination = Path(destination) if destination else ROOT / 'dist' / 'autodl-swanlab-dashboard.zip'
    files = [ROOT / name for name in RUNTIME_FILES]
    files += sorted((ROOT / 'static').glob('*'))
    files += sorted((ROOT / 'examples').glob('*.json'))
    for source in files:
        if not source.is_file() or source.is_symlink():
            raise ValueError('Missing or invalid source file: ' + source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for source in files:
            bundle.write(source, 'autodl-swanlab-dashboard/' + source.relative_to(ROOT).as_posix())
    return destination


if __name__ == '__main__':
    print(build())
