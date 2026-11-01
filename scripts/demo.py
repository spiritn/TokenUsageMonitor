#!/usr/bin/env python3
"""生成完全合成的预览数据，不访问用户的工具日志、账户或数据库。"""
import argparse
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daemon
from adapters import event
from meter import Meter, TZ


def populate(db, home):
    rng = random.Random(42)
    collector = Meter(db, home)
    now = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tools = [('claude', 'claude-sonnet', 'anthropic'), ('codex', 'gpt-codex', 'openai'),
             ('workbuddy', 'auto', 'codebuddy'), ('qianwen', 'qwen-agent', 'qianwen')]
    for days in range(45):
        day = today - timedelta(days=days)
        if days in (1, 3, 10, 15): continue
        for tool, model, provider in tools:
            for request in range(rng.randint(3, 12)):
                inp = rng.randint(12000, 96000); out = rng.randint(100, 3000)
                read = int(inp * rng.uniform(.45, .92))
                ts = day + timedelta(hours=9, minutes=request*3)
                e = event(tool, f'demo-{tool}-{days}', f'demo-{tool}-{days}-{request}',
                          ts.isoformat(), model, '', inp, out, read, 0)
                e['provider'] = provider
                if tool == 'workbuddy': e['credits'] = round(rng.uniform(.1, 1.2), 2)
                collector.insert(e)
    stamp = int(time.time()*1000)
    for tool, _, _ in tools:
        collector.conn.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?,?,?,?)',
                               (tool, 'ready', stamp, stamp, 8, 0, '合成演示数据'))
    collector.conn.execute("INSERT OR REPLACE INTO metadata VALUES('demo','true')")
    collector.conn.commit(); collector.conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18788)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='token-monitor-demo-') as folder:
        root = Path(folder); home = root / 'empty-home'; home.mkdir()
        db = root / 'demo.db'; populate(db, home)
        daemon.DB = db; daemon.SOURCE_HOME = home
        print(f'合成演示（不访问真实日志）：http://127.0.0.1:{args.port}', flush=True)
        class DemoAPI(daemon.API):
            def do_GET(self):
                # 演示常驻时刷新合成健康状态，不让静态样本被误标为采集过期。
                with daemon.connect(db) as conn:
                    stamp = int(time.time()*1000)
                    conn.execute('UPDATE health SET last_scan=?,last_success=?',(stamp,stamp))
                super().do_GET()
        daemon.ThreadingHTTPServer(('127.0.0.1', args.port), DemoAPI).serve_forever()


if __name__ == '__main__': main()
