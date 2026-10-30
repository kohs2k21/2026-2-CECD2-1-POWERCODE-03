"""Frozen structural contracts and compact, source-complete Model inputs."""
from pathlib import Path
import copy, json
from .io import *
from .conditional_expression import contract
PACKAGE = Path(__file__).resolve().parent.parent
FEATURE_SYSTEM = "Propose one executable offline metadata feature for the PUBLIC request. Return feature5.2 JSON only. Every plan explicitly chooses result_use: value / unavailable / unavailable_or_zero. Preserve source, requested filters, output rows and population. Do not replace an unsupported analysis with an easier calculation. You choose purpose, narrow meaning, calculation expression and result_use. For a calculation-result availability request, expression computes the requested value/comparison and result_use=unavailable checks that result. Do not enumerate missing causes with nullable OR. For unavailable-or-known-zero of a numeric result, select result_use=unavailable_or_zero. Result views are typed common compiler operations; they are not hidden fixes. Describe the selected final output, with no invented empty-case values; code infers types/units/missing policies/IDs/SHA. Never guess operational meaning from raw codes or unknown units. Physical parent existence is not a mandatory-parent business contract. A valid calculation may be constant or have no selected observations. If source, relation or an exact supported expression is unavailable, return candidates=[] with a precise complete stop_reason.\nSupported expression nodes: column(op,table,column), literal(op,value), is_missing(args1), subtract/divide/eq/lt/lte/gt/gte/and/or(args2), not(args1), in/not_in(args1,values homogeneous1..32 literals), if(args3), datetime_diff(args2,unit=ms/s/us), convert_unit(args1,from_unit,to_unit), child_count/parent_match_count(relationship_id), conditional_count(relationship_id,where), child_sum(relationship_id,value,where), group_count(keys,where). No add/abs or parent attribute lookup. Use JSON null, not string null.\nRelations are child/from -> parent/to. child_count, conditional_count, child_sum output PARENT rows; parent_match_count outputs CHILD rows. All nonliteral operands share output rows, including if's condition and branches. Grain keys identify output rows, not all source columns. group_by lists only actual group_count keys. A known empty child aggregate is0, missing relationship/key/predicate propagates unavailable. Division by0 is unavailable, never numeric0. is_missing tests any nested result and returns complete true/false on retained rows. and/or strictly propagate any unavailable operand; true OR unavailable is unavailable. if validates both branches, then lazily evaluates only the selected branch; unavailable condition stays unavailable. Filter-excluded or unknown-selection rows produce no output, not false or unavailable values. Do not hide invalid sources, types or execution errors as missingness.\nThe calculation component and final inspected output are distinct: never wrap the expression in a missingness test merely because result_use already requests unavailable. A public calculation_type constrains the inner value/comparison, not the final view. Keep one concise complete meaning sentence about the final view; detailed branch/empty-group behavior is recorded by compiler facts. Write purpose and meaning in English; quote original literal tokens when needed, but do not switch languages in explanatory prose. needed_evidence must not list unrelated supplied unit IDs. Write short COMPLETE purpose<=200, meaning<=320, stop_reason<=320. Prefer one concise meaning sentence around150–220 characters; do not repeat the whole plan or append a long branch-by-branch narrative. Explain calculation, filters, output row and result states. True0 differs from no evaluable rows. Raw IN/OUT/MAP/VOID/UNKNOWN have no business meaning beyond supplied authority. No fault/normality/SLA/online claim follows from offline metadata."

def validate_request_contract(request):
    req = request.get('analysis_request', {})
    columns = req.get('required_columns', [])
    if not isinstance(columns, list) or any(not isinstance(x, list) or len(x) != 2 or any(not isinstance(v, str) for v in x) for x in columns):
        raise InputFailure('/analysis_request/required_columns must be an array of [table, column] string pairs; malformed author input, no Model repair or semantic failure')
    relations = req.get('required_relationship_ids', [])
    if not isinstance(relations, list) or any(not isinstance(x, str) for x in relations):
        raise InputFailure('/analysis_request/required_relationship_ids must be an array of strings')

def feature_schema(dataset, request=None, legacy=False):
    validate_request_contract(request or {})
    s = read(PACKAGE / ('contracts/feature5.1.schema.json' if legacy else 'contracts/feature.schema.json'))
    rels = [r['id'] for r in dataset.inventory['relationships']]
    if not rels:
        s['$defs']['expression']['anyOf'] = [n for n in s['$defs']['expression']['anyOf'] if 'relationship_id' not in n.get('properties', {})]

    def walk(n):
        if isinstance(n, dict):
            if 'relationship_id' in n.get('properties', {}):
                n['properties']['relationship_id'] = {'enum': rels} if rels else {'type': 'string', 'not': {}}
            for x in n.values():
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    walk(s)
    from .row_schema import row_indexed
    return row_indexed(s, dataset, (request or {}).get('analysis_request', {}).get('output_table'))

