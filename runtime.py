"""运行数据与程序文件分离：升级不覆盖用户的历史记录。"""
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APP_ID = 'io.github.spiritn.tokenmonitor'
BASE = Path(__file__).resolve().parent
VERSION = (BASE / 'VERSION').read_text().strip()


def data_dir():
    return Path(os.environ.get('TOKEN_MONITOR_DATA_DIR', Path.home() / 'Library/Application Support/TokenMonitor')).expanduser().resolve()


def config():
    path = data_dir() / 'config.json'
    settings = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('config.json 必须是 JSON 对象')
    port = settings.get('port', 8787)
    timezone = settings.get('timezone', 'Asia/Shanghai')
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError('port 必须是 1024—65535 的整数')
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError('timezone 必须是有效的时区名，例如 Asia/Shanghai') from None
    return dict(port=port, timezone=timezone)


def prepare_data():
    os.umask(0o077)
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def save_config(settings):
    root = prepare_data()
    temp = root / 'config.json.tmp'
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n')
    temp.replace(root / 'config.json')
