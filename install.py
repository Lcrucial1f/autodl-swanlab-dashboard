#!/usr/bin/env python3
"""Install a per-user launcher while keeping account data outside the project."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parent
MARKER = '# AutoDL dashboard launcher v1'


def launcher_text(interpreter, root=ROOT):
    return ('#!/bin/sh\n' + MARKER + '\n'
            + 'exec ' + shlex.quote(str(interpreter)) + ' -B '
            + shlex.quote(str(root / 'autodl.py')) + ' "$@"\n')


def install(bin_dir, skip_deps=False):
    if sys.version_info < (3, 9):
        raise ValueError('需要 Python 3.9 或更新版本')
    if os.name != 'posix':
        raise ValueError('当前支持 macOS、Linux 与 Windows WSL')
    target = Path(bin_dir).expanduser().resolve() / 'autodl'
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise ValueError('目标已有其他入口，请使用 --bin-dir 选择空目录：' + str(target))
        if MARKER not in target.read_text(errors='replace'):
            raise ValueError('目标已有其他 autodl 命令，未覆盖。请使用 --bin-dir 选择空目录：' + str(target))
    interpreter = Path(sys.executable).resolve()
    if not skip_deps:
        environment = ROOT / '.venv'
        interpreter = environment / 'bin/python'
        if not interpreter.exists():
            print('正在创建项目 Python 环境…', flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
        print('正在安装 SwanLab 依赖…', flush=True)
        subprocess.run([str(interpreter), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(launcher_text(interpreter))
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return target


def main():
    parser = argparse.ArgumentParser(description='安装 AutoDL 本地命令；账号与实例配置保持独立')
    parser.add_argument('--bin-dir', default=str(Path.home() / '.local/bin'), help='命令入口目录，默认 ~/.local/bin')
    parser.add_argument('--skip-deps', action='store_true', help='跳过 SwanLab 环境安装；仍可使用 SSH 与 GPU 看板')
    args = parser.parse_args()
    target = install(args.bin_dir, args.skip_deps)
    print('安装完成：' + str(target))
    directories = [Path(p).expanduser().resolve() for p in os.environ.get('PATH', '').split(os.pathsep) if p]
    if target.parent not in directories:
        print('当前 PATH 未包含命令目录，可先运行：')
        print('  export PATH=' + shlex.quote(str(target.parent)) + ':"$PATH"')
    print('打开首次配置：' + shlex.quote(str(target)) + ' setup')
    print('项目移动后请重新运行安装程序以更新入口。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('安装失败：' + str(error), file=sys.stderr)
        sys.exit(1)
