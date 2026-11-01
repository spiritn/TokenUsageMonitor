import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapters import Claude,WorkBuddy,Codex,Qianwen,event
from meter import Meter,aggregate,period_range,overview,TZ

TS='2026-09-05T04:00:00Z'
def cc(rid='r1',out=2,ts=TS):
    return dict(type='assistant',sessionId='s',timestamp=ts,cwd='/project',message=dict(id=rid,model='deepseek-test',usage=dict(input_tokens=10,output_tokens=out,cache_read_input_tokens=20,cache_creation_input_tokens=0)))

class MeterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.m=Meter(self.root/'db.sqlite',self.root)
        self.p=self.root/'log.jsonl'
    def tearDown(self):self.m.conn.close();self.tmp.cleanup()
    def write(self,d,mode='ab'):
        with self.p.open(mode) as f:f.write(json.dumps(d).encode()+b'\n')
    def rows(self):return [dict(r) for r in self.m.conn.execute('SELECT * FROM events')]
    def test_partial_then_complete(self):
        b=json.dumps(cc()).encode();self.p.write_bytes(b[:40])
        self.m.scan_file(Claude(),self.p);self.assertEqual(len(self.rows()),0)
        with self.p.open('ab') as f:f.write(b[40:]+b'\n')
        self.m.scan_file(Claude(),self.p);self.assertEqual(aggregate(self.rows())['tokens'],32)
    def test_replay_restart_update_and_stale(self):
        self.write(cc());self.m.scan_file(Claude(),self.p)
        self.m.conn.close();self.m=Meter(self.root/'db.sqlite',self.root)
        self.write(cc(out=6));self.write(cc(out=1,ts='2026-09-04T04:00:00Z'))
        self.m.scan_file(Claude(),self.p);self.m.scan_file(Claude(),self.p)
        self.assertEqual(len(self.rows()),1);self.assertEqual(self.rows()[0]['output_tokens'],6)
    def test_rotation_and_truncate(self):
        self.write(cc());self.m.scan_file(Claude(),self.p)
        self.p.rename(self.root/'old');self.write(cc('r2'))
        self.m.scan_file(Claude(),self.p);self.assertEqual(len(self.rows()),2)
        self.p.write_bytes(b'');self.m.scan_file(Claude(),self.p)
        self.write(cc('r3'));self.m.scan_file(Claude(),self.p);self.assertEqual(len(self.rows()),3)
    def test_transaction_failure_does_not_advance(self):
        self.write(cc());old=self.m.insert
        def fail(e):raise ValueError('test')
        self.m.insert=fail
        with self.assertRaises(ValueError):self.m.scan_file(Claude(),self.p)
        self.assertEqual(self.m.conn.execute('SELECT count(*) FROM cursors').fetchone()[0],0)
        self.m.insert=old;self.m.scan_file(Claude(),self.p);self.assertEqual(len(self.rows()),1)
    def test_duplicate_parent_child(self):
        self.write(cc());self.m.scan_file(Claude(),self.p)
        child=self.root/'child.jsonl';d=cc();d['sessionId']='child';child.write_text(json.dumps(d)+'\n')
        self.m.scan_file(Claude(),child);self.assertEqual(len(self.rows()),1)
    def test_workbuddy_cache_and_credits(self):
        d=dict(type='function_call',id='i',sessionId='s',timestamp=TS,providerData=dict(messageId='m',rawUsage=dict(credit=1.2)),message=dict(usage=dict(input_tokens=100,output_tokens=10,total_tokens=110,cache_read_input_tokens=60)))
        e=WorkBuddy().parse(d,{},self.p)[0];self.m.insert(e)
        a=aggregate(self.rows());self.assertEqual(a['tokens'],110);self.assertEqual(a['credits'],1.2);self.assertIsNone(a['cost'])
    def test_codex_record_plus_snapshot(self):
        a=Codex();ctx={'session':'s','model':'gpt'}
        u=dict(input_tokens=100,output_tokens=10,cached_input_tokens=60)
        d=dict(type='token_usage_record',timestamp=TS,payload=dict(response_id='r',usage=u))
        self.m.insert(a.parse(d,ctx,self.p)[0])
        self.m.insert(a.parse(d,ctx,self.p)[0])
        snap=dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=u)))
        self.assertEqual(a.parse(snap,ctx,self.p),[]);self.assertEqual(a.parse(snap,ctx,self.p),[])
        self.assertEqual(aggregate(self.rows())['tokens'],110)
    def test_codex_cumulative_and_reset(self):
        a=Codex();ctx={'session':'s'}
        for inp,out in [(100,10),(100,10),(200,30)]:
            d=dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=dict(input_tokens=inp,output_tokens=out))))
            for e in a.parse(d,ctx,self.p):self.m.insert(e)
        self.assertEqual(aggregate(self.rows())['tokens'],230)
        d['payload']['info']=dict(total_token_usage=dict(input_tokens=20,output_tokens=2),last_token_usage=dict(input_tokens=20,output_tokens=2))
        for e in a.parse(d,ctx,self.p):self.m.insert(e)
        self.assertEqual(aggregate(self.rows())['tokens'],252)
    def test_qianwen_cache_exclusive(self):
        d=dict(type='canonical_item',threadId='s',timestamp=TS,payload=dict(item=dict(kind='usage',usageSource='provider',id='u',model='qwen',usage=dict(input_tokens=10,output_tokens=2,cache_read_input_tokens=30))))
        self.assertEqual(Qianwen().parse(d,{},self.p)[0]['input_tokens'],40)
    def test_project_statistics_removed(self):
        self.m.insert(Claude().parse(cc(),{},self.p)[0])
        result=overview(self.m.conn,start='2026-09-05',end='2026-09-05')
        self.assertNotIn('projects',result)
        self.assertNotIn('project_options',result)
        with self.assertRaises(ValueError):overview(self.m.conn,project='old')

    def test_bad_usage_does_not_block_following_lines(self):
        bad=cc('bad');bad['message']['usage']['input_tokens']=-1
        for d in [cc('before'),bad,cc('after')]:self.write(d)
        self.assertEqual(self.m.scan_file(Claude(),self.p),1)
        self.assertEqual(len(self.rows()),2)
        self.assertEqual(self.m.conn.execute('SELECT count(*) FROM rejected_lines').fetchone()[0],1)
        self.m.scan_file(Claude(),self.p)
        self.assertEqual(len(self.rows()),2)

    def test_missing_cache_does_not_become_zero(self):
        d=cc();d['message']['usage'].pop('cache_read_input_tokens')
        self.m.insert(Claude().parse(d,{},self.p)[0])
        a=aggregate(self.rows());self.assertIsNone(a['cache_hit_rate'])
        self.assertEqual(a['cache_coverage'],0)
        self.m.insert(Claude().parse(cc('known'),{},self.p)[0])
        a=aggregate(self.rows());self.assertAlmostEqual(a['cache_hit_rate'],20/30)
        self.assertAlmostEqual(a['cache_coverage'],30/40)

    def test_snapshot_then_response_survives_restart(self):
        u=dict(input_tokens=100,output_tokens=10,cached_input_tokens=60)
        self.write(dict(type='session_meta',payload=dict(id='s'),timestamp=TS))
        self.write(dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=u))))
        self.m.scan_file(Codex(),self.p)
        self.m.conn.close();self.m=Meter(self.root/'db.sqlite',self.root)
        self.write(dict(type='token_usage_record',timestamp=TS,payload=dict(response_id='r',usage=u)))
        self.m.scan_file(Codex(),self.p)
        self.assertEqual(aggregate(self.rows())['tokens'],110)
        self.assertEqual(len(self.rows()),1)

    def test_snapshot_covers_multiple_responses(self):
        a=Codex();ctx={'session':'s'}
        total=dict(input_tokens=200,output_tokens=20,cached_input_tokens=120)
        def accept(d):
            for e in a.parse(d,ctx,self.p):self.m.insert(e)
        accept(dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=total))))
        for rid,end in [('a',100),('b',200)]:
            usage=dict(input_tokens=100,output_tokens=10,cached_input_tokens=60)
            endpoint=dict(input_tokens=end,output_tokens=end//10,cached_input_tokens=end*6//10)
            accept(dict(type='token_usage_record',timestamp=TS,payload=dict(response_id=rid,usage=usage,thread_token_usage=endpoint)))
        self.assertEqual(aggregate(self.rows())['tokens'],220)
        self.assertEqual(len(self.rows()),2)
        accept(dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=total))))
        self.assertEqual(aggregate(self.rows())['tokens'],220)

    def test_response_update_reconciles_new_snapshot_increment(self):
        a=Codex();ctx={'session':'s'}
        def accept(d):
            for e in a.parse(d,ctx,self.p):self.m.insert(e)
        for n in [100,150]:
            u=dict(input_tokens=n,output_tokens=n//10,cached_input_tokens=n*6//10)
            accept(dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=u))))
            accept(dict(type='token_usage_record',timestamp=TS,payload=dict(response_id='r',usage=u,thread_token_usage=u)))
        self.assertEqual(aggregate(self.rows())['tokens'],165)
        self.assertEqual(len(self.rows()),1)

    def test_reset_snapshot_then_response(self):
        a=Codex();ctx={'session':'s','total':[200,20,120,0]}
        u=dict(input_tokens=100,output_tokens=10,cached_input_tokens=60)
        snapshot=dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=u,last_token_usage=u)))
        response=dict(type='token_usage_record',timestamp=TS,payload=dict(response_id='r',usage=u,thread_token_usage=u))
        for d in [snapshot,response]:
            for e in a.parse(d,ctx,self.p):self.m.insert(e)
        self.assertEqual(aggregate(self.rows())['tokens'],110)
        self.assertEqual(len(self.rows()),1)

    def test_legacy_snapshot_not_removed_by_later_response(self):
        a=Codex();ctx={'session':'s'}
        u=dict(input_tokens=100,output_tokens=10,cached_input_tokens=60)
        for e in a.parse(dict(type='event_msg',timestamp=TS,payload=dict(type='token_count',info=dict(total_token_usage=u))),ctx,self.p):self.m.insert(e)
        endpoint=dict(input_tokens=200,output_tokens=20,cached_input_tokens=120)
        for e in a.parse(dict(type='token_usage_record',timestamp='2026-09-05T04:01:00Z',payload=dict(response_id='new',usage=u,thread_token_usage=endpoint)),ctx,self.p):self.m.insert(e)
        self.assertEqual(aggregate(self.rows())['tokens'],220)

    def test_calendar_boundaries(self):
        now=datetime(2026,9,5,12,tzinfo=TZ)
        s,e=period_range('week',now=now)
        self.assertEqual(datetime.fromtimestamp(s/1000,TZ).strftime('%Y-%m-%d'),'2026-08-31')
        s,e=period_range('month',now=now)
        self.assertEqual(datetime.fromtimestamp(s/1000,TZ).day,1)
        s,e=period_range('last_month',now=datetime(2026,1,15,tzinfo=TZ))
        self.assertEqual(datetime.fromtimestamp(s/1000,TZ).strftime('%Y-%m-%d'),'2025-12-01')
        self.assertEqual(datetime.fromtimestamp(e/1000,TZ).strftime('%Y-%m-%d'),'2026-01-01')
        with self.assertRaises(ValueError):period_range(start='2026-09-05',end='2026-09-01')
    def test_unknown_cost_stays_unknown(self):
        self.m.insert(Claude().parse(cc(),{},self.p)[0]);a=aggregate(self.rows())
        self.assertIsNone(a['cost']);self.assertEqual(a['unknown_cost_calls'],1)

class PriceTests(unittest.TestCase):
    def test_null_baseline_and_dated_price(self):
        with tempfile.TemporaryDirectory() as folder:
            m=Meter(Path(folder)/'db',prices={'deepseek-test':[{'from':None,'input':1,'output':2,'cache_read':.1},{'from':'2026-09-01','input':3,'output':4,'cache_read':.2}]})
            e=Claude().parse(cc(),{},Path('test'))[0]
            m.insert(e)
            self.assertAlmostEqual(m.conn.execute('select cost from events').fetchone()[0],(10*3+20*.2+2*4)/1e6)
            m.conn.close()

if __name__=='__main__':unittest.main()
