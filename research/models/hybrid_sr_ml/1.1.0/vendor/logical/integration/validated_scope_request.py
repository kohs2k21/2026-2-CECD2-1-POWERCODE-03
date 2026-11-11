"""Authority admission before the unchanged source/model-bound request."""
from pathlib import Path
import argparse,json,subprocess,sys,time
from preflight_source import audit
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['bundle','input','base-path','output']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--root',action='append',required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);start=time.monotonic();record=audit(a.bundle,a.input)
    (a.output/'admission.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    if record['state']!='admitted':
        result=dict(state='unavailable',issues=record['issues'],actual_completed_model_responses=0,
                    operational_approval=False,automatic_authority_upgrade=False,seconds=time.monotonic()-start)
        (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));raise SystemExit(2)
    command=[sys.executable,'-B',str(Path(__file__).with_name('scope_safe_request.py')),
             '--bundle',str(a.bundle.resolve()),'--input',str(a.input.resolve()),
             '--base-path',str(a.base_path.resolve()),'--output',str(a.output.resolve()/'request')]
    for root in a.root:command+=['--root',root]
    result=subprocess.run(command,stdin=subprocess.DEVNULL)
    if (a.output/'request/result.json').is_file():record=json.loads((a.output/'request/result.json').read_text())
    else:record=dict(state='unavailable',issues=['underlying_request_incomplete'],operational_approval=False)
    record=dict(record,source_admission_policy='logical-source-authority-admission1.0',
                authority_is_automatically_upgraded=False,total_boundary_seconds=time.monotonic()-start)
    (a.output/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    raise SystemExit(result.returncode)
if __name__=='__main__':main()
