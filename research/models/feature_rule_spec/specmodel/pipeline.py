"""One production-shaped offline path. No manual AST/prose or default-result substitution."""
from pathlib import Path
import copy, csv, json, time
from .io import *
from .dataset import Dataset
from .calculations import compile_feature, compact_bundle
from .contracts import feature_record, feature_schema, rule_schema
from .validation import validate_feature
from .rule_logic import make_rule_record, validate_rule, counts
from .official_facts import feature_semantics, rule_semantics

def response_origin(out, relative):
    return {'file': relative, 'raw_sha': digest(out['raw']), 'parsed_sha': digest(out['parsed']), 'messages_sha': out['messages_sha'], 'budget': out['budget']}

def prepare_feature_retry(dataset, request, public, old, errs):
    delta_fields = None
    stop_delta = False
    p = (old or {}).get('candidates', [None])
    p = p[0] if p else None
    prose = bool(p and errs and all((x.get('path', '').startswith(('/candidates/0/meaning', '/candidates/0/purpose')) for x in errs)))
    if prose:
        delta_fields = sorted({x['path'].split('/')[3] for x in errs})
        schema = {'type': 'object', 'properties': {k: {'type': 'string', 'maxLength': 200 if k == 'purpose' else 320} for k in delta_fields}, 'required': delta_fields, 'additionalProperties': False}
        messages = [{'role': 'system', 'content': 'Revise ONLY the requested explanation fields. Preserve the supplied expression, source, grain, population and all other fields. Return exactly the requested JSON fields, within their limits. Do not change a calculation in prose, add a contract or truncate a sentence.'}, {'role': 'user', 'content': json.dumps({'public_task': public['input']['task'], 'preserved_candidate': p, 'verified_computation_facts': public.get('verified_computation_facts'), 'errors': errs, 'scope': 'Only exact failed fields are regenerated; full original response is archived outside this prompt.'}, ensure_ascii=False, separators=(',', ':'), default=json_default)}]
    else:
        schema = feature_schema(dataset, request)
        schema['properties']['candidates']['items']['properties']['meaning']['maxLength'] = 320
        messages = [public['messages'][0], {'role': 'user', 'content': json.dumps({'original_public_input': public['input'], 'failed_candidates': old, 'errors': errs, 'preserve': ['PUBLIC purpose', 'requested source/filter', 'output grain', 'population'], 'repair': 'Generate the corrected complete candidate and update any explanation depending on it. Do not replace the public analysis. The compiler supplies facts, not an expected AST.'}, ensure_ascii=False, separators=(',', ':'), default=json_default)}]
    if True and isinstance(old, dict) and (old.get('candidates') == []) and errs and all((e.get('path') == '/stop_reason' for e in errs)):
        schema = {'type': 'object', 'properties': {'stop_reason': {'type': 'string', 'minLength': 1, 'maxLength': 320}}, 'required': ['stop_reason'], 'additionalProperties': False}
        messages = [{'role': 'system', 'content': 'Repair only the supplied candidate0 explanation. Preserve the same absence of a candidate and the same supported-analysis limit. Write one concise complete sentence, preferably under180 characters; do not truncate. Return stop_reason JSON.'}, {'role': 'user', 'content': json.dumps({'public_input': public['input'], 'previous_stop_reason': old['stop_reason'], 'errors': errs}, ensure_ascii=False, separators=(',', ':'), default=json_default)}]
        stop_delta = True
    expression_delta = False
    if True and (not delta_fields) and p and errs and all(('/expression' in x.get('path', '') or x.get('path', '').startswith(('/candidates/0/meaning', '/candidates/0/purpose')) for x in errs)):
        typed = feature_schema(dataset, request)
        props = typed['properties']['candidates']['items']['properties']
        schema = {'type': 'object', '$defs': typed['$defs'], 'properties': {'expression': props['plan']['properties']['expression'], 'result_use': props['plan']['properties']['result_use'], 'purpose': {**props['purpose'], 'maxLength': 180}, 'meaning': {**props['meaning'], 'maxLength': 320}, 'needed_evidence': props['needed_evidence']}, 'required': ['expression', 'result_use', 'purpose', 'meaning', 'needed_evidence'], 'additionalProperties': False}
        messages = [public['messages'][0], {'role': 'user', 'content': json.dumps({'public_input': public['input'], 'failed_expression': p['plan']['expression'], 'previous_explanations': {'purpose': p['purpose'], 'meaning': p['meaning']}, 'preserved_fields': {'grain': p['plan']['grain'], 'population': p['plan']['population'], 'group_by': p['plan']['group_by'], 'proposal_id': p['proposal_id']}, 'errors': errs, 'repair_contract': 'Return only expression,result_use,purpose,meaning,needed_evidence. The Model regenerates the calculation and complete dependent explanations. Preserve the public analysis and supplied source/grain/population/group; no expected AST is given.'}, ensure_ascii=False, separators=(',', ':'), default=json_default)}]
        expression_delta = True
    return {'messages': messages, 'schema': schema, 'delta_fields': delta_fields, 'stop_delta': stop_delta, 'expression_delta': expression_delta}

