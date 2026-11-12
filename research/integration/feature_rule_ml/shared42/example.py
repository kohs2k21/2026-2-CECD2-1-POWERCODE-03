"""Public constructed metadata examples; no customer rows or generated labels."""
from datetime import datetime, timedelta
from pathlib import Path
import csv

from ..common import digest, new_dir, sha, write
from .artifacts import DEFAULT, profile


def make_capture(output, split, day, count=32, release=DEFAULT):
    _, p = profile(release)
    out = new_dir(output)
    d = p['definition']
    rows = {k:[] for k in 'TPM'}
    for i in range(count):
        tx = 'EXAMPLE_' + split + '_' + str(i)
        start = datetime.fromisoformat(day) + timedelta(minutes=i)
        end = start + timedelta(milliseconds=100+i*13)
        for short in 'TPM':
            cols = [c for c in d['schema']['tables'][short]['columns'] if not c['name'].startswith('__')]
            row = {c['name']:'' for c in cols}
            row.update(TRANSACTION_ID=tx,STATUS=('S','F','N','Z')[i%4],RESPONSE_CODE='EXAMPLE',RETRY_COUNT=str(i%3),START_TIME=start.isoformat(' '),END_TIME=end.isoformat(' '))
            for key in ('SUCCESS_COUNT','TOTAL_COUNT'):
                if key in row: row[key] = str(i%5+1)
            if 'ERROR_COUNT' in row: row['ERROR_COUNT'] = '1' if i%7==0 else '0'
            if short == 'T':
                row.update(PROCESS_COUNT='1',PROCESS_TIME=str(100+i*13),INTERFACE_ID='EXAMPLE_INTERFACE',INTERFACE_TYPE='EXAMPLE',CATEGORY_NAME='EXAMPLE',PROCESS_HUB_ID='EXAMPLE',START_CHANNEL_ID='EXAMPLE',END_CHANNEL_ID='EXAMPLE')
            elif short == 'P':
                row.update(PROCESS_ID='EXAMPLE_PROCESS',ADAPTER_TYPE='EXAMPLE',CHANNEL_ID='EXAMPLE',PROCESS_HUB_ID='EXAMPLE')
            else:
                row.update(PROCESS_ID='EXAMPLE_PROCESS',MESSAGE_ID='MESSAGE_'+tx,PROCESS_CHANNEL_ID='EXAMPLE',DATA_TYPE='EXAMPLE',DATA_NAME='' if i%2 else 'EXAMPLE',DIRECTION='' if i%3==0 else 'IN',DATA_SIZE=str(i+5),PROCESS_DT=end.isoformat(' '),MESSAGE_INDEX=str(i%2))
                if i%4==0: row['START_TIME']=''
                if i%5==0: row['END_TIME']=''
            rows[short].append(row)
    entries = {}
    for short, data in rows.items():
        path = out/(short+'.csv')
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
        entries[short] = dict(file=path.name,sha256=sha(path),row_count=len(data))
    write(out/'capture.json',dict(contract='shared42-capture1.0',definition_sha=p['definition_sha'],schema_sha=digest(d['schema']),admission={k:True for k in ['approved','Body_excluded','held_lineage_excluded','complete_family_capture','frozen_research_reference']},data_class='controlled',period=dict(start_inclusive=day,end_exclusive=(datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat(),timezone='constructed wall-clock'),split=split,lineage_tags=['public_constructed_example_not_customer_data'],confirmed_hr_evidence_ids=sorted({e for r in d['rules'] if r['role']=='HR' for e in r['evidence_ids']}),tables=entries))
    return out/'capture.json'


def demo(output, release=DEFAULT):
    from .trainer import train
    from .detector import detect
    root = new_dir(output)
    refs = [make_capture(root/name,name,day,release=release) for name,day in [('train','2026-07-01'),('calibration','2026-07-02'),('threshold','2026-07-03'),('test','2026-07-04')]]
    training = train(*refs[:3],root/'trainer',release)
    detection = detect(refs[3],root/'detector',release,root/'trainer/models',True)
    result=dict(public_constructed_example=True,training=training,detection=detection,customer_performance_claim=False)
    write(root/'result.json',result)
    return result
