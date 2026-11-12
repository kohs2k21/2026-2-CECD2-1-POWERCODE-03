"""Keep the caller's source question unchanged while checking requested roots.

This adapter does not generate rule conditions, prune a pack, retry or fit anything.
It accepts a whole actual pack only if every requested root is represented and no
executable candidate has an unrequested root. Missing coverage is unavailable.
Operational review remains required. The selected bundle is not modified.
"""
from pathlib import Path
import argparse, hashlib, json, subprocess, sys, time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--root', required=True, action='append')
    parser.add_argument('--base-path', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    start=time.monotonic();source=json.loads(a.input.read_text());wanted=list(dict.fromkeys(a.root))
    original=sha(a.input);issues=[];covered=[];pack_path=None;status=None
    if not 1<=len(wanted)<=32 or any(root not in source.get('schema',{})for root in wanted):
        issues.append('requested_root_not_in_source_schema_or_outside_limit')
    else:
        command=[sys.executable,'-B',str(a.bundle/'run_request.py'),'--input',str(a.input.resolve()),
                 '--base-path',str(a.base_path.resolve()),'--output',str(a.output.resolve()/'request')]
        with (a.output/'stdout.log').open('x')as stdout,(a.output/'stderr.log').open('x')as stderr:
            result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
        status_path=a.output/'request/request_status.json'
        status=json.loads(status_path.read_text())if status_path.is_file()else None
        if result.returncode or not status or status['request_status']!='completed_for_review':
            issues.append('selected_recommendation_unavailable')
        else:
            pack_path=a.output/'request/generation'/a.input.stem/'candidate_pack.json'
            pack=json.loads(pack_path.read_text())
            if not pack['execution'].get('executable_valid'):issues.append('invalid_actual_model_pack')
            roots=[c['plan']['select']['table']for c in pack['proposal']['candidates']if c['plan']is not None]
            covered=sorted(set(roots));missing=sorted(set(wanted)-set(covered));outside=sorted(set(covered)-set(wanted))
            if missing:issues.append({'requested_roots_without_executable_candidate':missing})
            if outside:issues.append({'actual_candidates_have_unrequested_roots':outside})
            if not issues:
                command=[sys.executable,'-B',str(a.bundle/'cli.py'),'library-build','--packs',str(pack_path),
                         '--output',str(a.output.resolve()/'library')]
                with (a.output/'library.stdout.log').open('x')as stdout,(a.output/'library.stderr.log').open('x')as stderr:
                    result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
                if result.returncode:issues.append('whole_pack_library_build_failed')
    if sha(a.input)!=original:issues.append('source_changed_during_request')
    record=dict(policy='unchanged-source-scope-adapter1.0',state='unavailable'if issues else'completed_for_review',
                requested_roots=wanted,actual_executable_roots=covered,issues=issues,
                actual_pack=str(pack_path)if pack_path else None,actual_pack_sha256=sha(pack_path)if pack_path and pack_path.is_file()else None,
                input_sha256=original,source_file_unchanged=sha(a.input)==original,
                model_input_question_or_schema_rewritten=False,manual_predicate_rewrite=False,
                candidate_subset_pruned=False,all_raw_attempts_preserved=True,automatic_retry=False,
                operational_approval=False,model_or_threshold_fit=False,seconds=time.monotonic()-start,
                request_status=status)
    write(a.output/'result.json',record)
    print(json.dumps({k:record[k]for k in ['state','requested_roots','actual_executable_roots','issues','seconds']}))
    if issues:raise SystemExit(2)

if __name__=='__main__':main()
