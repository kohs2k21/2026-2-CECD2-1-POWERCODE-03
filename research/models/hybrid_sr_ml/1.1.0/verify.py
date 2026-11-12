"""CPU unit controls for hybrid OR and frozen ESB C3 serving packs."""
from pathlib import Path
from copy import deepcopy
import json, sys, argparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from hybrid.common import combine, read
from hybrid.runner import summarize_components

LOGICAL = ROOT / 'vendor/logical/model'
sys.path[:0] = [str(LOGICAL / 'implementation'), str(LOGICAL)]
from serve_rules import serve

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', required=True, type=Path)
TEST_ROOT = parser.parse_args().output.resolve()
OUTPUT_ROOT = TEST_ROOT / 'test_hybrid_outputs'


def test_case(name, fn):
    try:
        return {'name': name, 'status': 'passed', 'detail': fn()}
    except Exception as exc:
        return {'name': name, 'status': 'failed', 'error': type(exc).__name__ + ': ' + str(exc)}


def summarize_case(name, entities, numerical, logical):
    folder = OUTPUT_ROOT / name
    folder.mkdir(parents=True, exist_ok=False)
    summary = summarize_components(entities, numerical, logical, folder, 'test_fixture')
    return summary


def nrow(entity, numeric='nonwarning', ml='nonwarning', **extra):
    return {'numeric_SR': numeric, 'ML': ml, 'if': 'nonwarning', 'kmeans': 'nonwarning', 'lof': 'nonwarning',
            'fired_numeric_rule_ids': '[]', 'unavailable_numeric_rule_ids': '[]', **extra}


def make_case(group):
    folder = ROOT / 'profiles/esb/logical' / group
    template = read(folder / 'source_template.json')
    pack = read(folder / 'serving_pack.json')
    case = deepcopy(template)
    case['records'] = {table: [] for table in case['schema']}
    return case, pack


def serve_result(group, case):
    _, pack = make_case(group)
    return serve(pack, case, bulk=True)


