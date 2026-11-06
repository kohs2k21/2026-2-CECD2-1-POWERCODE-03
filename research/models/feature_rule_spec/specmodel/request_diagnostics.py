"""Bounded counterfactual checks for an explicit public numeric-ratio availability request.
Not business-language proof. These values are never counted as source observations.
"""
import re, itertools, copy
from decimal import Decimal

def check_ratio_availability(dataset, request, proposal, compilation):
    if not request.get('analysis_request', {}).get('complete_boolean') or compilation['compiled_feature']['dtype'] != 'boolean':
        return []
    text = request['question']
    matches = list(re.finditer('\\b([A-Za-z_][A-Za-z_0-9]*)\\s*(?:/|divided\\s+by)\\s*(\\(\\s*[A-Za-z_][A-Za-z_0-9]*\\s*-\\s*[A-Za-z_][A-Za-z_0-9]*\\s*\\)|[A-Za-z_][A-Za-z_0-9]*)', text, re.I))
    if len(matches) != 1:
        return []
    m = matches[0]
    clause = re.split('[.;]', text[m.end():], maxsplit=1)[0]
    if re.search('\\bor\\b', clause, re.I) or re.search('false\\s+(?:if|when)', text[max(0, m.start() - 70):m.start()], re.I):
        return []
    if not re.match('\\s*(?:is\\s+unavailable|cannot\\s+be\\s+(?:evaluated|calculated|computed)|can\\s+not\\s+be\\s+(?:evaluated|calculated|computed))', text[m.end():], re.I):
        return []
    names = [m[1]] + re.findall('[A-Za-z_][A-Za-z_0-9]*', m[2])
    table = proposal['plan']['grain']['table']
    columns = {c['name']: c for c in dataset.engine.tables[table]['columns']}
    if len(names) not in [2, 3] or any((n not in columns or columns[n]['logical_type'] not in ['integer', 'number'] for n in names)):
        return []
    if not dataset.rows[table]:
        return []
    template = copy.deepcopy(dataset.rows[table][0])
    selection = request.get('analysis_request', {}).get('selected_equals')
    if selection and selection['table'] == table and (selection['column'] not in names):
        template[selection['column']] = selection['value']
    domains = [[None, 0, 1, -1] + ([Decimal('.5')] if columns[n]['logical_type'] == 'number' else []) for n in names]
    policy = compilation['compiled_feature']['plan']['missing_policy']
    tested = 0
    bad = []
    for values in itertools.product(*domains):
        row = {**template, **dict(zip(names, values))}
        filt = proposal['plan']['population']['filter']
        if filt is not None and dataset.engine.evaluate(filt, row, table, policy) is not True:
            continue
        (a, b) = values[:2]
        den = b if len(values) == 2 else None if b is None or values[2] is None else b - values[2]
        expected = a is None or den is None or den == 0
        actual = dataset.engine.evaluate(proposal['plan']['expression'], row, table, policy)
        tested += 1
        if type(actual) is not bool or actual != expected:
            bad.append({'hypothetical_inputs': dict(zip(names, values)), 'current_AST_result': actual, 'public_requested_unavailability': expected})
        if len(bad) >= 2:
            break
    if not bad:
        return []
    return [{'path': '/candidates/0/plan/expression', 'code': 'public_ratio_unavailability_counterexample', 'actual': {'public_quantity_phrase': m[0], 'counterexamples': bad, 'counterfactual_only_not_observed_source_statistics': True, 'retained_domain_examples_tested': tested}, 'allowed': 'Preserve the public quantity, source operands, row grain and population. A missing operand or zero denominator makes the numeric quantity unavailable and its complete availability diagnostic true; valid numeric0 remains calculable. Equivalent valid formulas are allowed; no expected AST is supplied.'}]
