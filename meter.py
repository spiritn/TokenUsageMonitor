"""统一计量层：事务游标、请求去重和日历周期统计。"""
import copy
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from adapters import ADAPTERS, UNAVAILABLE

TZ = ZoneInfo('Asia/Shanghai')
FIELDS = ('tool','account','session','response_id','ts','model','cwd','project','provider',
          'input_tokens','output_tokens','cache_read','cache_write','credits','basis','source','cost','currency','cache_known')


def connect(path, readonly=False):
    c = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=15) if readonly else sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    if readonly:
        return c
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS events (
      tool TEXT, account TEXT, session TEXT, response_id TEXT, ts INTEGER,
      model TEXT, cwd TEXT, project TEXT, provider TEXT,
      input_tokens INTEGER, output_tokens INTEGER, cache_read INTEGER, cache_write INTEGER,
      credits REAL, basis TEXT, source TEXT, cost REAL, currency TEXT,
      PRIMARY KEY(tool,account,response_id));
    CREATE INDEX IF NOT EXISTS event_time ON events(ts);
    CREATE TABLE IF NOT EXISTS cursors (
      path TEXT PRIMARY KEY, inode INTEGER, offset INTEGER, prefix TEXT, context TEXT);
    CREATE TABLE IF NOT EXISTS health (
      tool TEXT PRIMARY KEY, status TEXT, last_scan INTEGER, last_success INTEGER,
      files INTEGER, errors INTEGER, detail TEXT);
    CREATE TABLE IF NOT EXISTS quotas (tool TEXT PRIMARY KEY, ts INTEGER, data TEXT);
    CREATE TABLE IF NOT EXISTS account_daily (
      provider TEXT, date TEXT, balance REAL, opening_balance REAL, cost REAL,
      source TEXT, updated_ms INTEGER, PRIMARY KEY(provider,date));
    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS rejected_lines (
      path TEXT, inode INTEGER, offset INTEGER, reason TEXT,
      PRIMARY KEY(path,inode,offset));
    ''')
    if 'cache_known' not in {r['name'] for r in c.execute('PRAGMA table_info(events)')}:
        c.execute('ALTER TABLE events ADD COLUMN cache_known INTEGER')
        # 历史零值可能表示缺字段；仅对确有缓存量的记录确认可观测性。
        c.execute('UPDATE events SET cache_known=1 WHERE cache_read>0 OR cache_write>0')
        c.commit()
    return c


class Meter:
    def __init__(self, db, home=None, settings=None, prices=None):
        self.conn = connect(db)
        self.home = Path(home or Path.home())
        self.settings = settings or {}
        self.prices = prices or {}
        # 重新读取现存日志，补齐缓存可观测标记；已消失的日志对应记录保留。
        version = self.conn.execute("SELECT value FROM metadata WHERE key='parser_version'").fetchone()
        if not version or version[0] != '3':
            with self.conn:
                self.conn.execute('DELETE FROM cursors')
                self.conn.execute("INSERT OR REPLACE INTO metadata VALUES('parser_version','3')")

    def price(self, e):
        # 订阅和积分消费不能套 API 价格；只有明确 DeepSeek 调用才使用现有 CNY 表。
        if e['provider'] != 'deepseek' or e['tool'] not in ('claude','dsh'):
            return None, None
        versions = self.prices.get(e['model'], [])
        local = datetime.fromtimestamp(e['ts']/1000,TZ)
        eligible = [v for v in versions if not v.get('from') or v['from'] <= local.date().isoformat()]
        if not eligible:
            return None,None
        v = sorted(eligible,key=lambda x:x.get('from') or '')[-1]
        rate = v
        if 'offpeak' in v:
            peak=any(a<=local.hour<b for a,b in v.get('peak_hours',[]))
            rate=v['peak'] if peak else v['offpeak']
        ordinary = e['input_tokens']-e['cache_read']-e['cache_write']
        cost=(ordinary*rate['input']+e['output_tokens']*rate['output']+
              e['cache_read']*rate.get('cache_read',rate['input'])+
              e['cache_write']*rate.get('cache_write',rate['input']))/1e6
        return cost,'CNY'

    def insert(self, e):
        if e.get('_delete'):
            self.conn.execute("DELETE FROM events WHERE tool=? AND account=? AND response_id=? AND basis='cumulative_delta'",
                              (e['tool'],e['account'],e['response_id']))
            return
        e['project'] = ''
        e['cost'],e['currency'] = self.price(e)
        # 同响应较新的快照整体覆盖，不把多个快照相加，也不逐字段取最大值。
        sql = 'INSERT INTO events('+','.join(FIELDS)+') VALUES('+','.join('?' for _ in FIELDS)+') '
        sql += 'ON CONFLICT(tool,account,response_id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in FIELDS if k not in ('tool','account','response_id'))
        sql += ' WHERE excluded.ts >= events.ts'
        self.conn.execute(sql,[e[k] for k in FIELDS])

    def migrate(self, old_path):
        if self.conn.execute("SELECT 1 FROM metadata WHERE key='legacy_import'").fetchone():
            return
        if Path(old_path).exists():
            from adapters import event
            old = sqlite3.connect(Path(old_path).as_uri()+'?mode=ro',uri=True)
            old.row_factory=sqlite3.Row
            with self.conn:
                for r in old.execute('SELECT * FROM usage_events'):
                    e=event(r['tool'],r['session'],r['uuid'],r['ts'],r['model'],r['project'],
                            r['in_tokens']+r['cache_read']+r['cache_write'],r['out_tokens'],r['cache_read'],r['cache_write'])
                    e['provider']='deepseek' if (r['model'] or '').startswith('deepseek') else ('anthropic' if (r['model'] or '').startswith('claude') else 'unknown')
                    e['source']='legacy_import'
                    e['cache_known']=int(r['cache_read']>0 or r['cache_write']>0)
                    self.insert(e)
                # 旧 estimate 字段存在歧义，只迁移可验证的平台金额或余额。
                if old.execute("SELECT 1 FROM sqlite_master WHERE name='official_daily'").fetchone():
                    for r in old.execute('SELECT * FROM official_daily'):
                        cost = r['cost'] if r['source'] == 'official' else None
                        self.conn.execute('INSERT OR IGNORE INTO account_daily VALUES(?,?,?,?,?,?,?)',
                            ('deepseek', r['date'], r['balance'], None, cost,
                             'platform_private' if cost is not None else 'balance_only', r['updated_ms']))
            old.close()
        with self.conn:
            self.conn.execute("INSERT INTO metadata VALUES('legacy_import','done')")

    def scan_file(self, adapter, path):
        row = self.conn.execute('SELECT * FROM cursors WHERE path=?',(str(path),)).fetchone()
        with path.open('rb') as f:
            st=os.fstat(f.fileno());offset=0;ctx={}
            if row and row['inode']==st.st_ino and st.st_size>=row['offset']:
                prefix=hashlib.sha256(f.read(min(row['offset'],128))).hexdigest()
                if prefix==row['prefix']:
                    offset=row['offset'];ctx=json.loads(row['context'])
            f.seek(offset)
            malformed=0
            with self.conn:
                if not offset:
                    self.conn.execute('DELETE FROM rejected_lines WHERE path=?',(str(path),))
                # 限制单次读取，防止活跃的大日志饿死其他适配器。
                for _ in range(10000):
                    line=f.readline()
                    if not line or not line.endswith(b'\n'):
                        break
                    line_start=f.tell()-len(line)
                    trial=copy.deepcopy(ctx)
                    try:
                        d=json.loads(line)
                        if not isinstance(d,dict):raise ValueError('record must be an object')
                        events=adapter.parse(d,trial,path)
                    except (ValueError,UnicodeDecodeError,TypeError,KeyError,AttributeError,OverflowError) as ex:
                        self.conn.execute('INSERT OR REPLACE INTO rejected_lines VALUES(?,?,?,?)',
                                          (str(path),st.st_ino,line_start,type(ex).__name__))
                        malformed+=1;offset=f.tell();continue
                    for e in events:
                        self.insert(e)
                    ctx=trial
                    self.conn.execute('DELETE FROM rejected_lines WHERE path=? AND inode=? AND offset=?',(str(path),st.st_ino,line_start))
                    offset=f.tell()
                f.seek(0);prefix=hashlib.sha256(f.read(min(offset,128))).hexdigest()
                self.conn.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?,?)',
                    (str(path),st.st_ino,offset,prefix,json.dumps(ctx)))
                if ctx.get('quota'):
                    q=ctx.pop('quota')
                    self.conn.execute('INSERT INTO quotas VALUES(?,?,?) ON CONFLICT(tool) DO UPDATE SET ts=excluded.ts,data=excluded.data WHERE excluded.ts>=quotas.ts',
                                      (q['tool'],q['ts'],json.dumps(q['data'])))
            return malformed

    def scan(self):
        now=int(time.time()*1000)
        for a in ADAPTERS:
            errors=0;files=[];detail=''
            try:
                files=sorted(a.paths(self.home))
                for p in files:
                    try:
                        errors+=self.scan_file(a,p)
                    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error) as ex:
                        errors+=1;detail=type(ex).__name__+'：'+p.name
                rejected=self.conn.execute('SELECT count(*) FROM rejected_lines WHERE path LIKE ?',
                                           (str(self.home/('.'+a.tool))+'/%',)).fetchone()[0]
                if a.tool=='qianwen':
                    rejected=self.conn.execute('SELECT count(*) FROM rejected_lines WHERE path LIKE ?',
                                               (str(self.home/'Library/Application Support/Qianwen')+'/%',)).fetchone()[0]
                if rejected:
                    errors=max(errors,rejected);detail=f'{rejected} 条异常记录已隔离，可重放原文件'
            except OSError as ex:
                errors+=1;detail=type(ex).__name__
            status='error' if errors else ('ready' if files else 'no_source')
            with self.conn:
                self.conn.execute('''INSERT INTO health VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(tool) DO UPDATE SET status=excluded.status,last_scan=excluded.last_scan,
                last_success=CASE WHEN excluded.errors=0 THEN excluded.last_success ELSE health.last_success END,
                files=excluded.files,errors=excluded.errors,detail=excluded.detail''',
                (a.tool,status,now,now if not errors else None,len(files),errors,detail))


def period_range(period='day', days=None, start=None, end=None, now=None):
    now=now or datetime.now(TZ)
    midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if start or end:
        if not start or not end:
            raise ValueError('start 和 end 必须同时提供')
        first=datetime.strptime(start,'%Y-%m-%d').replace(tzinfo=TZ)
        last=datetime.strptime(end,'%Y-%m-%d').replace(tzinfo=TZ)+timedelta(days=1)
    elif days is not None:
        if not 1<=days<=3660:raise ValueError('days 超出范围')
        first=midnight-timedelta(days=days-1);last=midnight+timedelta(days=1)
    else:
        if period not in ('day','week','month','last_month'):raise ValueError('period 无效')
        if period=='last_month':
            last=midnight.replace(day=1);first=(last-timedelta(days=1)).replace(day=1)
            return int(first.timestamp()*1000),int(last.timestamp()*1000)
        first=midnight if period=='day' else (midnight-timedelta(days=midnight.weekday()) if period=='week' else midnight.replace(day=1))
        last=midnight+timedelta(days=1)
    if last<=first or (last-first).days>3660:raise ValueError('日期范围无效或超过十年')
    return int(first.timestamp()*1000),int(last.timestamp()*1000)


def aggregate(rows):
    costs=defaultdict(float)
    for r in rows:
        if r['cost'] is not None:costs[r['currency']]+=r['cost']
    inp=sum(r['input_tokens'] for r in rows);out=sum(r['output_tokens'] for r in rows)
    cr=sum(r['cache_read'] for r in rows);cw=sum(r['cache_write'] for r in rows)
    known=sum(r['cost'] is not None for r in rows)
    # input_tokens 是完整输入量（已包含缓存命中/写入）；缓存未命中是剩余输入。
    # 命中率只对确实提供输入缓存字段的事件计算，避免把未知误当成 0%。
    observed=[r for r in rows if r.get('cache_known') == 1]
    observed_input=sum(r['input_tokens'] for r in observed)
    observed_read=sum(r['cache_read'] for r in observed)
    cache_miss = sum(r['input_tokens']-r['cache_read']-r['cache_write'] for r in observed)
    return dict(tokens=inp+out,input_tokens=inp,output_tokens=out,cache_read=cr,cache_write=cw,
        cache_miss=cache_miss,
        cache_hit_rate=(observed_read / observed_input if observed_input else None),
        cache_known_input=observed_input,cache_coverage=(observed_input/inp if inp else None),
        calls=len(rows),sessions=len({(r['tool'],r['session']) for r in rows}),
        estimated_calls=sum(r['basis']!='response' for r in rows),costs=dict(costs),
        cost=costs.get('CNY') if known==len(rows) and len(costs)<=1 and rows else None,
        known_cost_calls=known,unknown_cost_calls=len(rows)-known,
        credits=sum(r['credits'] or 0 for r in rows) if any(r['credits'] is not None for r in rows) else None,
        **{'in':inp,'out':out})


def overview(conn, period='day', days=None, project=None, tool=None, start=None,end=None, home=None):
    if project:raise ValueError('项目统计已停用')
    begin,finish=period_range(period,days,start,end)
    sql='SELECT * FROM events WHERE ts>=? AND ts<?';args=[begin,finish]
    if tool:sql+=' AND tool=?';args.append(tool)
    rows=[dict(r) for r in conn.execute(sql+' ORDER BY ts',args)]
    result=dict(demo=bool(conn.execute("SELECT 1 FROM metadata WHERE key='demo'").fetchone()),version=2,range=dict(start=begin,end=finish,timezone=str(TZ),period=period),total=aggregate(rows))
    for key,column in [('tools','tool'),('providers','provider'),('models','model')]:
        groups=defaultdict(list)
        for r in rows:groups[r[column]].append(r)
        result[key]={k:aggregate(v) for k,v in groups.items()}
    trend=defaultdict(list)
    for r in rows:
        trend[datetime.fromtimestamp(r['ts']/1000,TZ).strftime('%Y-%m-%d')].append(r)
    result['trend']=[]
    day=datetime.fromtimestamp(begin/1000,TZ)
    while day.timestamp()*1000<finish:
        key=day.strftime('%Y-%m-%d'); result['trend'].append(dict(date=key,**aggregate(trend[key])));day+=timedelta(days=1)
    result['recent']=list(reversed(rows[-30:]))
    result['sources']=sources(conn,home)
    result['quota']=[dict(tool=r['tool'],ts=r['ts'],data=json.loads(r['data'])) for r in conn.execute('SELECT * FROM quotas')]
    result['accounts']=[dict(r) for r in conn.execute('SELECT * FROM account_daily ORDER BY date DESC LIMIT 30')]
    result['accounts_scope']='latest_snapshot'
    return result


def sources(conn,home=None):
    result=[]
    for a in ADAPTERS:
        r=conn.execute('SELECT * FROM health WHERE tool=?',(a.tool,)).fetchone()
        item=dict(r) if r else dict(tool=a.tool,status='starting',last_scan=None,last_success=None,files=0,errors=0,detail='')
        if item.get('last_scan') and time.time()*1000-item['last_scan']>120000:item['status']='stale'
        item.update(name=a.name,coverage=a.coverage,last_event=conn.execute('SELECT max(ts) FROM events WHERE tool=?',(a.tool,)).fetchone()[0])
        if a.tool=='codex':
            item['coverage_warning']=codex_coverage(home)
        result.append(item)
    return result+[dict(r) for r in UNAVAILABLE]


_codex_coverage_cache=(0,None,None)
def codex_coverage(home=None):
    """只检查会话存储元数据，不读取聊天正文或将累计 token 分配到日期。"""
    global _codex_coverage_cache
    if time.monotonic()-_codex_coverage_cache[0]<60 and _codex_coverage_cache[2]==str(home):return _codex_coverage_cache[1]
    warning=None
    path=Path(home or Path.home())/'.codex/state_5.sqlite'
    if path.exists():
        try:
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1) as state:
                count=state.execute("SELECT count(*) FROM threads WHERE history_mode='paginated'").fetchone()[0]
            if count:warning='客户端存在分页历史；当前仅采集现存 JSONL，迁移历史的覆盖尚未验证'
        except sqlite3.Error:
            warning='无法检查客户端历史格式；当前仅采集现存 JSONL'
    _codex_coverage_cache=(time.monotonic(),warning,str(home))
    return warning
