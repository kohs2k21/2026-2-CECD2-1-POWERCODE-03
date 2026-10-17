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


def comparison_family(rows, provenance):
    metrics = {}
    for checker in ('hr', 'sr'):
        metrics.update({checker + key[0].upper() + key[1:]: value for key, value in compare(rows, checker).items()})
    sr_available = any(row['sr']['status'] == 'succeeded' for row in rows)
    if not sr_available:
        for key in ('srBoth', 'srRuleOnly', 'srMlOnly', 'srNeither'):
            metrics[key]['reason'] = 'sr_experiment_pending'
    return {'status': 'measured', 'reason': None, 'metricVersion': VERSION, 'metrics': metrics,
            'details': {**provenance, 'interpretation': 'checker_agreement_not_accuracy',
                        'srStatus': 'measured' if sr_available else 'unavailable',
                        'srReason': None if sr_available else 'sr_experiment_pending'}}
