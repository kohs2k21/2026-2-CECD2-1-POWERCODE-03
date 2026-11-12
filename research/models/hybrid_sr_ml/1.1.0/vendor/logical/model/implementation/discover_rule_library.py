"""Accumulate source-backed local proposals without imposing a rule-count target.

Each round uses the existing pinned recommendation runtime in a separate process.
Old proposals remain unapproved and are never promoted to evidence or teacher labels.
"""
from pathlib import Path
from copy import deepcopy
import argparse,json,os,subprocess,sys,time,hashlib
from run import PACKAGE
from specmodel.io import read,write,digest,sha
from logical_rule_library import build_library

def signature(candidate):
    return digest({'role':candidate['role'],'plan':candidate['plan'],
                   'requires_complete_capture':candidate['requires_complete_capture']})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--arm',choices=['Base','Candidate'],required=True);p.add_argument('--candidate',type=Path)
    p.add_argument('--maximum-rounds',type=int,default=4,choices=range(1,13))
    p.add_argument('--logic-review',action='store_true',help='Use the versioned generic logical-completeness review extension')
    a=p.parse_args()
    if a.arm=='Candidate'and not a.candidate:raise ValueError('Explicit tested adapter required')
    original=read(a.input);a.output.mkdir(parents=True,exist_ok=False)
    write(a.output/'input_pin.json',{'path':str(a.input.resolve()),'sha256':sha(a.input),
        'maximum_rounds':a.maximum_rounds,'maximum_calls':3*a.maximum_rounds,'count_target':None,
        'teacher_or_private_answers_used':False,'rounds_are_development_not_independent_tests':True})
    packs=[];seen={};ledger=[];start=time.monotonic();stop=None
    for index in range(a.maximum_rounds):
        folder=a.output/f'round_{index+1:02d}';folder.mkdir();case=deepcopy(original)
        if seen:
            previous=[{'role':c['role'],'plan':c['plan'],
                       'requires_complete_capture':c['requires_complete_capture']}for c in seen.values()]
            case['question']=original['question']+'\nAdditional-source-coverage pass. These previously proposed calculations are UNAPPROVED, not business evidence or correct teacher answers. Re-read the original documents. Propose materially different justified checks that were omitted, or return zero candidates with a specific reason when no additional check is supported. Do not invent checks to reach a count. Previously proposed calculations: '+json.dumps(previous,separators=(',',':'))
        inp=folder/'input.json';write(inp,case)
        command=[sys.executable,'-B',str(Path(__file__).with_name('run_logical_completion.py'if a.logic_review else'run_discovery.py')),'--input',str(inp),
                 '--output',str(folder/'generation'),'--arm',a.arm,'--source-review','--execution-feedback','--strict-input-guards']
        if a.candidate:command+=['--candidate',str(a.candidate.resolve())]
        with(folder/'execution.log').open('x')as log:
            completed=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
        reservations=folder/'generation/model_calls.jsonl'
        reserved_attempts=len(reservations.read_text().splitlines())if reservations.exists()else 0
        completed_responses=len(list((folder/'generation').glob('input/*/output.json')))
        if completed.returncode:
            ledger.append({'round':index+1,'exit_code':completed.returncode,'accepted_as_source_valid_pack':False,
                           'reserved_attempts':reserved_attempts,'completed_model_responses':completed_responses})
            stop='runtime_failure_preserved';break
        pack=read(folder/'generation/input/candidate_pack.json')
        summary=read(folder/'generation/summary.json')
        if not pack['execution']['executable_valid']:
            ledger.append({'round':index+1,'source_valid':False,'errors':pack['execution']['errors'],
                           'reserved_attempts':reserved_attempts,'completed_model_responses':completed_responses,
                           'accepted_as_source_valid_pack':False})
            stop='source_invalid_round_preserved_not_silently_dropped';break
        packs.append(pack);new=[];holds=0
        for c in pack['proposal']['candidates']:
            if c['plan']is None:holds+=1;continue
            sig=signature(c)
            if sig not in seen:seen[sig]=c;new.append(sig)
        ledger.append({'round':index+1,'source_valid':True,'proposed_candidates':len(pack['proposal']['candidates']),
                       'evidence_requests':holds,'new_executable_calculations':len(new),'cumulative_unique_calculations':len(seen),
                       'reserved_attempts':reserved_attempts,'completed_model_responses':completed_responses,
                       'pack_sha256':sha(folder/'generation/input/candidate_pack.json'),
                       'semantic_review':'pending; source/type validity does not establish meaning'})
        write(a.output/'progress.json',ledger)
        if not new:
            stop='no_additional_executable_calculation_or_only_duplicates_or_holds';break
    if stop is None:stop='registered_round_budget_reached; coverage_not_proven_exhaustive'
    if packs:write(a.output/'library.json',build_library(packs))
    result={'rounds':ledger,'stop_reason':stop,'source_valid_packs':len(packs),
       'unique_executable_calculations':len(seen),
       'calculation_count_scope':'Exact plan AST + role + capture requirement signatures; not algebraically normalized features or approved rules.',
       'completed_model_responses':sum(r.get('completed_model_responses',0)for r in ledger),
       'reserved_attempts_including_failures':sum(r.get('reserved_attempts',0)for r in ledger),
       'seconds':time.monotonic()-start,'source_coverage_not_proven_exhaustive':True,
       'AI_proposals_operationally_approved':0,'SFT_or_ML_fit':False,'all_original_round_outputs_preserved':True}
    result['logical_completeness_review']=a.logic_review
    write(a.output/'summary.json',result);print(json.dumps(result))
    if stop.startswith(('runtime_failure','source_invalid')):raise SystemExit(2)

if __name__=='__main__':main()
