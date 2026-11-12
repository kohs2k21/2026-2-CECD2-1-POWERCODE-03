"""Source-bound computational claims, not a proof of arbitrary natural language."""
from .io import *
from . import calculations as calc
from types import SimpleNamespace
fc = SimpleNamespace(calc=calc)
import re
VERSION = 'calculation-facts2.0-guarded-availability'

def ast_states(node):
    from .conditional_expression import state_proof
    return state_proof(node)

def feature_facts(compilation):
    f = compilation['compiled_feature']
    m = f['measurement']['summary']
    p = f['plan']
    abstract = f.get('possible_states') or ast_states(p['expression'])
    states = ['true', 'false'] if f['dtype'] == 'boolean' else ['value']
    if abstract['unavailable_possible']:
        states += ['unavailable']
    out = {'version': VERSION, 'definition_sha': f['definition_sha'], 'source_version': f['source_version'], 'measurement_id': f['measurement_id'], 'measurement_sha': f['measurement']['measurement_sha256'], 'output_grain': p['grain'], 'population': p['population'], 'dtype': f['dtype'], 'unit': f['unit'], 'operator_contract_possible_states': states, 'operator_state_proof': abstract, 'missing_policy': p['missing_policy'], 'current': {'selected_rows': m['row_count'], 'population_rows_before_selection': m.get('population_rows_before_selection'), 'selection_unavailable': m.get('selection_unavailable_count'), 'numeric_or_boolean_nonnull': m['valid_count'], 'result_unavailable': m['missing_count'], 'true': m.get('true_count'), 'false': m.get('false_count')}, 'interpretation': {'null_result': 'Does not alone identify source null, parsing, join, denominator or fault cause.', 'empty_child_group': 'Zero only under the aggregate empty-group contract; unknown relationship/filter is different.', 'sample_extrema': 'Observed min/max are not proven bounds.', 'total_missing_test': 'is_missing evaluates retained null results to true; it is not an unavailable numerical comparison.'}, 'row_nodes': compilation.get('row_grain', {}).get('nodes', [])}
    out['sha256'] = digest(out)
    return out

def nested_unavailable_subject(clause, expression):
    low = clause.lower()
    prefix = re.search('\\b(?:(?:returns?|yields?|produces?)\\s+)?(?:true|false)\\s+(?:if|when)\\s+(.+)', low)
    introspection = re.search('\\b(?:tests?|checks?|diagnoses?)\\s+(?:if|whether)\\s+(.+)', low)
    if introspection and (not re.search('\\b(?:but|otherwise|however)\\b|\\band\\s+(?:then\\s+)?(?:returns?|yields?|produces?)', introspection.group(1))):
        return True
    formula = re.match('\\s*(?:the\\s+)?([A-Za-z0-9_()./+*\\- ]+)\\s+result\\s+(?:is|becomes)\\s+(?:unavailable|null)\\s+(?:if|when)\\b', low)
    if formula and expression.get('op') == 'is_missing':

        def render(n, qualified=False):
            if n['op'] == 'column':
                return (n['table'] + '.' if qualified else '') + n['column']
            if n['op'] == 'literal':
                return str(n['value'])
            if n['op'] in ['subtract', 'divide']:
                return '(' + render(n['args'][0], qualified) + (' - ' if n['op'] == 'subtract' else ' / ') + render(n['args'][1], qualified) + ')'
            return '__not_an_arithmetic_subject__'
        norm = lambda x: re.sub('[()\\s]', '', x).lower()
        if norm(formula.group(1)) in {norm(render(expression['args'][0], q)) for q in [False, True]}:
            return True
    if not prefix:
        return False
    subordinate = prefix.group(1)
    if re.search('\\b(?:but|otherwise|however)\\b|\\band\\s+(?:then\\s+)?(?:returns?|yields?|produces?)', subordinate):
        return False
    nodes = list(fc.calc.nodes(expression))
    words = []
    for n in nodes:
        words += {'divide': ['division', 'ratio', 'quotient'], 'subtract': ['difference', 'subtraction'], 'child_sum': ['sum'], 'child_count': ['count'], 'conditional_count': ['count'], 'parent_match_count': ['match count'], 'eq': ['comparison'], 'lt': ['comparison'], 'gt': ['comparison'], 'lte': ['comparison'], 'gte': ['comparison']}.get(n['op'], [])
    return any((re.search('\\b' + re.escape(w) + '\\b[^.;]{0,100}\\b(?:is|becomes|yields?|returns?)\\s+(?:an?\\s+)?(?:unavailable|null)\\b', subordinate) for w in words))

