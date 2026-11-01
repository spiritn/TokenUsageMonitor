#!/usr/bin/env python3
"""单用户安装、启动与诊断；无需 sudo，不修改宿主 Agent 的配置。"""
import argparse
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from runtime import APP_ID, BASE, VERSION, config, data_dir, prepare_data, save_config

LABELS = (APP_ID + '.daemon', APP_ID + '.panel')


def agents_dir():
    return Path(os.environ.get('TOKEN_MONITOR_LAUNCH_AGENTS_DIR', Path.home() / 'Library/LaunchAgents'))


def launchctl(*args, check=False):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, check=check)


def domain():
    return 'gui/' + str(os.getuid())


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def service_plists(settings, web_only=False):
    root = data_dir()
    env = {'TOKEN_MONITOR_DATA_DIR': str(root), 'TOKEN_MONITOR_TIMEZONE': settings['timezone']}
    specs = {}
    app = BASE / '.build/Token Monitor.app/Contents/MacOS/TokenMonitor'
    commands = [[sys.executable, str(BASE / 'daemon.py')], [str(app)]]
    for i, label in enumerate(LABELS):
        if i == 1 and web_only:
            continue
        specs[label] = dict(Label=label, ProgramArguments=commands[i], RunAtLoad=True,
                            EnvironmentVariables=env, Umask=0o077,
                            StandardOutPath=str(root / ('daemon.log' if i == 0 else 'panel.log')),
                            StandardErrorPath=str(root / ('daemon.log' if i == 0 else 'panel.log')))
        if i == 0:
            specs[label]['KeepAlive'] = True
    return specs


def stop(remove=False):
    for label in reversed(LABELS):
        launchctl('bootout', domain() + '/' + label)
        path = agents_dir() / (label + '.plist')
        if remove and path.exists():
            path.unlink()


def start(args):
    settings = config()
    if args.port is not None:
        settings['port'] = args.port
        if not 1024 <= args.port <= 65535:
            raise ValueError('端口必须在 1024—65535 之间')
    app = BASE / '.build/Token Monitor.app/Contents/MacOS/TokenMonitor'
    if not args.web_only and not app.exists():
        raise ValueError('原生面板尚未构建：运行 sh scripts/build-macos.sh，或使用 start --web-only')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', settings['port']))
        except OSError:
            if health(settings['port']).get('app_id') != APP_ID:
                raise ValueError(f"端口 {settings['port']} 已被其他程序占用；使用 start --port 8788")
    prepare_data()
    stop()
    save_config(settings)
    agents_dir().mkdir(parents=True, exist_ok=True)
    try:
        for label, spec in service_plists(settings, args.web_only).items():
            path = agents_dir() / (label + '.plist')
            temp = path.with_suffix('.tmp')
            temp.write_bytes(plistlib.dumps(spec)); temp.replace(path)
            launchctl('bootstrap', domain(), str(path), check=True)
        for _ in range(50):
            if health(settings['port']).get('app_id') == APP_ID:
                break
            time.sleep(.1)
        else:
            raise ValueError('后台未能启动；请运行 token-monitor doctor，查看本地 daemon.log')
    except Exception:
        stop()
        raise
    print(f"已启动并设置登录启动：http://127.0.0.1:{settings['port']}")
    if args.web_only and not args.no_open:
        webbrowser.open(f"http://127.0.0.1:{settings['port']}")


def doctor():
    settings = config()
    print('Token Monitor', VERSION)
    print('Python:', sys.version.split()[0], sys.executable)
    print('数据目录:', data_dir())
    print('界面:', f"http://127.0.0.1:{settings['port']}")
    result = health(settings['port'])
    print('后台:', '在线' if result.get('app_id') == APP_ID else '未运行或端口被其他程序占用')
    print('原生面板:', '已构建' if (BASE / '.build/Token Monitor.app').exists() else '未构建（可使用网页模式）')
    print('时区:', settings['timezone'])
    print('隐私: 无账户接口、无遥测；采集数据仅保存在本机。')
    return result


def uninstall():
    stop(remove=True)
    marker = BASE / '.token-monitor-install.json'
    if marker.exists():
        manifest = json.loads(marker.read_text())
        if manifest.get('app_id') != APP_ID or Path(manifest.get('directory', '')).resolve() != BASE:
            raise ValueError('安装标记不匹配，保留程序文件以避免误删')
        launcher = Path(manifest['launcher'])
        if launcher.is_file() and APP_ID in launcher.read_text():
            launcher.unlink()
        shutil.rmtree(BASE)
    print('已停止服务并移除登录启动。历史数据保留在：', data_dir())
    print('Homebrew 安装的程序请再运行 brew uninstall token-monitor。')


def main():
    parser = argparse.ArgumentParser(prog='token-monitor', description='多工具 Token 用量，本机查看。')
    parser.add_argument('--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('start', help='启动并设置登录启动')
    command.add_argument('--port', type=int)
    command.add_argument('--web-only', action='store_true')
    command.add_argument('--no-open', action='store_true')
    for name, description in [('stop', '停止采集与面板'), ('status', '查看运行状态'),
                              ('open', '打开网页'), ('doctor', '本地诊断'), ('uninstall', '卸载并保留历史数据')]:
        sub.add_parser(name, help=description)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('首版仅支持 macOS')
    try:
        if args.command == 'start': start(args)
        elif args.command == 'stop': stop(); print('已停止；下次登录仍会启动。卸载登录启动请使用 uninstall。')
        elif args.command == 'uninstall': uninstall()
        elif args.command == 'open': webbrowser.open(f"http://127.0.0.1:{config()['port']}")
        else: doctor()
    except (OSError, ValueError, subprocess.SubprocessError) as ex:
        print('未完成：', str(ex), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