def rule_schema(requests):
    s = read(PACKAGE / 'contracts/rule.schema.json')
    s['properties']['decisions']['minItems'] = s['properties']['decisions']['maxItems'] = len(requests)
    s['properties']['decisions']['items']['properties']['request'] = {'enum': requests}
    return s

def feature_record(dataset, request):
    validate_request_contract(request)
    dataset.assert_unchanged()
    inv = dataset.inventory
    profile = copy.deepcopy(dataset.profile())
    authority_scope = {tuple(x) for x in request.get('analysis_request', {}).get('required_columns', [])}
    for rel in inv['relationships']:
        authority_scope |= {(rel['from_table'], k) for k in rel['from_columns']} | {(rel['to_table'], k) for k in rel['to_columns']}
    detailed = set((tuple(x) for x in request.get('analysis_request', {}).get('required_columns', [])))
    table = request.get('analysis_request', {}).get('output_table')
    coverage = []
    for (tn, items) in profile['metadata'].items():
        for item in items:
            if item.get('unit'):
                item['unit'] = {k: v for (k, v) in item['unit'].items() if k in ['name', 'status']}
            selected = tn == table or (tn, item['column']) in detailed
            if not selected:
                for field in ['observed_raw_values', 'observed_min', 'observed_max', 'description', 'unit']:
                    item.pop(field, None)
            coverage.append({'table': tn, 'column': item['column'], 'detailed_observation': selected})
    profile['coverage'] = coverage
    profile['full_profile_sha'] = digest(dataset.profile())
    profile['selection_policy'] = 'Full schema/type/unit/relation inventory and rows/missing/distinct for every column; detailed values for public output table and required operands. Full profile is archived; no row sampling or token truncation.'
    public = {'contract': 'offline-specification-final1.0', 'task': request['question'], 'analysis_request': request.get('analysis_request', {}), 'schema_inventory': [{'table': t['name'], 'primary_key': t['primary_key'], 'columns': [{'name': c['name'], 'type': c['logical_type'], 'unit': {k: v for (k, v) in (c.get('unit') or {}).items() if k in ['name', 'status']}, 'description': c.get('description', '')} for c in t['columns']]} for t in inv['tables']], 'relationships': inv['relationships'], 'relationship_operator_domains': [{'relationship_id': r['id'], 'child_row_table': r['from_table'], 'parent_row_table': r['to_table'], 'child_count_conditional_count_child_sum_output': r['to_table'], 'parent_match_count_output': r['from_table'], 'no_implicit_row_conversion': True} for r in inv['relationships']], 'profile': profile, 'source': {'source_version': dataset.source_version, 'manifest_sha': sha(dataset.path), 'data_class': dataset.manifest['data_class'], 'period': dataset.manifest['period']}, 'confirmed_constraints': dataset.constraints, 'confirmed_authority': [{'id': x['id'], 'table': x.get('table'), 'column': x.get('column'), 'kind': x['kind'], 'confirmation': x['confirmation'], 'value': x['value']} for x in dataset.evidence.values() if (x.get('table'), x.get('column')) in authority_scope], 'unknown': dataset.manifest.get('unknown', ['No operational incident labels or online availability contract.']), 'guarded_conditional_contract': contract(), 'result_view_contract': {'version':'result-view1.0','value':'Use expression value, preserving unavailable.','unavailable':'Complete Boolean: true when expression result unavailable, false when it is available, including numeric0 and Booleanfalse.','unavailable_or_zero':'Complete Boolean for numeric expression: true if unavailable or known0, false for a known nonzero value.','scope':'All views apply only to retained output rows; invalid sources/types/software failures are errors. Explicit null branch adopts only the other concrete branch type/unit.'}, 'supported_operators_from_schema': True, 'existing_definitions_for_dedup_only': request.get('existing_definitions', [])}
    return {'input': public, 'messages': [{'role': 'system', 'content': FEATURE_SYSTEM}, {'role': 'user', 'content': json.dumps(public, ensure_ascii=False, separators=(',', ':'))}]}

def rule_system():
    return (PACKAGE / 'contracts/rule_system.txt').read_text() + ' Final delivery facts: unavailable FEATURE values are true events under is_missing, not unavailable CONDITION results. A numeric comparison evaluates only nonnull numerical values; is_missing evaluates every retained row. No selected rows differs from retained all-null rows. Baseline evidence needs both measurement_id and baseline.id. proposed has executable justified condition and needed=[]; deferred preserves a known condition but lacks applicable observations; request_evidence names absent essential evidence without inventing a condition; no_applicable has no needed items or condition. All decisions must use ONLY supplied evidence, public purposes and same immutable definition/source. A valid observed diagnostic needs no historical baseline or invented minimum sample. No rule is an operational incident label. Keep complete concise explanations.'