def feature_claims(proposal, compilation):
    facts = feature_facts(compilation)
    from .feature_scope_claims import claims
    out = claims(proposal, compilation)
    text_all = proposal['meaning'].lower()
    exception = bool(re.search('(?:propagat\\w*\\s+(?:null|unavailable)|(?:return\\w*|result\\w*)[^.;]{0,25}(?:null|unavailable))[^.;]{0,70}(?:if|when)[^.;]{0,60}(?:null|missing|unavailable)', text_all) or re.search('(?:null|unavailable)[^.;]{0,35}(?:if|when)[^.;]{0,60}(?:null|missing)', text_all) or re.search('(?:if|when)[^.;]{0,50}(?:input|operand|column|value)[^.;]{0,25}(?:null|missing)[^.;]{0,40}(?:return\\w*|propagat\\w*|result)[^.;]{0,20}(?:null|unavailable)', text_all))
    for field in ['purpose', 'meaning']:
        text = proposal[field]
        for clause in re.split('[.;\\n]', text):
            low = clause.lower()
            conflict = re.search('(?:unavailable|null)\\s+(?:if|when)\\s+(?:(?:the|a|an)\\s+)?(?:output\\s+)?rows?\\s+(?:(?:is|are|was|were)\\s+)?(?:excluded|filtered out)', low)
            conflict = conflict or re.search('(?:excluded|filtered[- ]out)\\s+rows?\\s+(?:return\\w*|yield\\w*|produce\\w*)\\s+(?:an?\\s+)?(?:unavailable|null)', low)
            if conflict and (not re.search('(?:does? not|never|not|without)\\s+(?:return\\w*|yield\\w*|produce\\w*)\\s+(?:an?\\s+)?(?:unavailable|null)', low)):
                out.append({'path': '/candidates/0/' + field, 'code': 'excluded_rows_are_not_unavailable_output_rows', 'actual': clause, 'allowed': 'Excluded rows have no output feature value. Retained rows can return unavailable only under the expression and missing policy. Preserve the valid expression, population, row keys and all correct fields; repair only this explanation.', 'evidence': {'population': compilation['compiled_feature']['plan']['population'], 'current': facts['current'], 'operator_possible_states': facts['operator_contract_possible_states'], 'definition_sha': facts['definition_sha'], 'source_version': facts['source_version']}})
    filt = compilation['compiled_feature']['plan']['population']['filter']
    if filt is not None:
        filter_columns = [n['column'].lower() for n in fc.calc.nodes(filt) if n['op'] == 'column']
        for field in ['purpose', 'meaning']:
            text = proposal[field]
            low = text.lower()
            names = [name for col in filter_columns for name in [col, col.replace('_', ' ')]]
            if any((name in low for name in names)) and re.search('(?:returns?|yields?)[^.;]{0,90}(?:unavailable|null)[^.;]{0,45}(?:if|when)[^.;]{0,70}(?:does not|not|fails to) match', low):
                out.append({'path': '/candidates/0/' + field, 'code': 'population_exclusion_is_not_returned_unavailable', 'actual': text, 'allowed': 'The population filter excludes nonmatching rows. Only selected rows produce this feature; unavailable calculated values are a separate state. Preserve the valid expression and exact filter.', 'evidence': {'population': compilation['compiled_feature']['plan']['population'], 'current': facts['current'], 'definition_sha': facts['definition_sha'], 'source_version': facts['source_version']}})
    aggregate_nodes = [n for n in fc.calc.nodes(proposal['plan']['expression']) if n['op'] in ['child_count', 'conditional_count', 'child_sum']]
    if aggregate_nodes and facts['missing_policy'].get('empty_group') == 'zero':
        for field in ['purpose', 'meaning']:
            for clause in re.split('[.;\\n]', proposal[field]):
                bad = re.search('\\b(?:unavailable|null)\\s+(?:if|when)\\b[^.;]{0,70}\\bno\\s+(?:linked|matching|related)\\s+(?:child\\s+)?[A-Za-z_]+\\s*$', clause, re.I)
                bad = bad or re.search('\\b(?:unavailable|null)\\s+(?:if|when)\\b[^.;]{0,60}\\b(?:child|children)\\s+(?:group|set|collection)\\s+(?:is\\s+)?empty\\s*$', clause, re.I)
                if bad:
                    out.append({'path': '/candidates/0/' + field, 'code': 'known_empty_child_group_not_unavailable', 'actual': clause, 'allowed': 'Preserve the valid aggregate, child predicate, parent output rows and population. Under its compiled contract a known empty child group returns numeric0. Missing parent keys, unknown relation or unknown child input/predicate are separate unavailable causes. Repair only the conflicting explanation, using a shorter complete sentence.', 'evidence': {'aggregate_operations': [n['op'] for n in aggregate_nodes], 'empty_group_policy': 'zero', 'counterexample': {'known_parent_key': True, 'known_child_rows': 0, 'returned_value': 0, 'returned_unavailable': False}, 'definition_sha': facts['definition_sha'], 'source_version': facts['source_version']}})
    if facts['dtype'] != 'boolean':
        return out
    if 'unavailable' not in facts['operator_contract_possible_states']:
        for field in ['purpose', 'meaning']:
            for clause in re.split('[.;\\n]', proposal[field]):
                low = clause.lower()
                claims = re.search('\\b(?:returns?|yields?|produces?)\\s+(?:an?\\s+)?(?:unavailable|null)\\b', low) or re.search('\\b(?:unavailable|null)\\s+(?:if|when)\\s+(?:(?:the|an?|either|any)\\s+)?(?:logic|input|operand|column|value)', low)
                if claims and nested_unavailable_subject(clause, proposal['plan']['expression']):
                    claims = None
                claims = claims or re.search('^\\s*(?:(?:the )?(?:feature|flag|Boolean)\\s+)?(?:preserves?|propagates?)\\s+(?:an?\\s+)?(?:unavailable|null)\\b', clause, re.I)
                claims = claims or re.search('\\b(?:unavailable|null)\\s+only\\s+(?:if|when)\\s+(?:the\\s+)?(?:diagnostic|flag|feature|Boolean(?: test)?)\\s+(?:itself\\s+)?(?:cannot|can not|fails to)\\s+(?:be\\s+)?evaluat', low)
                negated = re.search('\\b(?:not|never|no)\\s+(?:(?:returns?|yields?|produces?)\\s+)?(?:an?\\s+)?(?:unavailable|null)', low) or re.search('\\b(?:does? not|never)\\s+(?:return|yield|produce)', low)
                if claims and (not negated):
                    out.append({'path': '/candidates/0/' + field, 'code': 'complete_boolean_claims_unavailable_value', 'actual': clause, 'allowed': {'possible_states': ['true', 'false'], 'preserve': 'The valid expression, source, population and row keys. Retained rows yield complete Boolean values; a whole-program/software failure is a separate status, not an invented per-row unavailable output.'}, 'evidence': facts})
    for k in ['purpose', 'meaning']:
        text = proposal[k]
        for clause in re.split('[.;\\n]', text):
            low = clause.lower()
            total = re.search('false\\s+(?:in all other cases|otherwise)|otherwise\\s+(?:returns?\\s+)?false', low)
            limited = bool(re.search('(?:known|present|non[- ]?null|evaluable|available) (?:inputs|operands|values|comparisons)|(?:otherwise.*(?:null|unavailable)|(?:null|unavailable).*propagat)', low))
            if total and 'unavailable' in facts['operator_contract_possible_states'] and (not (limited or exception)):
                out.append({'path': '/candidates/0/' + k, 'code': 'total_boolean_claim_conflicts_with_operator_states', 'actual': clause, 'allowed': {'possible_states': facts['operator_contract_possible_states'], 'preserve': 'Exact valid AST, population and row keys. Qualify false for evaluable inputs; describe propagated unavailable separately.'}, 'evidence': facts})
    return out
