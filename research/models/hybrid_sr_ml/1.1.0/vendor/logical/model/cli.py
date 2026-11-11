"""Pinned local recommendation and frozen-rule execution. No downloads or fallback."""
from pathlib import Path
import sys,os,argparse,json,hashlib
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'implementation'));sys.path.insert(0,str(ROOT))
def check_selected():
    manifest=json.loads((ROOT/'package_manifest.json').read_text())
    for item in manifest['files']:
        q=(ROOT/item['path']).resolve()
        if not q.is_relative_to(ROOT.resolve()):raise ValueError('Package manifest path escapes bundle')
        if not q.is_file()or hashlib.sha256(q.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Package integrity mismatch: '+item['path'])
    m=json.loads((ROOT/'selected_pipeline.json').read_text())
    if hashlib.sha256((ROOT/'selection_evidence.json').read_bytes()).hexdigest()!=m['selection_record_sha256']:
        raise ValueError('Selection evidence integrity mismatch')
    if m['arm']=='Candidate':
        for name,expected in m['adapter_files'].items():
            p=ROOT/'models/logical'/name
            if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('Selected adapter integrity mismatch: '+name)
    return m
def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('recommend');g=q.add_mutually_exclusive_group(required=True)
    g.add_argument('--input',type=Path);g.add_argument('--input-dir',type=Path)
    q.add_argument('--output',required=True,type=Path);q.add_argument('--base-path',type=Path)
    q=sub.add_parser('discover');q.add_argument('--input',required=True,type=Path)
    q.add_argument('--output',required=True,type=Path);q.add_argument('--base-path',type=Path)
    q.add_argument('--maximum-rounds',type=int,default=4,choices=range(1,13))
    q=sub.add_parser('discover-roots');q.add_argument('--input',required=True,type=Path)
    q.add_argument('--output',required=True,type=Path);q.add_argument('--base-path',type=Path)
    q.add_argument('--root',action='append');q.add_argument('--maximum-roots',type=int,default=8,choices=range(1,33))
    for name in ['serve','export']:
        q=sub.add_parser(name);q.add_argument('--rules',required=True,type=Path);q.add_argument('--output',required=True,type=Path)
        q.add_argument('--source'if name=='export'else'--input',required=True,type=Path)
        if name=='serve':q.add_argument('--bulk',action='store_true')
    for name in ['replay-esb-export','replay-esb-status']:
        q=sub.add_parser(name);q.add_argument('--data-root',type=Path,required=True)
        q.add_argument('--source',type=Path,required=True);q.add_argument('--rules',type=Path,required=True)
        q.add_argument('--output',type=Path,required=True);q.add_argument('--families-per-batch',type=int,default=500)
    q=sub.add_parser('library-build');q.add_argument('--packs',nargs='+',required=True,type=Path)
    q.add_argument('--output',required=True,type=Path)
    q=sub.add_parser('library-serve');q.add_argument('--library',required=True,type=Path)
    q.add_argument('--input',required=True,type=Path);q.add_argument('--output',required=True,type=Path)
    q.add_argument('--bulk',action='store_true')
    sub.add_parser('check')
    a=p.parse_args();m=check_selected()
    if a.command=='check':print(json.dumps({'selected':m,'automatic_download':False,'automatic_model_fallback':False}));return
    if a.command in ['recommend','discover','discover-roots']:
        if a.base_path:os.environ['SPEC_BASE_PATH']=str(a.base_path.resolve())
        if not os.environ.get('SPEC_BASE_PATH'):raise ValueError('Provide --base-path or SPEC_BASE_PATH; no download')
        sys.argv=['run_discovery','--output',str(a.output),'--arm',m['arm']]
        if m['arm']=='Candidate':sys.argv+=['--candidate',str(ROOT/'models/logical')]
        if a.command=='discover-roots':
            sys.argv+=['--input',str(a.input),'--maximum-roots',str(a.maximum_roots)]
            for root in a.root or []:sys.argv+=['--root',root]
            from discover_by_root import main as run
            run()
        elif a.command=='discover':
            sys.argv+=['--input',str(a.input),'--maximum-rounds',str(a.maximum_rounds),'--logic-review']
            from discover_rule_library import main as run
            run()
        else:
            sys.argv+=['--source-review','--execution-feedback','--strict-input-guards']
            sys.argv+=['--input',str(a.input)]if a.input else['--input-dir',str(a.input_dir)]
            from run_logical_completion import main as run
            run()
            summary=json.loads((a.output/'summary.json').read_text())
            if any(not x['source_valid']for x in summary['items']):raise SystemExit(2)
    elif a.command=='serve':
        sys.argv=['serve_rules','--rules',str(a.rules),'--input',str(a.input),'--output',str(a.output)]
        if a.bulk:sys.argv+=['--bulk']
        from serve_rules import main as run
        run()
    elif a.command=='export':
        sys.argv=['export_contracts','--rules',str(a.rules),'--source',str(a.source),'--output',str(a.output)]
        from export_contracts import main as run
        run()
    elif a.command in ['replay-esb-export','replay-esb-status']:
        sys.argv=['replay_esb','--data-root',str(a.data_root),'--source',str(a.source),
            '--rules',str(a.rules),'--output',str(a.output),'--families-per-batch',str(a.families_per_batch)]
        import replay_esb_export_observation as replay
        if a.command=='replay-esb-status':
            replay.TABLES={
                'TransactionStatusExport':('EC_TRANSACTION_MONITORING.csv',('TRANSACTION_ID','STATUS','RETRY_COUNT')),
                'ProcessStatusExport':('EC_PROCESS_MONITORING.csv',('TRANSACTION_ID','PROCESS_ID','STATUS','RETRY_COUNT'))}
        replay.main()
    else:
        sys.argv=['logical_rule_library','build'if a.command=='library-build'else'serve','--output',str(a.output)]
        if a.command=='library-build':sys.argv+=['--packs']+[str(q)for q in a.packs]
        else:
            sys.argv+=['--library',str(a.library),'--input',str(a.input)]
            if a.bulk:sys.argv+=['--bulk']
        from logical_rule_library import main as run
        run()
if __name__=='__main__':main()
