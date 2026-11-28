"""Bind saved 1.1.0 ESB decisions to their exact source rows and definitions."""
from collections import defaultdict
from decimal import localcontext
from pathlib import Path
import importlib
import json
import sys
import numpy as np
from .artifacts import PROJECT, RELEASE, InputError, digest, jsonlines, read, require, rows, sha, verify
from .engine import combine, decimal, severity

SHORT = dict(EC_TRANSACTION_MONITORING='T', EC_PROCESS_MONITORING='P', EC_MESSAGE_MONITORING='M')
LOGICAL_TEXT = {
    'lookup_T_P': '수집된 프로세스 기록의 거래 ID와 일치하는 거래 기록이 이 캡처에 없는지 확인하는 조건',
    'lookup_P_M': '수집된 메시지의 거래 ID·프로세스 ID와 일치하는 프로세스 기록이 이 캡처에 없는지 확인하는 조건',
    'reported_process_failure_under_successful_transaction': 'S(성공) 거래와 같은 거래 ID의 F(실패) 프로세스 기록이 함께 있는지 확인하는 조건; 복구 실패나 업무 위반을 확정하는 조건은 아님',
}
CHECKS = ['review_source_operation', 'review_rule_threshold', 'review_model_baseline', 'review_input_completeness', 'review_recovery_history']


def state(value):
    value = 'unavailable' if value == 'unassessable' else value
    require(value in ('warning', 'nonwarning', 'unavailable'), 'unknown_saved_state')
    return value


def expression_label(expr):
    op = expr['op']
    if op == 'column': return SHORT.get(expr['table'], expr['table']) + '.' + expr['column']
    args = expr.get('args', [])
    symbols = dict(subtract='−', divide='÷', add='+', multiply='×')
    if op in symbols and len(args) == 2:
        return '(' + expression_label(args[0]) + ' ' + symbols[op] + ' ' + expression_label(args[1]) + ')'
    return op + '(' + ', '.join(expression_label(a) for a in args) + ')'


def unit(value):
    if not value or value in ('unknown', 'unconfirmed'):
        return dict(status='unknown', symbol=None)
    return dict(status='known', symbol={'count': '건', 'ratio': '(비율)', 'boolean': '(논리값)', 'ms': 'ms', 's': '초'}.get(value, '(' + value + ')'))


