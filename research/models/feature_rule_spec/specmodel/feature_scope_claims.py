import re

def claims(proposal, compilation):
    out = []
    plan = compilation['compiled_feature']['plan']
    filt = plan['population']['filter']
    facts = {'population': plan['population'], 'summary': compilation['compiled_feature']['measurement']['summary'], 'source_version': compilation['compiled_feature']['source_version']}
    expr = proposal['plan']['expression']

    def nodes(n):
        if isinstance(n, dict):
            yield n
            for v in n.values():
                yield from nodes(v)
        elif isinstance(n, list):
            for v in n:
                yield from nodes(v)
    predicates = []

    def selected_equalities(n, positive=True):
        if not isinstance(n, dict):
            return
        op = n.get('op')
        args = n.get('args', [])
        if op == 'eq' and len(args) == 2:
            (a, b) = args
            if a.get('op') == 'literal':
                (a, b) = (b, a)
            if b.get('op') == 'literal' and type(b.get('value')) is bool and (a.get('op') != 'column'):
                selected_equalities(a, positive if b['value'] else not positive)
                return
            if a.get('op') == 'column' and b.get('op') == 'literal' and positive:
                predicates.append((a, b))
            return
        if op == 'not' and len(args) == 1:
            selected_equalities(args[0], not positive)
            return
        if op == 'or':
            if not positive:
                for a in args:
                    selected_equalities(a, False)
            return
        if op == 'and':
            if positive:
                for a in args:
                    selected_equalities(a, True)
            return
        if op in ['conditional_count', 'child_sum'] and n.get('where') is not None:
            selected_equalities(n['where'], True)
        for a in args:
            selected_equalities(a, positive)
    selected_equalities(expr)
    for field in ['purpose', 'meaning']:
        text = proposal[field]
        for clause in re.split('[.;\\n]', text):
            low = clause.lower()
            excluded_cause = re.search('\\b(?:unavailable|null)\\s+(?:if|when)\\b[^;]{0,65}\\b(?:excluded|filtered out)\\b', low)
            excluded_yield = re.search('\\b(?:excluded|filtered[- ]out)\\b[^;]{0,45}\\b(?:return\\w*|yield\\w*|produce\\w*)\\s+(?:an?\\s+)?(?:unavailable|null|0|zero|false)\\b', low)
            other_scope = filt is not None and re.search('\\b(?:unavailable|null|0|zero|false)\\s+(?:for|on)\\s+(?:all\\s+)?(?:other\\s+(?:rows|interfaces|groups|transactions|processes|messages)|others)\\b', low)
            negated = bool(re.search('\\b(?:not|never|no)\\s+(?:(?:return\\w*|yield\\w*|produce\\w*)\\s+)?(?:an?\\s+)?(?:unavailable|null|0|zero|false)', low) or re.search('\\b(?:does? not|never)\\s+(?:return|yield|produce)', low))
            if (excluded_cause or excluded_yield or other_scope) and (not negated):
                out.append({'path': '/candidates/0/' + field, 'code': 'excluded_population_has_no_returned_value', 'actual': clause, 'allowed': 'Filtered/excluded rows have no feature output. State unavailable only for retained rows under actual computation. Empty child groups returning0 are a different state. Preserve the exact valid expression, selection, grain and all correct fields; repair this description only.', 'evidence': facts})
        for (a, b) in predicates:
            name = a['column']
            value = b['value']
            quoted = re.escape(str(value))

            def asserted(hit):
                if not hit:
                    return False
                prefix = re.split('[.;\\n]', text[:hit.start()])[-1][-100:].lower()
                negation = '\\b(?:(?:do(?:es)? not|not|never|cannot)\\s+(?:claim|assert|mean|imply|interpret|describe|label|call|state|interpreted|described)|no\\s+(?:claim|assertion))(?:\\W+\\w+){0,7}\\W*$'
                outcomes = list(re.finditer('\\b(?:returns?\\s+|yields?\\s+)?(true|false)\\s+(?:if|when|for)\\b', prefix, re.I))
                if outcomes and outcomes[-1].group(1).lower() == 'false':
                    return False
                return not re.search(negation, prefix)
            direct = asserted(re.search(re.escape(name) + '\\s+(?:is|equals?|==)\\s+(?:not|!=)\\s*[\'\\"]?' + quoted + '(?:[\'\\"]|\\b)', text, re.I))
            opposite_phrase = len(predicates) == 1 and asserted(re.search('\\b(?:not\\s+equal\\s+to|differs?\\s+from|different\\s+from|not\\s+matching)\\s*[\'\\"]?' + quoted + '(?:[\'\\"]|\\b)', text, re.I))
            zero_claim = type(value) in [int, float, str] and value in [0, '0'] and (len(predicates) == 1) and asserted(re.search('\\bnon[- ]zero\\b', text, re.I))
            if direct or zero_claim or opposite_phrase:
                out.append({'path': '/candidates/0/' + field, 'code': 'literal_predicate_description_reverses_AST', 'actual': text, 'allowed': 'The AST tests positive equality; the description claims its opposite. Preserve the original PUBLIC purpose, source, population and row unit. Determine which part is wrong; if the calculation changes, record a new definition and remeasure. Never silently rewrite the Model AST or make the purpose agree with a different analysis.', 'evidence': {'predicate': {'op': 'eq', 'args': [a, b]}, 'literal_python_type': type(value).__name__, 'source_version': facts['source_version']}})
    return out
