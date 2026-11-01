import unittest,tempfile,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meter import Meter
class MigrationTests(unittest.TestCase):
    def test_import_once_and_discard_ambiguous_estimate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=sqlite3.connect(root/'old.db')
            old.execute('CREATE TABLE usage_events(tool,session,uuid,ts,model,project,in_tokens,out_tokens,cache_read,cache_write)')
            old.execute("INSERT INTO usage_events VALUES('claude','s','r',1788580800000,'deepseek-test','/repo',10,2,20,0)")
            old.execute('CREATE TABLE official_daily(date,balance,cost,source,updated_ms)')
            old.execute("INSERT INTO official_daily VALUES('2026-09-04',99,98,'estimate',1788580800000)")
            old.execute("INSERT INTO official_daily VALUES('2026-09-05',90,3,'official',1788580800000)")
            old.commit();old.close()
            m=Meter(root/'new.db');m.migrate(root/'old.db');m.migrate(root/'old.db')
            self.assertEqual(m.conn.execute('SELECT count(*),sum(input_tokens+output_tokens) FROM events').fetchone()[:],(1,32))
            amounts=m.conn.execute('SELECT cost FROM account_daily ORDER BY date').fetchall()
            self.assertIsNone(amounts[0][0]);self.assertEqual(amounts[1][0],3)
            m.conn.close()