def feature_flow(dataset, request, model, budget, out, arm='Base', seed_failure=None, repair_limit=1):
    out = Path(out)
    if repair_limit not in [0, 1]:
        raise InputFailure('Feature repair limit must be0 or1')
    if seed_failure is not None:
        receipt = seed_failure.get('source_origin')
        if not receipt or sha(receipt['path']) != receipt['sha256'] or read(receipt['path'])['parsed'] != seed_failure['prediction']:
            raise InputFailure('Seeded repair requires exact pinned actual Model response')
    public = feature_record(dataset, request)
    write(out / 'feature_input.json', public)
    attempts = []
    pred = None
    last = None
    parent = None
    for attempt in range(1 if seed_failure is not None else 1 + repair_limit):
        delta_fields = None
        stop_delta = False
        if attempt == 0 and seed_failure is None:
            messages = public['messages']
            schema = feature_schema(dataset, request)
        else:
            failed = seed_failure if attempt == 0 else attempts[-1]
            old = failed['prediction']
            errs = failed['errors']
            if last and last.get('passed'):
                public['verified_computation_facts'] = feature_semantics(last, dataset)
            spec = prepare_feature_retry(dataset, request, public, old, errs)
            (messages, schema) = (spec['messages'], spec['schema'])
            (delta_fields, stop_delta) = (spec['delta_fields'], spec['stop_delta'])
        expression_delta = spec['expression_delta'] if not (attempt == 0 and seed_failure is None) else False
        label = 'feature_' + str(attempt)
        raw = model.generate('feature', messages, schema, out / 'model' / label, budget, label, arm=arm)
        value = raw['parsed']
        assembled = False
        if value is not None and stop_delta:
            pred = copy.deepcopy(old)
            pred['stop_reason'] = value['stop_reason']
            assembled = True
        elif value is not None and expression_delta:
            pred = copy.deepcopy(old)
            pred['candidates'][0]['plan']['expression'] = value['expression']
            pred['candidates'][0]['plan']['result_use'] = value['result_use']
            for k in ['purpose', 'meaning', 'needed_evidence']:
                pred['candidates'][0][k] = value[k]
            assembled = True
        elif value is not None and delta_fields:
            pred = copy.deepcopy(old)
            for k in delta_fields:
                pred['candidates'][0][k] = value[k]
            assembled = True
        else:
            pred = value
        errors = [{'path': '/', 'code': 'length_limit_hit' if raw['length_limit_hit'] else 'JSON', 'actual': raw['raw'], 'allowed': 'Complete JSON response'}] if raw['length_limit_hit'] or pred is None else validate_feature(dataset, request, pred)
        comp = None
        structural = pred is not None and (not raw['length_limit_hit']) and (not any((e['code'] == 'schema' for e in errors)))
        if structural and pred.get('candidates'):
            comp = compile_feature(dataset, pred['candidates'][0], label)
            errors = errors + comp['errors'] if not comp['passed'] else validate_feature(dataset, request, pred, comp)
        item = {'attempt': attempt, 'prediction': pred, 'passed': not errors, 'errors': errors, 'actual_model_origin': response_origin(raw, 'model/' + label + '/output.json'), 'partial_Model_prose_fields': delta_fields, 'partial_Model_expression_and_prose': expression_delta, 'partial_stop_reason': stop_delta, 'assembly_over_preserved_candidate': assembled, 'not_code_authored_answer': True}
        attempts.append(item)
        write(out / 'feature_attempts.json', attempts)
        last = comp
        if not errors:
            break
        if attempt and attempts[0]['prediction'] == pred:
            break
    if last and last.get('passed') and (not attempts[-1]['errors']):
        f = last['compiled_feature']
        f['description_revision']['Model_origin'] = attempts[-1]['actual_model_origin']
        f['description_revision']['assembled_preserved_parent'] = attempts[0]['actual_model_origin'] if attempts[-1]['assembly_over_preserved_candidate'] else None
        saved = {'status': 'feature_verified', 'preserved_seed_prediction': seed_failure['prediction'] if seed_failure else None, 'seed_source_pin': seed_failure.get('source_origin') if seed_failure else None, 'origin': 'actual_Model_partial_repair' if attempts[-1]['assembly_over_preserved_candidate'] else 'actual_Model_response', 'proposal': pred['candidates'][0], 'compilation': compact_bundle(last), 'attempts': attempts, 'source_manifest': {'sha256': sha(dataset.path), 'source_version': dataset.source_version}, 'official_semantics': feature_semantics(last, dataset), 'description_validation': {'status':'supported_claims_checked','complete_NL_proof':False,'free_prose_approval':'not_granted_by_program_supported_claims_only','actual_Model_repair':len(attempts)>1,'errors':attempts[-1]['errors']}, 'admin_semantic_review': 'Structured compiler facts define calculation behavior; AI prose has only the separately stated review scope.'}
        write(out / 'feature_bundle.json', saved)
        write(out / 'feature_values.json', {'dtype': f['dtype'], 'row_keys': f['plan']['grain']['keys'], 'source_row_keys': last['row_ids'], 'values': last['values'], 'measurement_sha': f['measurement']['measurement_sha256']})
        return (saved, last)
    if pred is not None and (not pred.get('candidates')) and (not attempts[-1]['errors']):
        saved = {'status': 'no_candidate_review', 'preserved_seed_prediction': seed_failure['prediction'] if seed_failure else None, 'seed_source_pin': seed_failure.get('source_origin') if seed_failure else None, 'not_positive_feature_generation': True, 'reason': pred['stop_reason'], 'attempts': attempts}
        write(out / 'feature_bundle.json', saved)
        return (saved, None)
    saved = {'status': 'feature_failed', 'preserved_seed_prediction': seed_failure['prediction'] if seed_failure else None, 'seed_source_pin': seed_failure.get('source_origin') if seed_failure else None, 'attempts': attempts, 'not_positive_generation': True}
    write(out / 'feature_bundle.json', saved)
    return (saved, None)

