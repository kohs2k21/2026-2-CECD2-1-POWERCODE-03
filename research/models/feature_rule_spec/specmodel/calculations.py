"""Admitted-scope compiler; Model expression, grain, selection and prose are immutable."""
from types import SimpleNamespace
from pathlib import Path
from decimal import Decimal, InvalidOperation
import copy, json, re
from .io import *
from . import engine
from .native_logic_extension import install as install_logic
from .conditional_expression import install as install_conditional, state_proof, retained_state_proof
from .row_scope import analyze
from .result_view import effective_plan, trace as result_trace
calc = SimpleNamespace(Engine=engine.Engine, FullEngine=engine.Engine, Independent=engine.Independent, engine_module=engine, nodes=engine.nodes, narrow=engine.narrow)
facade = SimpleNamespace(calc=calc, digest=digest)
install_logic(facade)
install_conditional(facade)
engine.narrow = calc.narrow
nodes = engine.nodes

def numeric_preflight(e, expression, selection):
    errors = []
    seen = set()
    for tree in [expression, selection]:
        for n in nodes(tree):
            if n.get('op') != 'column':
                continue
            key = (n['table'], n['column'])
            if key in seen or key not in e.columns:
                continue
            seen.add(key)
            info = e.columns[key]
            kind = info['logical_type']
            if kind not in ['integer', 'number', 'boolean']:
                continue
            for row in e.rows[key[0]]:
                raw = row.get(key[1])
                if raw is None or raw in info.get('missing_tokens', ['']):
                    continue
                bad = False
                if kind == 'boolean':
                    bad = type(raw) is not bool and str(raw).lower() not in ['true', 'false', '1', '0']
                else:
                    try:
                        value = Decimal(str(raw))
                        bad = type(raw) is bool or not value.is_finite() or (kind == 'integer' and value != value.to_integral_value())
                    except (InvalidOperation, ValueError, TypeError):
                        bad = True
                if bad:
                    errors.append({'path': '/plan/expression', 'code': 'source_type_failure', 'actual': {'table': key[0], 'column': key[1], 'source_row': row.get('__SOURCE_ROW_ID')}, 'allowed': 'Declared numeric/Boolean lexical value or an explicit source missing token. Not a result-unavailable event.'})
                    break
    return errors

def compile_feature(dataset, proposal, call_id):
    dataset.assert_unchanged()
    before = digest(proposal)
    model_plan = proposal['plan']
    p = effective_plan(model_plan)
    e = dataset.engine
    trace = analyze(e, p)
    errors = trace['errors']
    if model_plan.get('result_use') == 'unavailable_or_zero':
        try:
            from .row_scope import reset_inference
            reset_inference(e)
            typ, unit = e.infer(model_plan['expression'], model_plan['grain']['table'])
            if typ not in ['integer','number']:
                errors.append({'path':'/candidates/0/plan/result_use','code':'result_view_input_type','actual':typ,'allowed':'unavailable_or_zero accepts a numeric calculated value, not Boolean/category. Preserve the public analysis.'})
        except Exception:
            pass
    wanted = {(p['grain']['table'], k) for n in nodes(p['expression']) if n['op'] == 'group_count' for k in n['keys']}
    if {(x['table'], x['column']) for x in p['group_by']} != wanted:
        errors.append({'path': '/plan/group_by', 'code': 'group_metadata_conflict', 'allowed': 'Only actual group_count keys; relational aggregates already produce parent rows.'})
    errors += numeric_preflight(e, p['expression'], p['population']['filter'])
    if errors:
        return {'passed': False, 'errors': errors, 'row_grain': trace, 'proposal_sha': before}
    aggregate = any((n['op'] in ['child_count', 'conditional_count', 'child_sum', 'group_count'] for tree in [p['expression'], p['population']['filter']] for n in nodes(tree)))
    divide = any((n['op'] == 'divide' for tree in [p['expression'], p['population']['filter']] for n in nodes(tree)))
    missing = {'input': 'propagate', 'empty_group': 'zero' if aggregate else None, 'denominator_zero': 'missing' if divide else None, 'child_missing': 'propagate', 'unknown_predicate': 'defer'}
    plan = {'id': 'local_' + digest(p)[:22], 'description': calc.narrow(p['expression']), **copy.deepcopy(p), 'missing_policy': missing, 'schema_version': e.inventory['schema_version'], 'source_version': e.inventory['source_version']}
    checked = e.validate(plan)
    if not checked['passed']:
        return {'passed': False, 'errors': checked['errors'], 'proposal_sha': before, 'row_grain': trace}
    (measured, values) = e.measure_full(plan)
    if measured['measurement_status'] != 'measured':
        return {'passed': False, 'errors': [{'path': '/plan', 'code': 'measurement_deferred', 'actual': measured}], 'proposal_sha': before}
    proof = dataset.independent.verify(plan, values)
    rows = [r for r in e.rows[p['grain']['table']] if p['population']['filter'] is None or e.evaluate(p['population']['filter'], r, p['grain']['table'], missing) is True]
    identifier = 'fd_' + digest({'definition': checked['definition_sha256'], 'source': dataset.source_version})[:24]
    desc = {'purpose': proposal['purpose'], 'meaning': proposal['meaning']}
    f = {'Model_plan': copy.deepcopy(model_plan), 'result_view_compilation': result_trace(model_plan), 'id': identifier, 'definition_sha': checked['definition_sha256'], 'semantic_definition_sha': digest({k: plan[k] for k in ['expression', 'grain', 'population', 'group_by', 'missing_policy']} | {'dtype': checked['inferred_type'], 'unit': checked['unit']}), 'source_version': dataset.source_version, 'schema_version': e.inventory['schema_version'], 'plan': plan, 'dtype': checked['inferred_type'], 'unit': checked['unit'], 'meaning': proposal['meaning'], 'purpose': proposal['purpose'], 'source_columns': checked['identity']['source_columns'], 'relationships': checked['identity']['relationships'], 'measurement': measured, 'measurement_id': 'm_' + measured['measurement_sha256'][:24], 'independent_calculation': proof, 'description_revision': {'sha': digest(desc), 'calculation_definition_sha': checked['definition_sha256'], 'measurement_sha': measured['measurement_sha256'], 'measurement_reuse_requires': 'Same expression/grain/population/group/missing/type/unit/schema/source. This compilation always verifies its own source.'}, 'registration': {'call_id': call_id, 'Model_alias': proposal['proposal_id'], 'Model_proposal_sha': before}, 'possible_states': retained_state_proof(p['expression'], p['population']['filter']), 'availability': 'offline complete capture; prospective availability and IF not approved'}
    assert len(rows) == len(values) and digest(proposal) == before
    return {'passed': True, 'errors': [], 'compiled_feature': f, 'row_grain': trace, 'proposal_sha': before, 'values': values, 'row_ids': [[r[k] for k in p['grain']['keys']] for r in rows], 'selected_rows': rows}

def compact_bundle(c):
    q = copy.deepcopy(c)
    q.pop('selected_rows', None)
    q.pop('values', None)
    return q
