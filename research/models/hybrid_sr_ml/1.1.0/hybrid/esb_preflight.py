"""Validate the existing numerical capture before either detection component."""
from pathlib import Path
import argparse,csv,gzip,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hybrid.common import ROOT,read,write,sha
sys.path.insert(0,str(ROOT/'vendor/numeric'))
from research.integration.feature_rule_ml.shared42.artifacts import profile
from research.integration.feature_rule_ml.shared42.capture import Capture

def main():
    p=argparse.ArgumentParser();p.add_argument('--capture',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    _,pr=profile();c=Capture(a.capture,pr)
    with gzip.open(out/'entities.csv.gz','wt',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['entity_id','transaction_id','source_row_id']);w.writeheader()
        for i,row in enumerate(c.rows[c.transaction_table]):
            tid=row['TRANSACTION_ID'];w.writerow(dict(entity_id=tid or '__unlinked__:T:'+str(i),transaction_id=tid,source_row_id=row['__SOURCE_ROW_ID']))
    write(out/'admission.json',dict(admitted=True,capture_sha256=sha(a.capture),source_pins=c.pins,entities=len(c.rows[c.transaction_table]),definition_sha=pr['definition_sha'],no_fit_no_LLM=True))
    c.assert_unchanged();print(json.dumps(dict(admitted=True,entities=len(c.rows[c.transaction_table]))))

if __name__=='__main__':main()
