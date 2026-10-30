"""Official entry: inspect, feature, validate-feature, rule, pipeline, replay."""
from pathlib import Path
import argparse, json, sys, os, traceback, copy
from .io import *
from .dataset import Dataset
from .calculations import compile_feature, compact_bundle
from .contracts import feature_record, feature_schema
from .validation import validate_feature
from .pipeline import feature_flow, rule_flow, run_pipeline, export_result
from .local_models import LocalModels
from .rule_logic import validate_rule
PACKAGE = Path(__file__).resolve().parent.parent

def verify_origin(folder, feature):
    if feature.get('source_feature_ref'):
        ref = feature['source_feature_ref']
        path = Path(ref['path'])
        if sha(path) != ref['sha256']:
            raise InputFailure('Referenced Feature bundle changed')
        old = read(path)
        if old['proposal'] != feature['proposal']:
            raise InputFailure('Reused proposal differs from origin')
        return verify_origin(path.parent, old)
    if feature.get('origin') == 'provided_definition_CPU':
        return
    attempts = feature.get('attempts')
    if not attempts:
        raise InputFailure('Unknown feature origin; no stored response fallback')
    parent = None
    if feature.get('preserved_seed_prediction') is not None:
        receipt = feature.get('seed_source_pin')
        if not receipt or sha(receipt['path']) != receipt['sha256'] or read(receipt['path'])['parsed'] != feature['preserved_seed_prediction']:
            raise InputFailure('Seed Model response provenance changed')
        parent = copy.deepcopy(feature['preserved_seed_prediction'])
    for a in attempts:
        ref = a['actual_model_origin']
        raw = read(folder / ref['file'])
        if digest(raw['raw']) != ref['raw_sha'] or digest(raw['parsed']) != ref['parsed_sha']:
            raise InputFailure('Model raw response changed')
        if a.get('partial_stop_reason'):
            if parent is None:
                raise InputFailure('Candidate0 explanation repair has no original Model parent')
            p = copy.deepcopy(parent)
            p['stop_reason'] = raw['parsed']['stop_reason']
        elif a.get('partial_Model_expression_and_prose'):
            if parent is None:
                parent = copy.deepcopy(feature.get('preserved_seed_prediction'))
            if parent is None:
                raise InputFailure('Partial expression repair has no preserved original Model parent')
            p = copy.deepcopy(parent)
            p['candidates'][0]['plan']['expression'] = raw['parsed']['expression']
            if 'result_use' in raw['parsed']:
                p['candidates'][0]['plan']['result_use'] = raw['parsed']['result_use']
            for k in ['purpose', 'meaning', 'needed_evidence']:
                p['candidates'][0][k] = raw['parsed'][k]
        elif a.get('partial_Model_prose_fields'):
            if parent is None:
                raise InputFailure('Partial repair has no preserved Model parent')
            p = copy.deepcopy(parent)
            for k in a['partial_Model_prose_fields']:
                p['candidates'][0][k] = raw['parsed'][k]
        else:
            p = raw['parsed']
        if p != a['prediction']:
            raise InputFailure('Assembled candidate differs from exact Model fields')
        parent = p
    if feature['status'] == 'feature_verified':
        if not parent or not parent.get('candidates') or parent['candidates'][0] != feature['proposal']:
            raise InputFailure('Final proposal is not the actual assembled Model response')
    elif feature['status'] == 'no_candidate_review':
        if parent is None or parent.get('candidates') or parent['stop_reason'] != feature['reason']:
            raise InputFailure('Saved candidate0 reason differs from Model output')
    elif feature['status'] != 'feature_failed':
        raise InputFailure('Unsupported saved feature status')

