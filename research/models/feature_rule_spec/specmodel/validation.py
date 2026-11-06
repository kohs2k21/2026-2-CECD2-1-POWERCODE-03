"""Compiler facts + scoped explanation checks; never certifies all business semantics."""
import re, copy, json
import jsonschema
from .io import *
from .calculations import calc
from .contracts import feature_schema
from .conditional_expression import state_proof
from .semantic_facts import feature_claims
from .request_diagnostics import check_ratio_availability
from .result_view import effective_plan
from .official_facts import feature_description_conflicts

def fragment(text, path):
    if len(text) >= 318 and (not re.search('[.!?][\\"\\\']?$', text.rstrip())):
        return [{'path': path, 'code': 'field_boundary_or_unclosed_explanation', 'actual': text, 'allowed': 'A concise complete explanation; retain source/calculation facts and finish before the field limit. Do not truncate or append in code.'}]
    m = re.search('\\b(?:the|an?|by|because|although|and|or|but|when|where|which|whose|despite|than|from|with|as)\\s*$', text.rstrip(), re.I)
    return [{'path': path, 'code': 'unfinished_explanation', 'actual': text, 'allowed': 'A short complete factual sentence within the field limit; do not truncate in code.'}] if m else []

def semantic_errors(dataset, request, proposal, compilation):
    errors = []
    p = effective_plan(proposal['plan'])
    effective_proposal = {**proposal, 'plan': p}
    req = request.get('analysis_request', {})

    def err(path, code, actual, allowed):
        errors.append({'path': path, 'code': code, 'actual': actual, 'allowed': allowed})
    if req.get('output_table') and p['grain']['table'] != req['output_table']:
        err('/candidates/0/plan/grain', 'requested_output_rows_changed', p['grain'], req['output_table'])
    used = {(n['table'], n['column']) for tree in [p['expression'], p['population']['filter']] for n in calc.nodes(tree) if n['op'] == 'column'}
    for required in req.get('required_columns', []):
        if tuple(required) not in used:
            err('/candidates/0/plan', 'requested_source_missing', sorted(used), required)
    if req.get('selected_equals'):
        q = req['selected_equals']
        expected = [{'op': 'column', 'table': q['table'], 'column': q['column']}, {'op': 'literal', 'value': q['value']}]
        n = p['population']['filter']
        if not (n and n['op'] == 'eq' and (n['args'] in [expected, expected[::-1]])):
            err('/candidates/0/plan/population/filter', 'requested_filter_changed', n, q)
    ops = [n['op'] for n in calc.nodes(p['expression'])]
    if req.get('result_use') and proposal['plan'].get('result_use','value') != req['result_use']:
        err('/candidates/0/plan/result_use','requested_result_use_changed',proposal['plan'].get('result_use','value'),req['result_use'])
    if req.get('aggregation') and req['aggregation'] not in ops:
        err('/candidates/0/plan/expression', 'requested_aggregation_replaced', ops, req['aggregation'])
    used_relations = {n['relationship_id'] for tree in [p['expression'], p['population']['filter']] for n in calc.nodes(tree) if 'relationship_id' in n}
    for rid in req.get('required_relationship_ids', []):
        if rid not in used_relations:
            err('/candidates/0/plan/expression', 'requested_relation_dependency_missing', sorted(used_relations), rid)
    complete = req.get('complete_boolean') is True or bool(re.search('\\b(?:complete|total|always[- ]available)\\s+Boolean\\b', request['question'], re.I))
    if compilation.get('passed'):
        f = compilation['compiled_feature']
        if req.get('calculation_type'):
            from .row_scope import reset_inference
            reset_inference(dataset.engine)
            actual_type, _ = dataset.engine.infer(proposal['plan']['expression'],proposal['plan']['grain']['table'])
            wanted_type = req['calculation_type']
            if actual_type != wanted_type and not (actual_type in ['integer','number'] and wanted_type in ['integer','number']):
                err('/candidates/0/plan/expression','calculation_component_type_conflict',{'inferred_calculation_type':actual_type,'selected_result_use':proposal['plan'].get('result_use','value')},{'calculation_component_type':wanted_type,'meaning':'The expression computes the requested value/comparison; result_use separately inspects that result. Do not diagnose an already diagnosed Boolean when the requested calculation is numeric.'})
        if complete and f['dtype'] != 'boolean':
            err('/candidates/0/plan/expression', 'requested_Boolean_type', f['dtype'], 'Boolean')
        if complete and f['possible_states']['unavailable_possible']:
            examples = [{'source_row_key': k, 'actual_result': v} for (k, v) in zip(compilation['row_ids'], compilation['values']) if v is None][:2]
            err('/candidates/0/plan/expression', 'complete_Boolean_can_be_unavailable', {'operator_state_proof': f['possible_states'], 'actual_counterexamples': examples}, 'Complete true/false on retained rows. Preserve request/source/population. Strict OR remains strict; available supported operators are supplied. No answer AST.')
        claim_errors = feature_claims(effective_proposal, compilation)
        if proposal['plan'].get('result_use','value') != 'value':
            claim_errors = [e for e in claim_errors if not (e['code']=='complete_boolean_claims_unavailable_value' and re.search(r'\b(?:division|quotient|ratio|comparison|intermediate)\b',str(e.get('actual','')),re.I) and not re.search(r'\b(?:feature|flag|diagnostic|final|Boolean)\b[^.;]{0,30}\b(?:returns?|yields?|produces?)\s+(?:null|unavailable)',str(e.get('actual','')),re.I))]
        errors += claim_errors
        errors += feature_description_conflicts(effective_proposal, compilation, dataset)
        errors += check_ratio_availability(dataset, request, effective_proposal, compilation)
    actual_sources = set(used)
    for n in calc.nodes(p['expression']):
        if n.get('relationship_id') in dataset.engine.relations:
            rel = dataset.engine.relations[n['relationship_id']]
            actual_sources |= {(rel['from_table'], k) for k in rel['from_columns']} | {(rel['to_table'], k) for k in rel['to_columns']}
    if True:
        for key in proposal.get('needed_evidence', []):
            ev = dataset.evidence.get(key)
            if ev and ev.get('kind') == 'unit' and ((ev.get('table'), ev.get('column')) not in actual_sources):
                err('/candidates/0/needed_evidence', 'unrelated_unit_evidence', key, {'actual_calculation_sources': sorted(actual_sources), 'operator_units': 'Physical row counts/matching derive count units without unrelated quantity columns'})
    for field in ['purpose', 'meaning']:
        text = proposal[field]
        errors += fragment(text, '/candidates/0/' + field)
        prose_without_quotes = re.sub('([\\"\']).*?\\1', '', text)
        if re.search('[\\u3400-\\u9fff\\u3040-\\u30ff]', prose_without_quotes):
            err('/candidates/0/' + field, 'output_explanation_language', text, 'The public contract requires complete English explanatory prose; exact raw tokens may be quoted. Preserve the verified calculation and scope.')
        if re.search('\\b(?:sum|summed)\\b', text, re.I) and 'child_sum' not in ops:
            err('/candidates/0/' + field, 'sum_description_without_sum', text, 'Describe actual values/filter/output rows; count and sum are different.')
        raw_comparisons = [n for n in calc.nodes(p['expression']) if n['op'] in ['eq', 'in', 'not_in'] and any((c.get('op') == 'column' for c in n.get('args', [])))]
        authority = [x for x in dataset.evidence.values() if x.get('kind') in ['raw_code_meaning', 'column_description'] and (x.get('table'), x.get('column')) in used]
        if raw_comparisons and (not authority):
            for clause in re.split('[.;\\n]', text):
                hit = re.search('\\b(?:incoming|inbound|outgoing|outbound)\\b|empty payload|schema ambiguity|\\b(?:normal|faulty|failed|successful) (?:message|process|transaction)', clause, re.I)
                if hit and (not re.search('\\b(?:not|no|without|unconfirmed|unknown|cannot infer|does not imply)\\b', clause[:hit.start()], re.I)):
                    err('/candidates/0/' + field, 'unconfirmed_raw_code_business_meaning', clause, 'Keep raw literal calculation; exact source-scoped document/user authority is absent.')
    text = proposal['meaning']
    if any((n['op'] == 'parent_match_count' for n in calc.nodes(p['expression']))) and re.search('unavailable.{0,90}(?:due to|because|from).{0,70}empty parent', text, re.I) and (not re.search('not (?:due to|because|from)|never (?:due to|because|from)', text, re.I)):
        rels = [dataset.engine.relations[n['relationship_id']] for n in calc.nodes(p['expression']) if n['op'] == 'parent_match_count']
        if any((not dataset.rows[r['to_table']] for r in rels)):
            err('/candidates/0/meaning', 'empty_parent_is_known_zero_not_unavailable', text, {'operator_contract': 'Nonmissing child join keys and an admitted empty parent table yield known match count0. Missing child join keys yield unavailable.', 'preserve': ['actual expression', 'output grain', 'population']})
    return errors

def validate_feature(dataset, request, pred, compilation=None):
    errors = []
    for x in jsonschema.Draft202012Validator(feature_schema(dataset, request, legacy=bool(pred and pred.get('schema') == 'feature5.1'))).iter_errors(pred):
        errors.append({'path': '/' + '/'.join(map(str, x.absolute_path)), 'code': 'schema', 'actual': x.instance, 'allowed': x.message})
    if errors:
        return errors
    if not pred['candidates']:
        if not pred.get('stop_reason'):
            return [{'path': '/stop_reason', 'code': 'precise_stop_reason_required', 'actual': pred, 'allowed': 'Missing source/unit/relation or unsupported exact analysis'}]
        return fragment(pred['stop_reason'], '/stop_reason')
    proposal = pred['candidates'][0]
    return semantic_errors(dataset, request, proposal, compilation or {})
