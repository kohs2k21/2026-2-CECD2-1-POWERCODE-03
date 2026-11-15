"""Local PostgreSQL: restricted Collector can stage/admit four streams, not read BODY values."""
import csv,os,sys,tempfile,uuid
from pathlib import Path
from urllib.parse import urlsplit
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector.input_store import ensure_source,stage_csv,switch
from collector.input_runner import tick,read_state
from collector import resource_sampler
from collector.source_clock import set_clock_basis

def main():
    dsn=os.environ['DATABASE_URL'];p=urlsplit(dsn)
    assert p.hostname in ('localhost','127.0.0.1') and p.path=='/qa_pipeline' and not p.query
    infra=Path(os.environ.get('RUNTIME_INFRA_ROOT',str(Path(__file__).resolve().parents[2]/'infra')))
    with psycopg.connect(dsn) as admin:admin.execute((infra/'scripts/database-roles.sql').read_text())
    role_dsn=dsn+'?options=-c%20role%3Dpowercode_collector'
    with tempfile.TemporaryDirectory() as temp,psycopg.connect(role_dsn) as conn:
        source=ensure_source(conn,'input-role-'+uuid.uuid4().hex[:8]);conn.commit()
        specs={'T':(['TRANSACTION_ID'],[['t']]),'P':(['TRANSACTION_ID','PROCESS_ID','TOTAL_COUNT','ERROR_COUNT'],[['t','p',10,1]]),
            'M':(['TRANSACTION_ID','PROCESS_ID','MESSAGE_ID'],[['t','p','m']]),'B':(['MESSAGE_ID','MESSAGE_BODY'],[['m','SECRET']])}
        files={}
        for layer,(headers,rows) in specs.items():
            files[layer]=Path(temp)/(layer+'.csv')
            with files[layer].open('w',newline='') as f:w=csv.writer(f);w.writerow(headers);w.writerows(rows)
        replay=stage_csv(conn,source,'roles',files);conn.commit()
        switch(conn,source,mode='csv',expected_revision=0,actor='role-test',settings={'replayId':str(replay),'rate':10000,'repeat':1});conn.commit()
        while read_state(conn,source)['position']<4:
            result=tick(conn,source,collect_catalog=False)
            assert 'errorCode' not in result,result
        assert dict(conn.execute('SELECT layer::text,count(*) FROM data_observations WHERE source_id=%s GROUP BY layer',(source,)).fetchall())=={'T':1,'P':1,'M':1,'B':1}
        conn.commit()
        try:conn.execute('SELECT message_body FROM data_message_bodies');conn.commit()
        except psycopg.errors.InsufficientPrivilege:conn.rollback()
        else:raise AssertionError('collector can read BODY values')
        print('PASS: restricted Collector initialization, frozen four-file replay, T/P/M/B admission and BODY value read denied')
        # 030: operator clock-basis CLI path and the resources sampler run under the same restricted role.
        changed=set_clock_basis(conn,source,verify_timezone='Asia/Seoul',actor='role-test');conn.commit()
        assert changed['clockBasis']['status']=='verified' and changed['clockBasis']['verifiedBy']=='role-test',changed
        sampler=resource_sampler.Sampler(proc_root=Path(temp)/'no-proc',disks={'database_data':None,'artifacts':temp})
        resource_sampler.store(conn,sampler.resource_row(),sampler.disk_rows())
        saved=conn.execute("""SELECT cpu_status::text,cpu_reason_code,memory_status::text FROM ops_resource_measurements
            ORDER BY measured_at DESC LIMIT 1""").fetchone()
        disks=dict(conn.execute("""SELECT DISTINCT ON (storage_key) storage_key,reason_code FROM ops_storage_measurements
            WHERE target_kind='filesystem' ORDER BY storage_key,measured_at DESC""").fetchall());conn.commit()
        assert saved==('unavailable','proc_stat_unavailable','unavailable'),saved
        assert disks['database_data']=='mount_not_visible',disks
        try:conn.execute('UPDATE ops_resource_measurements SET scope=%s',('host',));conn.commit()
        except psycopg.errors.InsufficientPrivilege:conn.rollback()
        else:raise AssertionError('collector can update resource samples')
        print('PASS: collector role sets clock_basis, stores unavailable resource/disk samples, cannot update samples')

if __name__=='__main__':main()