def verify_rule_origin(folder, rule):
    previous = None
    if rule.get('preserved_seed_prediction') is not None:
        receipt = rule.get('seed_source_pin')
        if not receipt or sha(receipt['path']) != receipt['sha256'] or read(receipt['path'])['parsed'] != rule['preserved_seed_prediction']:
            raise InputFailure('Seed Rule Model provenance changed')
        previous = copy.deepcopy(rule['preserved_seed_prediction'])
    from .contracts import rule_schema
    import jsonschema
    for a in rule['attempts']:
        ref = a['actual_model_origin']
        raw = read(folder / ref['file'])
        if digest(raw['raw']) != ref['raw_sha'] or digest(raw['parsed']) != ref['parsed_sha']:
            raise InputFailure('Rule Model raw response changed')
        q = raw['parsed']
        subset = a.get('partial_roles')
        expected = q
        if q is not None and a.get('partial_Rule_prose_fields'):
            if previous is None:
                raise InputFailure('Rule prose delta has no preserved full Model response')
            expected = copy.deepcopy(previous)
            updates = {d['request']: d for d in q['updates']}
            for d in expected['decisions']:
                for field in a['partial_Rule_prose_fields'].get(d['request'], []):
                    d[field] = updates[d['request']][field]
        elif q is not None and subset and isinstance(previous, dict) and ('decisions' in previous) and (not list(jsonschema.Draft202012Validator(rule_schema(subset)).iter_errors(q))):
            if q['feature'] == previous['feature'] and q['sha'] == previous['sha']:
                expected = copy.deepcopy(previous)
                replacements = {d['request']: d for d in q['decisions']}
                for (n, d) in enumerate(expected['decisions']):
                    if d['request'] in subset:
                        expected['decisions'][n] = replacements[d['request']]
        if expected != a['prediction']:
            raise InputFailure('Saved Rule assembly differs from exact Model fields')
        previous = expected
    if previous != rule['prediction']:
        raise InputFailure('Final Rule prediction differs from its Model attempts')

def replay(folder, source=None, request=None, reference=None):
    folder = Path(folder).resolve()
    manifest = read(folder / 'run_manifest.json')
    for x in manifest['files']:
        if sha(folder / x['file']) != x['sha256']:
            raise InputFailure('Saved artifact changed: ' + x['file'])
    f = read(folder / 'feature_bundle.json')
    verify_origin(folder, f)
    result = {'saved_artifacts': True, 'raw_Model_provenance': True, 'source_remeasured': False, 'new_Model_calls': 0}
    if source and f['status'] == 'feature_verified':
        ds = Dataset(source)
        c = compile_feature(ds, f['proposal'], 'saved_output_CPU_replay')
        if not c['passed']:
            raise InputFailure('Saved feature no longer compiles: ' + repr(c['errors']))
        old = f['compilation']['compiled_feature']
        new = c['compiled_feature']
        for k in ['definition_sha', 'source_version', 'schema_version']:
            if new[k] != old[k]:
                raise InputFailure('Saved definition/source version differs: ' + k)
        if new['measurement']['measurement_sha256'] != old['measurement']['measurement_sha256']:
            raise InputFailure('Saved measurement differs')
        if old['meaning'] != f['proposal']['meaning'] or old['description_revision']['sha'] != digest({'purpose': f['proposal']['purpose'], 'meaning': f['proposal']['meaning']}):
            raise InputFailure('Compiled description is stale')
        if request:
            errs = validate_feature(ds, read(request), {'schema': ('feature5.2' if 'result_use' in f['proposal']['plan'] else 'feature5.1'), 'candidates': [f['proposal']], 'stop_reason': None}, c)
            if errs:
                raise InputFailure('Saved feature semantic facts conflict: ' + repr(errs))
        values = read(folder / 'feature_values.json')
        expected_values = json.loads(json.dumps(c['values'], default=json_default))
        if values['values'] != expected_values or values['source_row_keys'] != c['row_ids']:
            raise InputFailure('Saved output values/row correspondence differ from fresh measurement')
        if (folder / 'rule_bundle.json').exists():
            rule = read(folder / 'rule_bundle.json')
            verify_rule_origin(folder, rule)
            from .rule_logic import make_rule_record
            plan = read(folder / 'public_rule_plan.json')
            ref = Dataset(reference) if reference else None
            record = make_rule_record(ds, ref, f['proposal'], c, plan)
            original = read(folder / 'rule_input.json')['input']
            if digest(record['input']) != digest(original):
                raise InputFailure('Saved Rule input/evidence differs from current source/reference')
            errs = validate_rule(record, rule['prediction'])
            if rule['status'] == 'rule_verified' and errs:
                raise InputFailure('Saved approved Rule fails current checks: ' + repr(errs))
            if rule['status'] == 'rule_verified':
                from .rule_logic import counts
                proposed = [d for d in rule['prediction']['decisions'] if d['state'] == 'proposed']
                pending = [d for d in rule['prediction']['decisions'] if d['state'] != 'proposed']
                for (file, decisions) in [('validated_rules.json', proposed), ('pending_decisions.json', pending)]:
                    saved = read(folder / file)
                    if [x['decision'] for x in saved] != decisions:
                        raise InputFailure('Exported decisions differ from Model Rule bundle')
                    for (x, d) in zip(saved, decisions):
                        if x['feature'] != rule['feature_reference']:
                            raise InputFailure('Exported Rule feature version differs')
                        expected_counts = counts(c['values'], d['condition']) if d['condition'] else None
                        if x['condition_statistics'] != expected_counts:
                            raise InputFailure('Exported Rule condition denominator differs')
            for d in rule['prediction']['decisions']:
                if d['request'] not in original['requests']:
                    raise InputFailure('Role coverage changed')
        result.update(source_remeasured=True, independent_calculation=c['compiled_feature']['independent_calculation'], definition_sha=new['definition_sha'], source_version=new['source_version'])
    return result

