#!/usr/bin/env python3
"""只读本地 Agent 日志；不调用供应商、浏览器或账户接口。"""
import argparse
import fcntl
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import meter
from meter import Meter, connect, overview, sources
from runtime import APP_ID, BASE, VERSION, config, prepare_data

DB = None
SOURCE_HOME = Path.home()


def scan_loop(db, home, prices):
    collector = Meter(db, home, prices=prices)
    while True:
        try:
            collector.scan()
        except Exception as ex:
            # 不记录异常正文，避免第三方日志中的敏感字段进入本程序日志。
            print('[scan]', type(ex).__name__, flush=True)
        time.sleep(5)


class API(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, data, kind='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = self.headers.get('Host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):
            return self.send(403, {'error': 'localhost only'})
        url = urlparse(self.path)
        if url.path in ('/', '/index.html'):
            return self.send(200, (BASE / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/api/health':
            return self.send(200, dict(app_id=APP_ID, version=VERSION))
        if url.path not in ('/api/v2/overview', '/api/sources'):
            return self.send(404, {'error': 'not found'})
        conn = None
        try:
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            conn = connect(DB, readonly=True)
            body = sources(conn, SOURCE_HOME) if url.path == '/api/sources' else overview(
                conn, period=q.get('period', 'day'), tool=q.get('tool'),
                start=q.get('start'), end=q.get('end'), home=SOURCE_HOME)
            self.send(200, body)
        except (ValueError, TypeError):
            self.send(400, {'error': '查询参数无效'})
        except Exception as ex:
            print('[api]', type(ex).__name__, flush=True)
            self.send(500, {'error': '查询失败'})
        finally:
            if conn is not None:
                conn.close()


def main():
    global DB, SOURCE_HOME
    settings = config()
    root = prepare_data()
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=settings['port'])
    parser.add_argument('--db', type=Path, default=root / 'usage.db')
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    DB = args.db.resolve(); SOURCE_HOME = args.home.resolve()
    DB.parent.mkdir(parents=True, exist_ok=True)
    meter.TZ = ZoneInfo(settings['timezone'])
    lock = open(str(DB) + '.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('已有采集器使用此数据库')
    prices_path = root / 'prices.json'
    prices = json.loads(prices_path.read_text()) if prices_path.exists() else {}
    collector = Meter(DB, SOURCE_HOME, prices=prices)
    if args.once:
        collector.scan()
        print(json.dumps(sources(collector.conn, SOURCE_HOME), ensure_ascii=False))
        return
    collector.conn.close()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), API)
    threading.Thread(target=scan_loop, args=(DB, SOURCE_HOME, prices), daemon=True).start()
    print(f'Token Monitor {VERSION}: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
