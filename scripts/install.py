#!/usr/bin/env python3
"""按白名单安装程序；数据独立保存，不复制源码目录里的用户文件。"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
FILES = ('VERSION', 'runtime.py', 'cli.py', 'daemon.py', 'meter.py', 'adapters.py',
         'web/index.html', 'native/Info.plist', 'native/TokenMonitor.swift', 'scripts/build-macos.sh')
APP_ID = 'io.github.spiritn.tokenmonitor'


def install(destination, bin_dir, web_only=False, no_start=False):
    destination = destination.expanduser().resolve(); bin_dir = bin_dir.expanduser().resolve()
    def owned(directory):
        try:
            marker = json.loads((directory / '.token-monitor-install.json').read_text())
            return marker.get('app_id') == APP_ID and marker.get('directory') == str(destination)
        except (OSError, ValueError, AttributeError):
            return False
    if destination == SOURCE or SOURCE.is_relative_to(destination):
        raise ValueError('安装目录不能覆盖源码或其上级目录')
    if destination.exists() and not owned(destination):
        raise ValueError('目标目录已有非本程序的内容，请设置 TOKEN_MONITOR_INSTALL_DIR')
    destination.parent.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / 'token-monitor'
    if launcher.exists() and APP_ID not in launcher.read_text():
        raise ValueError('token-monitor 命令已存在；请设置 TOKEN_MONITOR_BIN_DIR')
    stage = Path(tempfile.mkdtemp(prefix='.token-monitor-', dir=destination.parent))
    previous = destination.with_name(destination.name + '.previous')
    try:
        for name in FILES:
            target = stage / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / name, target)
        if not web_only:
            subprocess.run(['/bin/sh', str(stage / 'scripts/build-macos.sh')], check=True)
        manifest = dict(app_id=APP_ID, directory=str(destination), launcher=str(launcher))
        (stage / '.token-monitor-install.json').write_text(json.dumps(manifest) + '\n')
        if previous.exists():
            if not owned(previous):
                raise ValueError('旧备份目录不属于本程序，保留现场')
            shutil.rmtree(previous)
        if destination.exists(): destination.rename(previous)
        stage.rename(destination)
        bin_dir.mkdir(parents=True, exist_ok=True)
        command = '#!/bin/sh\n# ' + APP_ID + '\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(destination / 'cli.py')) + ' "$@"\n'
        temp = launcher.with_suffix('.tmp'); temp.write_text(command); temp.chmod(0o755); temp.replace(launcher)
        print('程序已安装：', destination)
        print('命令：', launcher)
        print('以后升级可重新运行安装命令，历史数据不会被覆盖。')
        if not no_start:
            args = [sys.executable, str(destination / 'cli.py'), 'start']
            if web_only: args.append('--web-only')
            subprocess.run(args, check=True)
        print('若终端找不到命令，请将此目录加入 PATH：', bin_dir)
    finally:
        if stage.exists(): shutil.rmtree(stage)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-only', action='store_true')
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'darwin': parser.error('首版仅支持 macOS')
    if os.geteuid() == 0: parser.error('请使用当前用户安装，无需 sudo')
    install(Path(os.environ.get('TOKEN_MONITOR_INSTALL_DIR', Path.home() / '.local/share/token-monitor')),
            Path(os.environ.get('TOKEN_MONITOR_BIN_DIR', Path.home() / '.local/bin')), args.web_only, args.no_start)