def main(argv=None):
    actual = sys.argv[1:] if argv is None else argv
    if actual and actual[0] == 'batch':
        from .batch_runner import run
        return run(actual[1:])
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ['inspect', 'feature', 'validate-feature', 'rule', 'pipeline']:
        q = sub.add_parser(name)
        q.add_argument('--input-manifest', type=Path, required=True)
        q.add_argument('--output', type=Path, required=True)
        q.add_argument('--request', type=Path)
        q.add_argument('--base-path', type=Path, default=os.environ.get('SPEC_BASE_PATH'))
        q.add_argument('--rule-adapter', type=Path)
        q.add_argument('--feature-candidate', choices=['final_silver51'], help='Explicit compatibility alias for a pinned Feature adapter; default is model_manifest selection')
        q.add_argument('--reference-manifest', type=Path)
        q.add_argument('--rule-plan', type=Path)
        q.add_argument('--feature-bundle', type=Path)
        q.add_argument('--proposal', type=Path)
        q.add_argument('--max-model-calls', type=int, default=6)
        q.add_argument('--shared-experiment-plan', type=Path)
        q.add_argument('--phase', default='user_run')
    q = sub.add_parser('replay')
    q.add_argument('--run', type=Path, required=True)
    q.add_argument('--suite', type=Path)
    q.add_argument('--input-manifest', type=Path)
    q.add_argument('--request', type=Path)
    q.add_argument('--reference-manifest', type=Path)
    q.add_argument('--output', type=Path, required=True)
    a = p.parse_args(argv)
    out = None
    try:
        out = new_output(a.output)
        if a.command == 'replay':
            if (a.run / 'manifest.json').exists() and (a.run / 'validation_report.json').exists():
                if not a.suite:
                    raise InputFailure('Batch replay requires --suite with its exact input receipts')
                from .batch_runner import replay_batch
                result = replay_batch(a.run, a.suite)
            else:
                result = replay(a.run, a.input_manifest, a.request, a.reference_manifest)
            write(out / 'replay.json', result)
            print(json.dumps(result, ensure_ascii=False, default=str))
            return0 = 0
            return return0
        ds = Dataset(a.input_manifest)
        write(out / 'source_admission.json', {'manifest_sha': sha(ds.path), 'source_version': ds.source_version, 'inventory': ds.inventory, 'row_counts': {t: len(r) for (t, r) in ds.rows.items()}, 'Body_excluded': True, 'held_lineage_excluded': True})
        if a.command == 'inspect':
            write(out / 'profile.json', ds.profile())
            if a.request:
                write(out / 'prepared_feature_input.json', feature_record(ds, read(a.request)))
            write(out / 'counts.json', {'tables': len(ds.rows), 'source_rows': sum(map(len, ds.rows.values())), 'Model_calls': 0})
            print('Input/profile ready; no Model loaded.')
            return0 = 0
            return return0
        if a.command == 'validate-feature':
            if not a.proposal:
                raise InputFailure('--proposal required for explicit CPU-only definition validation')
            prop = read(a.proposal)
            c = compile_feature(ds, prop, 'provided_definition_CPU')
            errs = validate_feature(ds, read(a.request) if a.request else {'question': 'Provided definition CPU review'}, {'schema': ('feature5.2' if 'result_use' in prop['plan'] else 'feature5.1'), 'candidates': [prop], 'stop_reason': None}, c)
            f = {'status': 'feature_verified' if c['passed'] and (not errs) else 'feature_failed', 'origin': 'provided_definition_CPU', 'proposal': prop, 'compilation': compact_bundle(c), 'input_definition_sha': sha(a.proposal), 'Model_generated': False, 'errors': errs}
            write(out / 'feature_bundle.json', f)
            export_result(out, f, c)
            print(json.dumps({'status': f['status'], 'Model_calls': 0}))
            return0 = 0 if f['status'] == 'feature_verified' else 2
            return return0
        budget = Budget(out, a.max_model_calls, a.shared_experiment_plan, a.phase)
        candidate = None
        arm = 'selected'
        if a.feature_candidate:
            entry = read(PACKAGE / 'model_manifest.json')['Feature_candidates'][a.feature_candidate]
            candidate = PACKAGE / entry['adapter']
            if sha(candidate / 'adapter_model.safetensors') != entry['weight_sha256']:
                raise ModelFailure('Explicit candidate weight SHA mismatch')
            arm = 'candidate'
        model = LocalModels(a.base_path, a.rule_adapter, candidate)
        reference = Dataset(a.reference_manifest) if a.reference_manifest else None
        if a.command in ['feature', 'pipeline'] and (not a.request):
            raise InputFailure('--request required')
        if a.command in ['rule', 'pipeline'] and (not a.rule_plan):
            raise InputFailure('--rule-plan required; no identical diagnostic/role bundle is auto-applied')
        request = read(a.request) if a.request else None
        if request:
            write(out / 'public_request.json', request)
        plan = read(a.rule_plan) if a.rule_plan else None
        if plan:
            write(out / 'public_rule_plan.json', plan)
        if a.command == 'pipeline':
            result = run_pipeline(ds, reference, request, plan, model, budget, out, arm=arm)
        elif a.command == 'feature':
            (f, c) = feature_flow(ds, request, model, budget, out, arm=arm)
            result = export_result(out, f, c)
        else:
            if not a.feature_bundle:
                raise InputFailure('--feature-bundle required')
            old = read(a.feature_bundle)
            if old.get('status') != 'feature_verified':
                raise InputFailure('Rule-only path requires a verified Feature bundle; failed outputs are not silently promoted')
            verify_origin(a.feature_bundle.parent, old)
            c = compile_feature(ds, old['proposal'], 'Rule_input_revalidation')
            if not c['passed']:
                raise InputFailure('Feature is not currently measurable')
            for k in ['definition_sha', 'source_version']:
                if c['compiled_feature'][k] != old['compilation']['compiled_feature'][k]:
                    raise InputFailure('Feature/source changed before Rule generation')
            if c['compiled_feature']['measurement']['measurement_sha256'] != old['compilation']['compiled_feature']['measurement']['measurement_sha256']:
                raise InputFailure('Feature measurement changed before Rule generation')
            f = {**old, 'origin': 'explicit_verified_definition_reuse_not_new_Feature_generation', 'source_feature_ref': {'path': str(a.feature_bundle.resolve()), 'sha256': sha(a.feature_bundle)}}
            write(out / 'feature_bundle.json', f)
            r = rule_flow(ds, reference, old['proposal'], c, plan, model, budget, out)
            result = export_result(out, f, c, r)
        write(out / 'selected_models.json', {'Feature': a.feature_candidate or read(PACKAGE / 'model_manifest.json')['Feature']['selection'], 'Feature_adapter': (read(PACKAGE / 'model_manifest.json')['Feature_candidates'][a.feature_candidate]['adapter'] if a.feature_candidate else read(PACKAGE / 'model_manifest.json')['Feature']['adapter']), 'candidate_explicitly_requested': bool(a.feature_candidate), 'Rule_weight_sha': read(PACKAGE / 'model_manifest.json')['Rule']['weight_sha256'], 'no_template_fallback': True})
        manifest = read(out / 'run_manifest.json')
        manifest['files'] = [{'file': str(x.relative_to(out)), 'sha256': sha(x)} for x in sorted(out.rglob('*')) if x.is_file() and x.name != 'run_manifest.json']
        write(out / 'run_manifest.json', manifest)
        print(json.dumps(result, ensure_ascii=False))
        return0 = 0 if result['Feature_status'] == 'feature_verified' and result['Rule_status'] in ['rule_verified', 'not_run'] else 2
        return return0
    except (InputFailure, ModelFailure, OSError, ValueError, KeyError, TypeError) as e:
        detail = {'status': 'explicit_failure', 'error_type': type(e).__name__, 'message': str(e), 'no_default_or_past_response_fallback': True}
        if out:
            write(out / 'failure.json', detail)
        print(json.dumps(detail, ensure_ascii=False), file=sys.stderr)
        return3 = 3
        return return3
if __name__ == '__main__':
    raise SystemExit(main())
