"""Source- and model-bound boundary around an unchanged-source recommendation.

No predicate rewriting, candidate pruning, extra model retries or fitting. The
previous adapter and all attempts remain intact in attempt/. Successful execution
is a proposal for review, not approval or proof of exhaustive discovery.
"""
from pathlib import Path
import argparse, hashlib, json, subprocess, sys, time


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value):
    with path.open('x')as stream:json.dump(value,stream,indent=2);stream.write('\n')


def audit_actual_result(bundle, source, attempt, requested_roots=None):
    raw=read(attempt/'result.json');issues=list(raw.get('issues',[]));original=sha(source)
    if raw.get('state')!='completed_for_review':issues.append('original_scope_request_unavailable')
    selected=read(bundle/'selected_pipeline.json')['adapter_files']['adapter_model.safetensors']
    base_revision=read(bundle/'model_manifest.json')['Base']['revision']
    status=raw.get('request_status')or{}
    if status.get('input_sha256')!=original:issues.append('request_input_sha_mismatch')
    if raw.get('input_sha256')!=original:issues.append('adapter_input_sha_mismatch')
    wanted=list(dict.fromkeys(requested_roots if requested_roots is not None else raw.get('requested_roots',[])))
    if raw.get('requested_roots')!=wanted:issues.append('requested_roots_record_mismatch')
    pack_path=Path(raw['actual_pack'])if raw.get('actual_pack')else None
    responses=[]
    if pack_path and pack_path.is_file()and pack_path.resolve().is_relative_to(attempt.resolve()):
        pack=read(pack_path)
        if pack.get('source_file_sha256')!=original:issues.append('actual_pack_source_sha_mismatch')
        if pack.get('actual_model_weight_sha')!=selected:issues.append('actual_pack_selected_model_mismatch')
        if pack.get('Base_revision')!=base_revision:issues.append('actual_pack_base_revision_mismatch')
        if raw.get('actual_pack_sha256')!=sha(pack_path):issues.append('actual_pack_bytes_changed')
        roots={c['plan']['select']['table']for c in pack['proposal']['candidates']if c['plan']is not None}
        if roots!=set(wanted):issues.append('actual_model_roots_do_not_exactly_cover_requested_scope')
        library_path=attempt/'library/library.json'
        library=read(library_path)if library_path.is_file()else{}
        entries=library.get('packs',[])
        if len(entries)!=1 or entries[0].get('pack')!=pack:issues.append('whole_actual_pack_not_preserved_in_library')
        if library.get('operational_approval')is not False:issues.append('unexpected_library_operational_approval')
        for response in sorted(pack_path.parent.glob('*/output.json')):
            receipt=read(response);model=receipt.get('model',{})
            if model.get('adapter_weight_sha')!=selected:issues.append('response_receipt_model_mismatch')
            if model.get('Base',{}).get('revision')!=base_revision:issues.append('response_receipt_base_revision_mismatch')
            responses.append(dict(path=str(response),sha256=sha(response)))
        if not responses:issues.append('no_completed_actual_model_response')
    else:issues.append('actual_pack_missing_or_outside_attempt')
    return dict(policy='unchanged-source-scope-adapter1.1-source-model-bound',
                state='unavailable'if issues else'completed_for_review',issues=issues,
                original_input_sha256=original,selected_model_weight_sha256=selected,
                actual_completed_model_responses=len(responses),actual_response_files=responses,
                request_ledger_reservations=status.get('model_call_ledger_reservations'),
                reservation_count_is_not_completed_responses=True,
                source_question_schema_or_records_rewritten=False,manual_predicate_rewrite=False,
                candidate_subset_pruned=False,raw_attempts_preserved_under=str(attempt),
                operational_approval=False,model_or_threshold_fit=False,automatic_retry=False,
                source_result=raw)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['bundle','input','base-path','output']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--root',required=True,action='append');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);start=time.monotonic();failure=None
    try:
        source=read(a.input)
        if not isinstance(source,dict)or not isinstance(source.get('schema'),dict):raise ValueError('JSON source object with schema required')
        wanted=list(dict.fromkeys(a.root))
        if not 1<=len(wanted)<=32 or any(root not in source['schema']for root in wanted):raise ValueError('Requested roots must exist in the source schema')
    except Exception as exc:failure='input_admission_failed: '+type(exc).__name__+': '+str(exc)
    if failure:
        record=dict(policy='unchanged-source-scope-adapter1.1-source-model-bound',state='unavailable',issues=[failure],actual_completed_model_responses=0,operational_approval=False,automatic_retry=False,model_or_threshold_fit=False)
    else:
        command=[sys.executable,'-B',str(Path(__file__).with_name('recommend_with_original_scope.py')),
                 '--bundle',str(a.bundle.resolve()),'--input',str(a.input.resolve()),
                 '--base-path',str(a.base_path.resolve()),'--output',str(a.output.resolve()/'attempt')]
        for root in a.root:command+=['--root',root]
        with (a.output/'stdout.log').open('x')as out,(a.output/'stderr.log').open('x')as err:
            result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err)
        try:record=audit_actual_result(a.bundle.resolve(),a.input.resolve(),a.output.resolve()/'attempt',a.root)
        except Exception as exc:record=dict(policy='unchanged-source-scope-adapter1.1-source-model-bound',state='unavailable',issues=['post_execution_audit_failed: '+type(exc).__name__+': '+str(exc)],raw_attempts_preserved_under=str(a.output/'attempt'),operational_approval=False,automatic_retry=False,model_or_threshold_fit=False)
        if result.returncode and record['state']=='completed_for_review':record['state']='unavailable';record['issues'].append('original_adapter_nonzero_exit')
    record['seconds']=time.monotonic()-start;write(a.output/'result.json',record)
    print(json.dumps({k:record[k]for k in ['policy','state','issues','seconds']}))
    if record['state']!='completed_for_review':raise SystemExit(2)

if __name__=='__main__':main()