def main():
    from hybrid.common import verify_bundle
    verify_bundle()
    TEST_ROOT.mkdir(parents=True, exist_ok=False)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=False)
    checks = []

    def numeric_warning_missing_logical():
        entities = [{'entity_id': 'tx-1', 'transaction_id': 'tx-1', 'source_row_id': 't1'}]
        numerical = {'tx-1': nrow('tx-1', numeric='warning', ml='unavailable')}
        summary = summarize_case('numeric_warning_missing_logical', entities, numerical, {})
        assert summary['components']['combined'] == {'warning': 1}, summary
        return {'combined': summary['components']['combined'], 'missing': summary['prediction_rows_missing']}
    checks.append(test_case('numeric warning survives missing logical result', numeric_warning_missing_logical))

    def no_warning_missing_component():
        entities = [{'entity_id': 'tx-2', 'transaction_id': 'tx-2', 'source_row_id': 't2'}]
        numerical = {'tx-2': nrow('tx-2')}
        summary = summarize_case('no_warning_missing_logical', entities, numerical, {})
        assert summary['components']['combined'] == {'unavailable': 1}, summary
        return {'combined': summary['components']['combined'], 'missing': summary['prediction_rows_missing']}
    checks.append(test_case('no warning plus missing component remains unavailable', no_warning_missing_component))

    def full_entity_denominator():
        entities = [{'entity_id': f'tx-{i}', 'transaction_id': f'tx-{i}', 'source_row_id': f't{i}'} for i in range(3)]
        numerical = {'tx-0': nrow('tx-0'), 'tx-1': nrow('tx-1', numeric='warning')}
        logical = {'tx-0': {'logical_SR': 'nonwarning', 'fired_rule_ids': '[]', 'unavailable_rule_ids': '[]'}}
        summary = summarize_case('full_entity_denominator', entities, numerical, logical)
        assert summary['entities'] == 3
        assert summary['components']['combined'] == {'nonwarning': 1, 'warning': 1, 'unavailable': 1}, summary
        assert summary['prediction_rows_missing'] == {'numerical_prediction': 1, 'logical_prediction': 2}, summary
        return {'entities_kept_in_denominator': summary['entities'], 'combined': summary['components']['combined'], 'missing': summary['prediction_rows_missing']}
    checks.append(test_case('incomplete prediction coverage preserves nonempty entity denominator', full_entity_denominator))

    def duplicate_rejected():
        entities = [{'entity_id': 'dup'}, {'entity_id': 'dup'}]
        try:
            summarize_case('duplicate_entity_rejected', entities, {}, {})
        except ValueError as exc:
            assert 'Ambiguous duplicate source identity' in str(exc), str(exc)
            return {'duplicate_entity_rejected': True, 'error': str(exc)}
        raise AssertionError('duplicate identity was accepted')
    checks.append(test_case('duplicate source entity rejects ambiguous aggregation', duplicate_rejected))

    def legacy_observed_excluded():
        entities = [{'entity_id': 'tx-legacy', 'transaction_id': 'tx-legacy', 'source_row_id': 't-legacy'}]
        numerical = {'tx-legacy': nrow('tx-legacy', observed_SR='warning', legacy_observed_SR='warning')}
        logical = {'tx-legacy': {'logical_SR': 'nonwarning', 'fired_rule_ids': '[]', 'unavailable_rule_ids': '[]'}}
        summary = summarize_case('legacy_observed_sr_excluded', entities, numerical, logical)
        assert summary['components']['combined'] == {'nonwarning': 1}, summary
        assert 'observed_SR' not in summary['components'], summary['components']
        return {'combined': summary['components']['combined'], 'primary_components': sorted(summary['components'])}
    checks.append(test_case('legacy observed SR is not part of primary combination', legacy_observed_excluded))

    def valid_relations_nonfailed():
        exp, _ = make_case('ESBExportReview')
        exp['records'] = {
            'TransactionExport': [{'TRANSACTION_ID':'tx-a','PROCESS_HUB_ID':'hub-a','RETRY_COUNT':'0'}],
            'ProcessExport': [{'TRANSACTION_ID':'tx-a','PROCESS_ID':'p-a','RETRY_COUNT':'0'}],
            'MessageExport': [{'TRANSACTION_ID':'tx-a','PROCESS_ID':'p-a','MESSAGE_ID':'m-a','RETRY_COUNT':'0'}],
        }
        out, _ = make_case('ESBReportedOutcomeReview')
        out['records'] = {
            'TransactionStatusExport': [{'TRANSACTION_ID':'tx-a','STATUS':'S','RETRY_COUNT':'0'}],
            'ProcessStatusExport': [{'TRANSACTION_ID':'tx-a','PROCESS_ID':'p-a','STATUS':'S','RETRY_COUNT':'0'}],
        }
        e = serve_result('ESBExportReview', exp)
        o = serve_result('ESBReportedOutcomeReview', out)
        assert e['decision'] == 'nonwarning' and all(r['decision'] == 'nonwarning' for r in e['rules']), e
        assert o['decision'] == 'nonwarning' and all(r['decision'] == 'nonwarning' for r in o['rules']), o
        return {'ESBExportReview': e['decision'], 'ESBReportedOutcomeReview': o['decision']}
    checks.append(test_case('valid relations and nonfailed reported statuses are nonwarning', valid_relations_nonfailed))

    def missing_process_for_message():
        case, _ = make_case('ESBExportReview')
        case['records'] = {
            'TransactionExport': [{'TRANSACTION_ID':'tx-b','PROCESS_HUB_ID':'hub-b','RETRY_COUNT':'0'}],
            'ProcessExport': [],
            'MessageExport': [{'TRANSACTION_ID':'tx-b','PROCESS_ID':'p-b','MESSAGE_ID':'m-b','RETRY_COUNT':'0'}],
        }
        result = serve_result('ESBExportReview', case)
        rule = next(r for r in result['rules'] if r['rule_id'] == 'lookup_P_M')
        assert rule['decision'] == 'warning' and any(row['value'] is True for row in rule['rows']), result
        return {'overall': result['decision'], 'missing_process_for_message': rule['decision'], 'warning_rows': sum(row['value'] is True for row in rule['rows'])}
    checks.append(test_case('missing referenced P for M warns in complete supplied export', missing_process_for_message))

    def incomplete_export_unavailable():
        case, _ = make_case('ESBExportReview')
        case['capture']['entity_family_complete'] = False
        case['records'] = {
            'TransactionExport': [{'TRANSACTION_ID':'tx-c','PROCESS_HUB_ID':'hub-c','RETRY_COUNT':'0'}],
            'ProcessExport': [],
            'MessageExport': [{'TRANSACTION_ID':'tx-c','PROCESS_ID':'p-c','MESSAGE_ID':'m-c','RETRY_COUNT':'0'}],
        }
        result = serve_result('ESBExportReview', case)
        rule = next(r for r in result['rules'] if r['rule_id'] == 'lookup_P_M')
        assert rule['decision'] == 'unavailable' and any(row['value'] is None for row in rule['rows']), result
        return {'overall': result['decision'], 'affected_rule': rule['decision'], 'unknown_rows': sum(row['value'] is None for row in rule['rows'])}
    checks.append(test_case('incomplete export preserves missing-reference unknown', incomplete_export_unavailable))

    def successful_transaction_with_failed_process():
        case, _ = make_case('ESBReportedOutcomeReview')
        case['records'] = {
            'TransactionStatusExport': [{'TRANSACTION_ID':'tx-d','STATUS':'S','RETRY_COUNT':'0'}],
            'ProcessStatusExport': [{'TRANSACTION_ID':'tx-d','PROCESS_ID':'p-d','STATUS':'F','RETRY_COUNT':'0'}],
        }
        result = serve_result('ESBReportedOutcomeReview', case)
        rule = result['rules'][0]
        assert result['decision'] == 'warning' and rule['decision'] == 'warning' and rule['rows'][0]['value'] is True, result
        return {'overall': result['decision'], 'recorded_outcome_predicate': rule['decision'], 'scope':'factual status combination, not proof of unrecovered incident'}
    checks.append(test_case('successful T with recorded P failure is a factual review warning', successful_transaction_with_failed_process))

    def empty_sources_unknown():
        outputs = {}
        for group in ('ESBExportReview','ESBReportedOutcomeReview'):
            case, _ = make_case(group)
            case['records'] = {table: [] for table in case['schema']}
            result = serve_result(group, case)
            assert result['decision'] == 'unavailable', {'group':group,'result':result}
            assert result['rules'] and all(r['decision'] == 'unavailable' and not r['rows'] for r in result['rules']), result
            outputs[group] = {'decision':result['decision'],'rules':len(result['rules']),'empty_rule_rows':True}
        assert combine([]) == 'unavailable'
        return {'packs':outputs,'empty_component_combines_to':combine([])}
    checks.append(test_case('empty source and empty component remain unknown', empty_sources_unknown))

    status = [item['status'] for item in checks]
    result = {
        'scope':'CPU-only unit controls; synthetic in-memory fixtures; no labels, model fit, LLM, GPU, install, or new model generation; bundle files are SHA-verified.',
        'checks':checks,
        'passed':status.count('passed'),
        'failed':status.count('failed'),
        'not_run':status.count('not_run'),
        'limitations':['Logical warnings reflect supplied export facts only; not confirmed incidents.','Tests do not measure detection performance. Bundle SHA verification is separate from semantic correctness.'],
    }
    output = TEST_ROOT / 'test_results.json'
    with output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
    print(json.dumps({'output':str(output),'passed':result['passed'],'failed':result['failed'],'not_run':result['not_run']},ensure_ascii=False))
    if result['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