def rule_flow(dataset, reference, proposal, comp, plan, model, budget, out, seed_failure=None):
    out = Path(out)
    record = make_rule_record(dataset, reference, proposal, comp, plan)
    write(out / 'rule_input.json', {'input': record['input'], 'messages': record['messages']})
    attempts = []
    pred = seed_failure['prediction'] if seed_failure else None
    if seed_failure:
        receipt = seed_failure.get('source_origin')
        if not receipt or sha(receipt['path']) != receipt['sha256'] or read(receipt['path'])['parsed'] != seed_failure['prediction']:
            raise InputFailure('Rule seed must be an exact pinned actual Model response')
    for attempt in range(1 if seed_failure else 2):
        subset = None
        if attempt == 0 and seed_failure is None:
            r = record
            messages = r['messages']
        else:
            errors = seed_failure['errors'] if seed_failure and attempt == 0 else attempts[-1]['errors']
            indices = {int(x['path'].split('/')[2]) for x in errors if x['path'].startswith('/decisions/') and x['path'].split('/')[2].isdigit()} if isinstance(pred, dict) and isinstance(pred.get('decisions'), list) else set()
            subset = [pred['decisions'][i]['request'] for i in sorted(indices)] if indices else record['input']['requests']
            r = {**record, 'input': {**record['input'], 'requests': subset}}
            failures = [d for d in (pred or {}).get('decisions', []) if d['request'] in subset]
            messages = [record['messages'][0], {'role': 'user', 'content': json.dumps({'verified_input': r['input'], 'failed_decisions_only': failures, 'errors': errors, 'preserve': 'Unchanged feature ID, definition SHA, source, measurement and reference. Regenerate failed roles only; update dependent state/condition/evidence/needed/reasons together. Other roles are preserved outside this prompt.'}, ensure_ascii=False, separators=(',', ':'), default=json_default)}]
        prose_fields = None
        output_schema = rule_schema(r['input']['requests'])
        if (attempt or seed_failure) and errors and all((e.get('path', '').startswith('/decisions/') and e['path'].split('/')[-1] in ['reason', 'warning_meaning', 'unavailable_reason', 'business_scope'] for e in errors)):
            prose_fields = {}
            for e in errors:
                d = pred['decisions'][int(e['path'].split('/')[2])]
                prose_fields.setdefault(d['request'], set()).add(e['path'].split('/')[-1])
            prose_fields = {k: sorted(v) for (k, v) in prose_fields.items()}
            variants = []
            for (role, fields) in prose_fields.items():
                props = {'request': {'const': role}, **{k: {'type': 'string', 'minLength': 1, 'maxLength': 320} for k in fields}}
                variants.append({'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False})
            output_schema = {'type': 'object', 'properties': {'updates': {'type': 'array', 'items': {'anyOf': variants}, 'minItems': len(variants), 'maxItems': len(variants)}}, 'required': ['updates'], 'additionalProperties': False}
            packet={'feature_reference':{'id':record['input']['feature']['id'],'definition_sha':record['input']['definition_sha'],'source_version':record['input']['source_version'],'measurement_id':record['input']['measurement_id'],'dtype':record['input']['feature']['dtype'],'unit':record['input']['feature']['unit']},'scope':record['input']['scope'],'public_analysis_request':record['input']['public_analysis_request'],'confirmed_constraints':record['input']['constraints'],'unknown':record['input'].get('unknown',[]),'fields_to_revise':prose_fields,'errors':errors,'roles':[]}
            for decision in pred['decisions']:
                role=decision['request']
                if role not in prose_fields:continue
                packet['roles'].append({'request':role,'preserved_decision_sha':digest(decision),'preserved_state':decision['state'],'preserved_condition':decision['condition'],'preserved_evidence':decision['evidence'],'failed_text':{field:decision[field]for field in prose_fields[role]},'condition_statistics':counts(comp['values'],decision['condition'])if decision['condition']else None,'feature_statistics':{'retained_rows':record['input']['measurement']['row_count'],'valid_value_rows':record['input']['measurement']['valid_count'],'unavailable_value_rows':record['input']['measurement']['missing_count']},'reference_statistics':record['input']['baseline'],'operator_state_proof':record['input']['feature']['operator_state_proof'],'evaluation_contract':'is_missing evaluates every retained row, including unavailable feature values. Numeric comparisons need valid numeric values. A complete Boolean is evaluable on every retained row. No selected rows is distinct from a per-row unavailable result.'})
            messages = [{'role':'system','content':'Revise ONLY the exact failed Rule explanation fields. All role/state/condition/evidence/population fields are immutable and assembled outside your response. Use the condition_statistics for claims about condition evaluability; feature_statistics describe the value, not necessarily that condition. Numbers are optional; if quoted state their precise scope. Write concise complete explanations of verified facts without invented causes. Do not repeat the faulty text. Return updates JSON.'},{'role':'user','content':json.dumps(packet,ensure_ascii=False,separators=(',',':'),default=json_default)}]
        label = 'rule_' + str(attempt)
        raw = model.generate('rule', messages, output_schema, out / 'model' / label, budget, label, maximum=1536 if len(r['input']['requests'])==1 else 2304 if len(r['input']['requests'])==2 else 3072)
        q = raw['parsed']
        if q is not None and prose_fields and isinstance(q, dict) and isinstance(q.get('updates'), list) and ({x.get('request') for x in q['updates']} == set(prose_fields)):
            merged = copy.deepcopy(pred)
            updates = {d['request']: d for d in q['updates']}
            for d in merged['decisions']:
                if d['request'] in prose_fields:
                    for field in prose_fields[d['request']]:
                        d[field] = updates[d['request']][field]
            pred = merged
        elif q is not None and subset and isinstance(pred, dict) and ('decisions' in pred) and (not list(__import__('jsonschema').Draft202012Validator(rule_schema(subset)).iter_errors(q))):
            merged = copy.deepcopy(pred)
            replacements = {d['request']: d for d in q['decisions']}
            for (n, d) in enumerate(merged['decisions']):
                if d['request'] in subset:
                    merged['decisions'][n] = replacements[d['request']]
            if q['feature'] != pred['feature'] or q['sha'] != pred['sha']:
                merged = q
            pred = merged
        else:
            pred = q
        errors = [{'path': '/', 'code': 'JSON_or_length', 'actual': raw['raw'], 'allowed': 'Complete structural JSON'}] if pred is None or raw['length_limit_hit'] else validate_rule(record, pred)
        attempts.append({'attempt': attempt, 'prediction': pred, 'passed': not errors, 'errors': errors, 'actual_model_origin': response_origin(raw, 'model/' + label + '/output.json'), 'partial_roles': subset, 'partial_Rule_prose_fields': prose_fields, 'normal_roles_preserved': bool(subset)})
        write(out / 'rule_attempts.json', attempts)
        if not errors:
            break
    result = {'status': 'rule_verified' if not errors else 'rule_failed', 'preserved_seed_prediction': seed_failure['prediction'] if seed_failure else None, 'seed_source_pin': seed_failure.get('source_origin') if seed_failure else None, 'prediction': pred, 'attempts': attempts, 'input': record['input'], 'feature_reference': {'id': comp['compiled_feature']['id'], 'definition_sha': comp['compiled_feature']['definition_sha'], 'source_version': dataset.source_version, 'measurement_sha': comp['compiled_feature']['measurement']['measurement_sha256']}, 'official_semantics': [rule_semantics(record,d)for d in (pred or {}).get('decisions',[])], 'description_validation':{'status':'supported_claims_checked'if not errors else'not_approved','complete_NL_proof':False,'free_prose_approval':'not_granted_by_program_supported_claims_only','actual_Model_repair':len(attempts)>1,'errors':errors}, 'admin_meaning_review': 'Structured condition facts are authoritative; free AI text has separate approval status.'}
    write(out / 'rule_bundle.json', result)
    return result

def export_result(out, feature, comp, rule=None):
    out = Path(out)
    if comp and not comp.get('passed'):
        comp = None
    rules = []
    pending = []
    if rule and rule['status'] == 'rule_verified':
        for d in rule['prediction']['decisions']:
            item = {'feature': rule['feature_reference'], 'decision': d, 'condition_statistics': counts(comp['values'], d['condition']) if d['condition'] else None, 'actual_Model_attempts': rule['attempts'], 'official_semantics': rule_semantics({'input':rule['input'],'compilation':comp},d), 'AI_explanation_status':rule['description_validation'], 'operational_approval': False, 'anomaly_execution': 'not_run'}
            (rules if d['state'] == 'proposed' else pending).append(item)
    if comp:
        f = comp['compiled_feature']
        write(out/'feature_values.json',{'dtype':f['dtype'],'row_keys':f['plan']['grain']['keys'],'source_row_keys':comp['row_ids'],'values':comp['values'],'measurement_sha':f['measurement']['measurement_sha256']})
        write(out/'official_feature_semantics.json',feature.get('official_semantics') or feature_semantics(comp))
        write(out/'feature_values_by_grain'/ (comp['compiled_feature']['plan']['grain']['table']+'.json'),{'feature_id':comp['compiled_feature']['id'],'definition_sha':comp['compiled_feature']['definition_sha'],'source_version':comp['compiled_feature']['source_version'],'row_keys':comp['compiled_feature']['plan']['grain']['keys'],'source_row_keys':comp['row_ids'],'values':comp['values'],'measurement_sha':comp['compiled_feature']['measurement']['measurement_sha256']})
    write(out / 'validated_rules.json', rules)
    write(out / 'pending_decisions.json', pending)
    counts_ = {'Feature_status': feature['status'], 'Rule_status': rule['status'] if rule else 'not_run', 'definition_proposed_and_validated': feature['status'] == 'feature_verified', 'definition_kind': ('raw' if comp['compiled_feature']['plan']['expression']['op'] == 'column' else 'derived') if comp else None, 'selected_rows': comp['compiled_feature']['measurement']['summary']['row_count'] if comp else None, 'valid_observations': comp['compiled_feature']['measurement']['summary']['valid_count'] if comp else None, 'positive_feature_generated': feature['status'] == 'feature_verified' and feature.get('origin', '').startswith('actual_Model') and (not feature.get('response_reused')) and (comp is not None) and (comp['compiled_feature']['measurement']['summary']['valid_count'] > 0), 'rules': len(rules), 'by_role': {'HR': sum((x['decision']['request'] == 'hr' for x in rules)), 'observed_SR': sum((x['decision']['request'] == 'observed' for x in rules)), 'baseline_SR': sum((x['decision']['request'] == 'baseline' for x in rules))}, 'pending': len(pending), 'no_candidate_is_not_positive_generation': True}
    write(out / 'counts.json', counts_)
    files = [{'file': str(p.relative_to(out)), 'sha256': sha(p)} for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'run_manifest.json']
    write(out / 'run_manifest.json', {'contract': 'offline-specification-run-final1.0', 'files': files, 'counts': counts_, 'not_operational_detection': True})
    return counts_

def run_pipeline(dataset, reference, request, rule_plan, model, budget, out, arm='Base'):
    (feature, comp) = feature_flow(dataset, request, model, budget, out, arm)
    rule = rule_flow(dataset, reference, feature['proposal'], comp, rule_plan, model, budget, out) if comp else None
    return export_result(out, feature, comp, rule)