class SavedESB:
    def __init__(self, run, release=RELEASE, capture=None):
        self.run, self.release = Path(run).resolve(), Path(release).resolve()
        self.release_pins = verify(self.release, 'bundle_manifest.json')
        self.run_pins = verify(self.run)
        self.execution, self.summary = read(self.run / 'execution.json'), read(self.run / 'summary.json')
        require(self.execution['model_sha256'] == sha(self.release / 'model.json'), 'saved_model_version_mismatch')
        require(self.summary.get('profile') == 'esb_shared42_C3', 'unsupported_saved_profile')
        require(self.summary.get('HR_in_primary') is False and self.summary.get('legacy_observed_SR_in_primary') is False, 'unreviewed_primary_components')
        self.native_execution = read(self.run / 'numerical/execution.json')
        candidates = [s['command'][s['command'].index('--capture') + 1] for s in self.execution['steps'] if '--capture' in s['command']]
        require(bool(candidates) or capture is not None, 'capture_path_missing')
        self.capture_path = Path(capture or candidates[0]).resolve()
        require(self.capture_path.is_relative_to(PROJECT), 'capture_outside_project')
        require(sha(self.capture_path) == self.execution['capture_sha256'], 'saved_capture_sha_mismatch')
        self.capture_manifest = read(self.capture_path)
        # Load only the pinned metadata/AST engine; detector and trainer are never imported.
        numeric_root = self.release / 'vendor/numeric'
        sys.path.insert(0, str(numeric_root))
        artifacts = importlib.import_module('research.integration.feature_rule_ml.shared42.artifacts')
        capmodule = importlib.import_module('research.integration.feature_rule_ml.shared42.capture')
        require(Path(artifacts.__file__).resolve().is_relative_to(numeric_root), 'native_import_collision')
        _, self.profile = artifacts.profile(numeric_root / 'research/integration/feature_rule_ml/shared42/release')
        require(self.profile['definition_sha'] == self.native_execution['definition_sha'], 'saved_feature_definition_mismatch')
        self.capture = capmodule.Capture(self.capture_path, self.profile)
        self.physical = {}
        for short, spec in self.profile['definition']['schema']['tables'].items():
            self.physical[short] = {r['__SOURCE_ROW_ID']: r for r in self.capture.rows[spec['name']]}
        for p in self.native_execution['source_pins']:
            require(p['sha256'] == self.capture_manifest['tables'][p['table']]['sha256'], 'saved_source_pin_mismatch')
        self.features = {f['id']: f['feature'] for f in self.profile['definition']['features']}
        self.rules = {r['id']: r for r in self.profile['definition']['rules'] if r['role'] == 'numeric_SR'}
        self.ratio_registry = read(self.release / 'profiles/esb/ratio_fallback.json')
        self.model_meta = self.native_execution['model_set']
        shipped_models = read(numeric_root / 'research/integration/feature_rule_ml/shared42/release/models/model_set.json')
        require(self.model_meta['models'] == shipped_models['models'] and self.model_meta['definition_sha'] == self.profile['definition_sha'], 'saved_ML_model_set_mismatch')
        self.model_folder = numeric_root / 'research/integration/feature_rule_ml/shared42/release/models'
        # LOF's exact cutoff is embedded in its pinned trusted local bundle.
        # Reading metadata is the only pickle operation: no score(), predict(), fit().
        artifacts.runtime_path()
        import joblib
        self.lof_thresholds = {}
        for short in 'TPM':
            pin = self.model_meta['models'][short + '_lof']; path = self.model_folder / pin['file']
            require(sha(path) == pin['sha256'], 'LOF_weight_sha_mismatch')
            bundle = joblib.load(path)
            require(bundle['contract'] == self.profile['definition']['ml_contracts'][short], 'LOF_feature_contract_mismatch')
            self.lof_thresholds[short] = str(bundle['threshold'])
            del bundle
        self.decisions = list(rows(self.run / 'decisions.csv.gz'))
        require(len(self.decisions) == self.summary['entities'], 'saved_decision_coverage_mismatch')
        require(len({r['entity_id'] for r in self.decisions}) == len(self.decisions), 'duplicate_transaction_decision')
        self.exact_cache = {}
        self.checked_plans = set()

    def select(self, limit):
        if limit is None or limit >= len(self.decisions): return self.decisions
        require(limit > 0, 'positive_limit_required')
        groups = defaultdict(list)
        for r in self.decisions:
            groups[tuple(r[k] for k in ('logical_SR', 'numeric_SR', 'ML', 'combined'))].append(r)
        ordered = sorted(groups, key=lambda k: (k[0] != 'warning', k[0] != 'unavailable', k[3] != 'unavailable', k))
        result, offset = [], 0
        while len(result) < limit:
            added = False
            for key in ordered:
                if offset < len(groups[key]) and len(result) < limit:
                    result.append(groups[key][offset]); added = True
            if not added: break
            offset += 1
        return result

    def prepare(self, selected):
        wanted = {r['entity_id'] for r in selected}
        self.native, self.arrays, self.logical, self.logical_evidence = defaultdict(list), {}, defaultdict(list), {}
        self.alternatives = {}
        self.logical_predictions = {r['entity_id']: r for r in rows(self.run / 'logical/predictions.csv.gz') if r['entity_id'] in wanted}
        require(set(self.logical_predictions) == wanted, 'missing_logical_transaction_results')
        unlinked_t_rows = {x['source_row_id'] for x in selected if not x['transaction_id']}
        for short in 'TPM':
            for ordinal, r in enumerate(rows(self.run / 'numerical' / (short + '_decisions.csv.gz'))):
                if r['transaction_id'] in wanted or (short == 'T' and r['source_row_id'] in unlinked_t_rows):
                    require(r['source_row_id'] in self.physical[short], 'native_source_row_missing')
                    raw = self.physical[short][r['source_row_id']]
                    require(r['transaction_id'] == raw['TRANSACTION_ID'] and r['process_id'] == raw.get('PROCESS_ID', '') and r['message_id'] == raw.get('MESSAGE_ID', ''), 'native_source_identity_mismatch')
                    entity = r['transaction_id'] or next(x['entity_id'] for x in selected if x['source_row_id'] == r['source_row_id'])
                    self.native[entity].append((short, ordinal, r))
            with np.load(self.run / 'numerical' / (short + '_features.npz'), allow_pickle=False) as z:
                self.arrays[short] = {k: z[k].copy() for k in ('values', 'population', 'selection_unknown', 'feature_ids')}
        for r in rows(self.run / 'logical/predicate_values.csv.gz'):
            if r['entity_id'] in wanted and r['channel'] == 'logical_SR': self.logical[r['entity_id']].append(r)
        for r in jsonlines(self.run / 'logical/warning_and_unknown_evidence.jsonl.gz'):
            if r.get('entity_id') in wanted and r['channel'] == 'logical_SR':
                key = (r['entity_id'], r['rule_id'], r['physical_table'], r['source_row_id'])
                require(key not in self.logical_evidence, 'duplicate_logical_witness')
                self.logical_evidence[key] = r
        if (self.run / 'ratio_fallback/alternative_rule_evidence.csv.gz').is_file():
            for r in rows(self.run / 'ratio_fallback/alternative_rule_evidence.csv.gz'):
                if r['transaction_id'] in wanted:
                    key = (r['table'], r['source_row_id'], r['ratio_rule_id'])
                    require(key not in self.alternatives, 'duplicate_alternative_witness')
                    self.alternatives[key] = r
        self.logical_contracts = {}
        for p in sorted((self.run / 'logical/linked_contracts').glob('*.json')):
            pack = read(p)
            for rule in pack['rule_recommendations']:
                self.logical_contracts[p.stem + ':' + rule['model_proposal_id']] = (rule, next(f for f in pack['feature_contracts'] if f['feature_id'] == rule['condition']['feature_id']))

    def exact(self, short, ordinal, raw, feature_id):
        key = (short, ordinal, feature_id)
        if key in self.exact_cache: return self.exact_cache[key]
        f = self.features[feature_id]; e = self.capture.engine
        plan = {k: f[k] for k in ('expression', 'grain', 'population', 'group_by', 'missing_policy')}
        plan.update(id=feature_id, schema_version=e.inventory['schema_version'], source_version=e.inventory['source_version'])
        if feature_id not in self.checked_plans:
            checked = e.validate(plan)
            require(checked['passed'] and not checked['capture_missing'] and (checked['inferred_type'], checked['unit']) == (f['dtype'], f['unit']), 'feature_plan_validation_failed')
            self.checked_plans.add(feature_id)
        e.current_table, e.current_population = f['grain']['table'], f['population']['filter']
        with localcontext() as ctx:
            ctx.prec = 50
            flag = e.evaluate(f['population']['filter'], raw, e.current_table, f['missing_policy']) if f['population']['filter'] is not None else True
            value = e.evaluate(f['expression'], raw, e.current_table, f['missing_policy']) if flag is True else None
        a = self.arrays[short]; j = a['feature_ids'].tolist().index(feature_id)
        require(bool(a['population'][ordinal, j]) == (flag is True) and bool(a['selection_unknown'][ordinal, j]) == (flag is None), 'saved_population_mismatch')
        stored = a['values'][ordinal, j]
        require((value is None and np.isnan(stored)) or (value is not None and float(value) == stored), 'saved_feature_value_mismatch')
        self.exact_cache[key] = (value, flag)
        return value, flag

    def unavailable_reason(self, feature, raw):
        e = self.capture.engine
        def scan(expr):
            fields, zero = [], False
            if expr['op'] == 'column' and expr.get('table') == feature['grain']['table'] and raw.get(expr['column'], '') == '':
                fields.append(expr['column'])
            for a in expr.get('args', []):
                fs, dz = scan(a); fields += fs; zero |= dz
            if expr['op'] == 'divide':
                den = e.evaluate(expr['args'][1], raw, feature['grain']['table'], feature['missing_policy'])
                zero |= den is not None and den == 0
            return fields, zero
        fields, zero = scan(feature['expression'])
        return ('source_missing' if fields else 'undefined_denominator' if zero else 'no_reference'), sorted(set(fields))

    def packet(self, saved, policy=None):
        entity = saved['entity_id']; tid = saved['transaction_id'] or entity
        sources, facts, metadata = {}, [], {}
        def source(short, row_id):
            raw = self.physical[short].get(row_id)
            require(raw is not None and (raw['TRANSACTION_ID'] == saved['transaction_id'] or (short == 'T' and not saved['transaction_id'] and row_id == saved['source_row_id'])), 'source_binding_error')
            key = short + ':' + row_id
            sources[key] = dict(source_id=key, snapshot_sha256=self.capture_manifest['tables'][short]['sha256'], table=short, source_row_id=row_id,
                                transaction_ref=tid, process_ref=raw.get('PROCESS_ID') or None, message_ref=raw.get('MESSAGE_ID') or None)
            return key
        def add(kind, state_, refs, payload, detail=None, required=None):
            fid = 'E' + str(len(facts) + 1)
            facts.append(dict(id=fid, kind=kind, state=state_, required=state_ in ('warning', 'unavailable') if required is None else required, source_refs=sorted(set(refs)), payload=payload))
            metadata[fid] = detail or {}
        component_states = defaultdict(list)
        algorithm_states = defaultdict(list)
        for short, ordinal, native in self.native[entity]:
            raw = self.physical[short][native['source_row_id']]; sid = source(short, native['source_row_id'])
            fired = set(json.loads(native['fired_rule_ids'])); unavailable = set(json.loads(native['unavailable_rule_ids']))
            for rule_id, rule in self.rules.items():
                if SHORT[rule['table']] != short: continue
                f = self.features[rule['feature_id']]; value, selected = self.exact(short, ordinal, raw, rule['feature_id'])
                if selected is False: continue
                witness = self.alternatives.get((short, native['source_row_id'], rule_id))
                method = short + ':' + native['source_row_id'] + ':' + rule_id
                ref = dict(kind='baseline', id=rule_id, sha256=digest(rule['baseline']))
                if witness and witness['new_state'] != 'unavailable':
                    actual = witness['actual_evaluation_rule']
                    require(rule_id in unavailable and value is None and f['expression']['op'] == 'divide', 'alternative_without_original_unavailable')
                    e = self.capture.engine
                    operands = [e.evaluate(node, raw, f['grain']['table'], f['missing_policy']) for node in f['expression']['args']]
                    require(all(v is not None for v in operands) and decimal(operands[0]) == decimal(witness['numerator']) and decimal(operands[1]) == decimal(witness['denominator']) and decimal(operands[1]) == 0, 'alternative_operand_binding_mismatch')
                    s = state(witness['new_state'])
                    if actual == 'fallback:counter_partition_consistency':
                        counters = json.loads(witness['counter_values'])
                        require(set(counters) == {'TOTAL_COUNT','SUCCESS_COUNT','ERROR_COUNT'}, 'counter_partition_fields_missing')
                        for key, v in counters.items():
                            require(raw.get(key, '') != '' and decimal(raw[key]) == decimal(v) and decimal(v) >= 0, 'counter_partition_source_mismatch')
                        residual = decimal(counters['TOTAL_COUNT']) - decimal(counters['SUCCESS_COUNT']) - decimal(counters['ERROR_COUNT'])
                        payload = dict(method_id=method, original_rule_id=rule_id, alternative_rule_id=actual,
                                       alternative_rule_sha256=digest(self.ratio_registry['counter_definition_evidence']), denominator=witness['denominator'],
                                       alternative_feature_name=short + '.TOTAL_COUNT − ' + short + '.SUCCESS_COUNT − ' + short + '.ERROR_COUNT',
                                       alternative_value=str(residual), operator='ne', threshold='0', unit=unit('count'),
                                       reference=dict(kind='rule_spec', id=actual, sha256=digest(self.ratio_registry['counter_definition_evidence'])))
                        detail = dict(original_rule=rule, original_feature=f, actual_rule=actual, counters=counters, evidence=self.ratio_registry['counter_definition_evidence'])
                    else:
                        alt_id = actual.split('=>')[-1]; alt_rule = self.rules.get(alt_id)
                        require(alt_rule is not None, 'alternative_rule_missing')
                        alt_value, alt_population = self.exact(short, ordinal, raw, alt_rule['feature_id'])
                        require(alt_population is True and alt_value is not None and decimal(witness['alternative_value']) == decimal(alt_value), 'alternative_measurement_mismatch')
                        require(json.loads(witness['alternative_condition']) == alt_rule['condition'], 'alternative_threshold_mismatch')
                        payload = dict(method_id=method, original_rule_id=rule_id, alternative_rule_id=actual, alternative_rule_sha256=digest(alt_rule), denominator=witness['denominator'],
                                       alternative_feature_name=expression_label(self.features[alt_rule['feature_id']]['expression']), alternative_value=str(alt_value), operator=alt_rule['condition']['op'], threshold=str(alt_rule['condition']['value']), unit=unit(self.features[alt_rule['feature_id']]['unit']), reference=dict(kind='baseline', id=alt_id, sha256=digest(alt_rule['baseline'])))
                        detail = dict(original_rule=rule, actual_rule=alt_rule, original_feature=f, denominator=witness['denominator'])
                    add('ratio_alternative', s, [sid], payload, detail)
                elif value is None or rule_id in unavailable:
                    require(rule_id in unavailable, 'saved_numeric_availability_mismatch')
                    reason, missing = self.unavailable_reason(f, raw)
                    s = 'unavailable'
                    add('unavailable', s, [sid], dict(method_id=method, check_name=rule_id + ' / ' + expression_label(f['expression']), reason_code=reason, missing_fields=missing), dict(rule=rule, feature=f))
                else:
                    s = 'warning' if rule_id in fired else 'nonwarning'
                    add('rule_comparison', s, [sid], dict(method_id=method, role='numeric_SR', rule_id=rule_id, rule_sha256=digest(rule), feature_id=rule['feature_id'], definition_sha256=f['sha'], feature_name=expression_label(f['expression']), observed=str(value), operator=rule['condition']['op'], threshold=str(rule['condition']['value']), unit=unit(f['unit']), reference=ref), dict(rule=rule, feature=f))
                component_states['numeric_SR'].append(s)
            for alg, column, threshold in [('if', 'if_margin', '0'), ('kmeans', 'kmeans_margin', '0'), ('lof', 'lof_score', self.lof_thresholds[short])]:
                s = state(native[alg]); method = short + ':' + native['source_row_id'] + ':ml:' + alg
                if s == 'unavailable':
                    add('unavailable', s, [sid], dict(method_id=method, check_name=short + ' ' + alg + ' 검사', reason_code='source_missing' if native['ML_unavailable_reason'] == 'all_selected_features_unavailable' else 'method_not_run', missing_fields=[]), dict(saved_reason=native['ML_unavailable_reason']))
                else:
                    require(native[column] != '', 'missing_evaluated_ML_score')
                    pin = self.model_meta['models'][short + '_' + alg]
                    add('ml_score', s, [sid], dict(method_id=method, algorithm={'if':'IF','kmeans':'K-means','lof':'LOF'}[alg], model_sha256=pin['sha256'], preprocessor_sha256=pin['sha256'], feature_space_sha256=self.profile['definition']['ml_contracts'][short]['sha256'], score_name='경고 기준 대비 점수 차이' if alg != 'lof' else '저장된 정규화 이상 점수', score=native[column], threshold=threshold, operator='gt', score_direction='larger_is_more_anomalous', score_grain=short), dict(model_bundle=pin, preprocessing_scope='encoder embedded in the same pinned model bundle', actual_scoring_not_run=True))
                component_states['ML'].append(s)
                algorithm_states[alg].append(s)
        for r in self.logical[entity]:
            rid = r['rule_id']; rule, feature = self.logical_contracts[rid]
            proposal = rule['model_proposal_id']; require(proposal in LOGICAL_TEXT, 'unsupported_logical_description')
            require(r['feature_definition_sha256'] == feature['definition_sha256'] and r['feature_id'] == feature['feature_id'], 'logical_definition_mismatch')
            sid = source(r['physical_table'], r['source_row_id']); refs = [sid]
            s = 'unavailable' if r['value'] == '' else 'warning' if r['value'] == '1' else 'nonwarning' if r['value'] == '0' else None
            require(s is not None, 'invalid_logical_predicate_value')
            witness = self.logical_evidence.get((entity, rid, r['physical_table'], r['source_row_id']))
            values = [dict(field_ref='stored_logical_predicate', value='unknown' if s == 'unavailable' else r['value'])]
            if s != 'nonwarning':
                require(witness is not None and state(witness['decision']) == s, 'logical_witness_missing')
                require(witness['feature_definition_sha256'] == feature['definition_sha256'], 'logical_witness_definition_mismatch')
                raw = self.physical[r['physical_table']][r['source_row_id']]
                for key, v in witness['source_record'].items():
                    if key in raw:
                        require((None if raw[key] == '' else raw[key]) == v, 'logical_source_value_mismatch')
                        if key in ('STATUS', 'TRANSACTION_ID', 'PROCESS_ID', 'RETRY_COUNT'):
                            values.append(dict(field_ref=r['physical_table'] + '.' + key, value='(결측)' if v is None else str(v)))
                for support in witness['support_rows']:
                    refs.append(source(support['physical_table'], support['source_row_id']))
            method = r['physical_table'] + ':' + r['source_row_id'] + ':logical:' + rid
            if s == 'unavailable':
                add('unavailable', s, refs, dict(method_id=method, check_name=LOGICAL_TEXT[proposal], reason_code='no_reference', missing_fields=[]), dict(rule=rule, feature=feature, witness=witness, saved_rule_id=rid))
            else:
                add('logical_rule', s, refs, dict(method_id=method, role='logical_SR', rule_id=rid, rule_sha256=digest(rule), condition_id=feature['feature_id'], approved_statement=LOGICAL_TEXT[proposal], matched_values=values, reference=dict(kind='rule_spec', id=rid, sha256=digest(rule))), dict(rule=rule, feature=feature, witness=witness, operational_rule_approved=False))
            component_states['logical_SR'].append(s)
        # The saved logical executor explicitly reports U when a predicate has no
        # root rows. No source row exists to place in predicate_values.csv; retain
        # that U from its transaction receipt instead of inferring false.
        logical_saved = self.logical_predictions[entity]
        represented = {r['rule_id'] for r in self.logical[entity]}
        for rid in json.loads(logical_saved['unavailable_rule_ids']):
            if rid in represented: continue
            rule, feature = self.logical_contracts[rid]
            proposal = rule['model_proposal_id']; require(proposal in LOGICAL_TEXT, 'unsupported_logical_description')
            expected_short = SHORT.get(feature['calculation']['select']['table'])
            if expected_short is None:
                name = feature['calculation']['select']['table']
                expected_short = 'P' if name.startswith('Process') else 'M' if name.startswith('Message') else 'T'
            require(not any(s == expected_short for s, _, _ in self.native[entity]), 'logical_predicate_rows_missing_without_root_absence')
            sid = source('T', saved['source_row_id'])
            method = 'T:' + saved['source_row_id'] + ':logical-missing-root:' + rid
            add('unavailable', 'unavailable', [sid], dict(method_id=method, check_name=LOGICAL_TEXT[proposal] + ' (평가 대상 ' + expected_short + ' 원천 행 없음)',
                                                        reason_code='source_missing', missing_fields=[]),
                dict(rule=rule, feature=feature, reason='no_root_rows_in_saved_logical_execution', source_receipt=logical_saved, saved_rule_id=rid))
            component_states['logical_SR'].append('unavailable')
        for component in ('numeric_SR', 'logical_SR', 'ML'):
            require(bool(component_states[component]) and combine(component_states[component]) == state(saved[component]), 'saved_component_witness_mismatch:' + component)
        observed_numeric = {f['payload'].get('alternative_rule_id') or f['payload'].get('rule_id') for f in facts if f['state'] == 'warning' and f['kind'] in ('rule_comparison', 'ratio_alternative')}
        observed_logical = {f['payload']['rule_id'] for f in facts if f['state'] == 'warning' and f['kind'] == 'logical_rule'}
        require(observed_numeric == set(json.loads(saved['fired_numeric_rule_ids'])), 'saved_fired_numeric_rule_coverage_mismatch')
        require(observed_logical == set(json.loads(saved['fired_logical_rule_ids'])), 'saved_fired_logical_rule_coverage_mismatch')
        unknown_numeric = {metadata[f['id']]['rule']['id'] for f in facts if f['kind'] == 'unavailable' and metadata[f['id']].get('rule', {}).get('role') == 'numeric_SR'}
        unknown_logical = {metadata[f['id']]['saved_rule_id'] for f in facts if f['kind'] == 'unavailable' and 'saved_rule_id' in metadata[f['id']]}
        require(unknown_numeric == set(json.loads(saved['unavailable_numeric_rule_ids'])), 'saved_unknown_numeric_rule_coverage_mismatch')
        require(unknown_logical == set(json.loads(logical_saved['unavailable_rule_ids'])), 'saved_unknown_logical_rule_coverage_mismatch')
        for alg, values in algorithm_states.items():
            require(combine(values) == state(saved[alg]), 'saved_ML_algorithm_witness_mismatch:' + alg)
        require(combine(state(saved[k]) for k in ('numeric_SR','logical_SR','ML')) == state(saved['combined']), 'saved_combination_mismatch')
        top = source('T', saved['source_row_id'])
        add('observation', 'context', [top], dict(field_name='저장된 검사 결과', value='수치 SR=' + state(saved['numeric_SR']) + ', 논리 SR=' + state(saved['logical_SR']) + ', ML=' + state(saved['ML']) + '. 현재 적용된 연구용 검사 결과이며 확정 장애 판정은 아닙니다.', unit=dict(status='not_applicable', symbol=None)), required=True)
        risk, diagnostics = severity(facts, state(saved['combined']), policy)
        packet = dict(schema_version='xai-evidence/1.1.0', packet_id='XAI-' + digest(dict(run=sha(self.run/'manifest.json'), entity=entity))[:24],
                      scope=dict(kind='transaction', transaction_ref=tid, grain='T', entity_ref=entity),
                      decision=dict(id=entity, sha256=digest(saved), state=state(saved['combined']), policy='research_or', detector_version='1.1.0', basis_method_ids=sorted({f['payload']['method_id'] for f in facts if f['state']=='warning'})),
                      severity=risk, source_refs=list(sources.values()), facts=facts, allowed_checks=CHECKS,
                      provenance=dict(kind='saved_detector_run', evidence_snapshot_sha256=digest(dict(decision=saved, source_manifest=self.execution['capture_sha256'], run_manifest=sha(self.run/'manifest.json')))))
        return packet, dict(packet_id=packet['packet_id'], severity=diagnostics, fact_metadata=metadata, component_reconciliation={k:combine(v) for k,v in component_states.items()}, input_source_class=self.capture_manifest['data_class'])

    def assert_unchanged(self):
        self.capture.assert_unchanged()
        for folder, pins in [(self.release, self.release_pins), (self.run, self.run_pins)]:
            for name, expected in pins.items(): require(sha(folder/name)==expected, 'artifact_changed_during_XAI')
