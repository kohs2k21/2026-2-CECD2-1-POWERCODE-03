"""Unlabeled cross-checker comparisons with explicit comparable denominators."""
VERSION = 'rule-ml-comparison-v1'


def measurement(value, unit, *, numerator=None, denominator=None, coverage=None, reason=None):
    return {'value': value, 'unit': unit, 'numerator': numerator, 'denominator': denominator,
            'coverage': coverage, 'reason': reason}


def compare(rows, checker):
    counts = dict.fromkeys(('both', 'ruleOnly', 'mlOnly', 'neither'), 0)
    total, comparable = len(rows), 0
    for row in rows:
        rule, ml = row[checker], row['ml']
        if rule['status'] != 'succeeded' or ml['status'] != 'succeeded' or \
                type(rule.get('anomalous')) is not bool or type(ml.get('anomalous')) is not bool:
            continue
        comparable += 1
        key = 'both' if rule['anomalous'] and ml['anomalous'] else 'ruleOnly' if rule['anomalous'] else \
              'mlOnly' if ml['anomalous'] else 'neither'
        counts[key] += 1
    coverage = comparable / total if total else None
    metrics = {key: measurement(count / comparable if comparable else None, 'ratio', numerator=count,
                               denominator=comparable, coverage=coverage,
                               reason=None if comparable else 'no_comparable_rows') for key, count in counts.items()}
    for key, value in [('total', total), ('comparable', comparable), ('missing', total - comparable)]:
        metrics[key] = measurement(value, 'count', coverage=coverage)
    return metrics


SR_STATUS = 'not_applicable'
SR_REASON = 'sr_removed'


def sr_cells(total):
    """통계 SR 폐기(#94): `sr*` 셀은 영구 not_applicable/sr_removed. 키 집합은 기존 그대로 유지한다."""
    cells = {key: measurement(None, 'ratio', reason=SR_REASON) for key in ('srBoth', 'srRuleOnly', 'srMlOnly', 'srNeither')}
    cells.update(srTotal=measurement(total, 'count'), srComparable=measurement(0, 'count'),
                 srMissing=measurement(total, 'count'))
    return cells


def comparison_family(rows, provenance):
    metrics = {'hr' + key[0].upper() + key[1:]: value for key, value in compare(rows, 'hr').items()}
    # SR 경로의 행별 상태(`not_ready`·fixture의 `succeeded`)는 더 이상 집계하지 않는다 — 방법 자체가 카탈로그에서 제거됨.
    metrics.update(sr_cells(len(rows)))
    return {'status': 'measured', 'reason': None, 'metricVersion': VERSION, 'metrics': metrics,
            'details': {**provenance, 'interpretation': 'checker_agreement_not_accuracy',
                        'srStatus': SR_STATUS, 'srReason': SR_REASON}}
