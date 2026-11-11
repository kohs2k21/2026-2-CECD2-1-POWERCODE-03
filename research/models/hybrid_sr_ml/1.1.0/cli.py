"""Frozen hybrid research model: capture/train separately, detect without LLMs."""
from pathlib import Path
import argparse,json,subprocess,sys
from hybrid.common import ROOT,read,verify_bundle

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    s.add_parser('inspect')
    q=s.add_parser('detect-esb');q.add_argument('--capture',required=True);q.add_argument('--output',required=True);q.add_argument('--models');q.add_argument('--ratio-policy',choices=['fallback','strict'],default='fallback')
    q=s.add_parser('detect-flowbench');q.add_argument('--workflow',choices=['1000genome','montage'],required=True);q.add_argument('--output',required=True);g=q.add_mutually_exclusive_group(required=True);g.add_argument('--input-dir');g.add_argument('--run-list')
    q=s.add_parser('capture-esb')
    for name in ['transactions','processes','messages','start','end','output']:q.add_argument('--'+name,required=True)
    q.add_argument('--split',choices=['train','calibration','threshold','test','application'],required=True);q.add_argument('--data-class',choices=['approved_real','controlled'],required=True)
    for flag in ['complete-family-capture','held-lineage-excluded','accept-frozen-reference','confirm-same-hr-contract']:q.add_argument('--'+flag,action='store_true')
    q=s.add_parser('train-esb')
    for name in ['train','calibration','threshold','output']:q.add_argument('--'+name,required=True)
    q=s.add_parser('recommend-logical')
    for name in ['input','base-path','output']:q.add_argument('--'+name,required=True)
    q.add_argument('--root',required=True,action='append')
    a=p.parse_args();verify_bundle()
    if a.command=='inspect':result=read(ROOT/'model.json')
    elif a.command=='detect-esb':
        from hybrid.runner import detect_esb
        result=detect_esb(a.capture,a.output,a.models,a.ratio_policy)
    elif a.command=='detect-flowbench':
        from hybrid.runner import detect_flowbench
        result=detect_flowbench(a.workflow,a.output,a.input_dir,a.run_list)
    elif a.command=='recommend-logical':
        cmd=[sys.executable,'-B',str(ROOT/'vendor/logical/integration/scope_safe_request.py'),'--bundle',str(ROOT/'vendor/logical/model')]
        for k in ['input','base_path','output']:cmd+=['--'+k.replace('_','-'),getattr(a,k)]
        for root in a.root:cmd+=['--root',root]
        # User must inspect exposed GPU availability before this optional command.
        # The fixed detector never calls this design-time LLM path.
        return subprocess.run(cmd,stdin=subprocess.DEVNULL).returncode
    else:
        import os
        env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'vendor/numeric');env['PYTHONDONTWRITEBYTECODE']='1'
        cmd=[sys.executable,'-B','-m','research.integration.feature_rule_ml.shared42','train' if a.command=='train-esb' else 'capture']
        for key,value in vars(a).items():
            if key=='command' or value is False:continue
            flag='--'+key.replace('_','-');cmd.append(flag)
            if value is not True:cmd.append(str(value))
        return subprocess.run(cmd,env=env,stdin=subprocess.DEVNULL).returncode
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 2 if result.get('state') in ('unavailable','completed_with_unavailable') else 0

if __name__=='__main__':raise SystemExit(main())
