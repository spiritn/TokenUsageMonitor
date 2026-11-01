#!/usr/bin/env python3
"""仅检查将被提交的文件；发现风险时只输出位置与类型，不打印疑似密钥。"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'api-key': re.compile(r'\bsk-[A-Za-z0-9_-]{24,}'),
    'jwt': re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    'personal-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'private-email': re.compile(r'\b[A-Za-z0-9_.+-]+@(?:163|126|qq|gmail|outlook)\.com\b'),
}
DENIED = {'config.json', 'prices.json', 'state.json', 'deployment.json', '.env', '.DS_Store'}


def files():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    return [Path(name) for name in result.stdout.decode().split('\0') if name]


def inspect(paths):
    issues = []
    for relative in paths:
        path = ROOT / relative
        if path.is_symlink() or relative.name in DENIED or relative.name.startswith(('.env', '.token-monitor-install')) or any(x in relative.parts for x in ('backups', '.build', 'dist', '__pycache__')) or any(x.endswith('.app') for x in relative.parts) or re.search(r'\.(?:db|sqlite)(?:[-.].*)?$', relative.name) or path.suffix in ('.log', '.pem', '.key', '.p12', '.heic', '.HEIC', '.jpg', '.jpeg'):
            issues.append((str(relative), 0, 'private-file')); continue
        if path.suffix == '.png':
            if relative.parts[:2] != ('docs', 'images'):
                issues.append((str(relative), 0, 'unexpected-image'))
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            issues.append((str(relative), 0, 'unexpected-binary')); continue
        for line, value in enumerate(text.splitlines(), 1):
            for kind, pattern in PATTERNS.items():
                if pattern.search(value): issues.append((str(relative), line, kind))
    return issues


def main():
    paths = files()
    if not paths: raise SystemExit('没有待发布文件；请先在隔离的仓库中 git add。')
    issues = inspect(paths)
    for path, line, kind in issues: print(f'{path}:{line}: {kind}')
    if issues: raise SystemExit(1)
    print(f'已检查 {len(paths)} 个待发布文件：未发现匹配的密钥、个人路径或运行数据。')


if __name__ == '__main__': main()
