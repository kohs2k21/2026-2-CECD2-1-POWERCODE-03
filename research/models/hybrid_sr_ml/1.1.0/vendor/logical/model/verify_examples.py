"""CPU replay of actual LLM-generated fictional examples; zero model calls."""
from pathlib import Path
import argparse,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    integrity=subprocess.run([sys.executable,'-B',str(ROOT/'cli.py'),'check'],capture_output=True,text=True)
    if integrity.returncode:raise ValueError('Bundle integrity verification failed: '+integrity.stderr)
    sys.path.insert(0,str(ROOT/'implementation'));sys.path.insert(0,str(ROOT))
    from serve_rules import serve
    from export_contracts import export
    pack=json.loads((ROOT/'examples/generated_candidate_pack.json').read_text())
    expected=json.loads((ROOT/'examples/expected_results.json').read_text())
    args.output.mkdir(parents=True,exist_ok=False);checks=[];start=time.monotonic()
    for name,decision in expected['cases'].items():
        source=json.loads((ROOT/'examples'/name).read_text());result=serve(pack,source)
        (args.output/(Path(name).stem+'_result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        definitions={x['feature_id']:x['definition_sha256']for x in result['linked_feature_contracts']}
        linked=all(definitions.get(x['feature_id'])==x['feature_definition_sha256']for x in result['linked_feature_values'])
        checks.append({'case':name,'expected':decision,'actual':result['decision'],
            'linked_feature_definitions_match':linked,'passed':result['decision']==decision and linked})
    contracts=export(pack,json.loads((ROOT/'examples/source.json').read_text()))
    (args.output/'linked_contracts.json').write_text(json.dumps(contracts,ensure_ascii=False,indent=2)+'\n')
    report={'cases':checks,'passed':sum(x['passed']for x in checks),'total':len(checks),
        'actual_LLM_calls':0,'fit_or_threshold_change':False,'seconds':time.monotonic()-start,
        'scope':'Fictional executable examples, not company incident accuracy.'}
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
    if any(not x['passed']for x in checks):raise SystemExit(2)

if __name__=='__main__':main()
