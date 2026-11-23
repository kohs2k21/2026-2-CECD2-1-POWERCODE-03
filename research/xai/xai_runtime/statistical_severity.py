"""Frozen relative alert grades; these are not calibrated business-impact risks.

Only existing warning evidence is ranked. Per-channel warning excesses are
collapsed to one maximum per transaction, converted to empirical midranks, then
combined by maximum. Reference transaction thirds are frozen with ties intact.
The existing detector, evidence and fixed-rule severity implementation are kept.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
from decimal import localcontext
from fractions import Fraction
from math import ceil

from .artifacts import digest, require
from .engine import LABELS, decimal, validate_packet

CONTRACT = 'xai-statistical-severity-policy/1.0.0'


def fraction_text(value):
    return str(value.numerator) + '/' + str(value.denominator)


def midrank(values, value):
    """Equal scores get equal midranks; do not rank by row ID or CSV order."""
    require(bool(values), 'severity_empty_reference_distribution')
    return Fraction(bisect_left(values, value) + bisect_right(values, value), 2 * len(values))


def quantile_thirds(values):
    """Nearest-rank upper boundaries; equality stays in the lower interval."""
    ordered = sorted(values)
    require(len(ordered) >= 3, 'severity_insufficient_reference_transactions')
    return ordered[ceil(len(ordered) / 3) - 1], ordered[ceil(2 * len(ordered) / 3) - 1]


def measure(packet, fact):
    """Return an identity-pinned numeric channel and oriented warning excess."""
    if fact['state'] != 'warning':
        return None, 'not_warning'
    kind, payload = fact['kind'], fact['payload']
    if kind not in ('ml_score', 'rule_comparison', 'ratio_alternative'):
        return None, 'no_ordered_numeric_strength'
    op = payload['operator']
    if op == 'eq':
        return None, 'equality_warning_has_no_ordered_strength'
    key = {'ml_score': 'score', 'rule_comparison': 'observed',
           'ratio_alternative': 'alternative_value'}[kind]
    value, threshold = decimal(payload[key]), decimal(payload['threshold'])
    with localcontext() as ctx:
        ctx.prec = max(100, len(value.as_tuple().digits) + len(threshold.as_tuple().digits) + 20)
        strength = value - threshold if op in ('gt', 'ge') else threshold - value if op in ('lt', 'le') else abs(value - threshold)
    require(strength >= 0, 'severity_warning_strength_direction_mismatch')
    sources = {s['source_id']: s for s in packet['source_refs']}
    grains = sorted({sources[ref]['table'] for ref in fact['source_refs']})
    descriptor = dict(kind=kind, grains=grains, operator=op, threshold=str(threshold),
                      detector_version=packet['decision']['detector_version'],
                      combination_policy=packet['decision']['policy'])
    if kind == 'ml_score':
        descriptor.update(algorithm=payload['algorithm'], model_sha256=payload['model_sha256'],
                          preprocessor_sha256=payload['preprocessor_sha256'],
                          feature_space_sha256=payload['feature_space_sha256'],
                          score_direction=payload['score_direction'], score_name=payload['score_name'])
    else:
        descriptor.update(unit=payload['unit'], reference=payload['reference'])
        if kind == 'rule_comparison':
            descriptor.update(rule_id=payload['rule_id'], rule_sha256=payload['rule_sha256'],
                              feature_id=payload['feature_id'], definition_sha256=payload['definition_sha256'])
        else:
            descriptor.update(original_rule_id=payload['original_rule_id'],
                              alternative_rule_id=payload['alternative_rule_id'],
                              alternative_rule_sha256=payload['alternative_rule_sha256'],
                              alternative_feature_name=payload['alternative_feature_name'])
    return dict(channel=digest(descriptor), descriptor=descriptor, strength=strength,
                evidence_id=fact['id'], method_id=payload['method_id']), None


def measurements(packet):
    validate_packet(packet)
    found, skipped = [], []
    for fact in packet['facts']:
        if fact['state'] != 'warning':
            continue
        item, reason = measure(packet, fact)
        if item is None:
            skipped.append(dict(evidence_id=fact['id'], method_id=fact['payload']['method_id'], reason=reason))
        else:
            found.append(item)
    return found, skipped


def ranked_items(packet, policy):
    found, skipped = measurements(packet)
    scores = []
    for item in found:
        channel = policy['channels'].get(item['channel'])
        if channel is None:
            skipped.append(dict(evidence_id=item['evidence_id'], method_id=item['method_id'],
                                reason='no_usable_reference_for_pinned_channel'))
            continue
        values = [decimal(v) for v in channel['warning_strengths']]
        rank = midrank(values, item['strength'])
        scores.append(dict(evidence_id=item['evidence_id'], method_id=item['method_id'],
                           channel=item['channel'], warning_strength=str(item['strength']),
                           reference_transactions=len(values), rank=fraction_text(rank)))
    return scores, skipped


def calibrate(packets, reference, minimum_channel_transactions=3):
    require(minimum_channel_transactions >= 3, 'severity_channel_minimum_too_small')
    require(bool(packets), 'severity_no_reference_packets')
    require(len({p['scope']['transaction_ref'] for p in packets}) == len(packets),
            'severity_duplicate_reference_transaction')
    settings = {(p['decision']['detector_version'], p['decision']['policy']) for p in packets}
    require(len(settings) == 1, 'severity_mixed_reference_detectors')
    groups, descriptors = defaultdict(dict), {}
    for packet in packets:
        require(packet['decision']['state'] == 'warning', 'severity_reference_requires_warnings')
        found, _ = measurements(packet)
        for item in found:
            channel, tid = item['channel'], packet['scope']['transaction_ref']
            descriptors[channel] = item['descriptor']
            groups[channel][tid] = max(groups[channel].get(tid, item['strength']), item['strength'])
    usable, omitted = {}, []
    for channel, per_transaction in sorted(groups.items()):
        values = sorted(per_transaction.values())
        if len(values) < minimum_channel_transactions or len(set(values)) < 2:
            omitted.append(dict(channel=channel, transactions=len(values), distinct_values=len(set(values)),
                                reason='insufficient_sample_or_no_ordered_variation', descriptor=descriptors[channel]))
        else:
            usable[channel] = dict(descriptor=descriptors[channel], warning_strengths=[str(v) for v in values])
    require(bool(usable), 'severity_no_usable_reference_channels')
    policy = dict(contract=CONTRACT, approved_for_operations=False, meaning='relative_statistical_alert_grade_not_business_impact',
                  detector_version=next(iter(settings))[0], combination_policy=next(iter(settings))[1],
                  reference=reference, minimum_channel_transactions=minimum_channel_transactions,
                  reference_transactions=len(packets), channels=usable, omitted_channels=omitted,
                  per_channel='one maximum oriented excess per transaction; empirical midrank among warning transactions',
                  transaction_aggregation='maximum available pinned-channel midrank',
                  tie_policy='equal values retain equal grades; cutoff equality uses lower interval',
                  refresh_policy='frozen until explicit creation of a new version; never refit on an incoming batch')
    transaction_scores = []
    for packet in packets:
        values, _ = ranked_items(packet, policy)
        if values:
            transaction_scores.append(max(Fraction(x['rank']) for x in values))
    require(len(set(transaction_scores)) >= 2, 'severity_no_ordered_transaction_variation')
    lo, hi = quantile_thirds(transaction_scores)
    policy.update(cutoffs=dict(p33=fraction_text(lo), p67=fraction_text(hi)),
                  scoreable_reference_transactions=len(transaction_scores),
                  reference_score_distribution=[fraction_text(v) for v in sorted(transaction_scores)])
    policy['version'] = 'statistical-thirds/1.0.0+' + digest(policy)[:16]
    return policy


def validate_policy(policy):
    require(policy.get('contract') == CONTRACT and isinstance(policy.get('channels'), dict), 'severity_invalid_statistical_policy')
    require(policy.get('meaning') == 'relative_statistical_alert_grade_not_business_impact', 'severity_meaning_mismatch')
    require(policy.get('approved_for_operations') is False, 'severity_statistical_policy_is_not_impact_approval')
    original = {k: v for k, v in policy.items() if k != 'version'}
    require(policy.get('version') == 'statistical-thirds/1.0.0+' + digest(original)[:16], 'severity_policy_content_changed')
    for key, channel in policy['channels'].items():
        values = [decimal(x) for x in channel['warning_strengths']]
        require(digest(channel['descriptor']) == key and values == sorted(values), 'severity_reference_channel_corrupted')
        require(len(values) >= policy['minimum_channel_transactions'] and len(set(values)) >= 2,
                'severity_reference_channel_insufficient')
    scores = [Fraction(x) for x in policy['reference_score_distribution']]
    lo, hi = quantile_thirds(scores)
    require(policy['cutoffs'] == dict(p33=fraction_text(lo), p67=fraction_text(hi)), 'severity_cutoff_mismatch')


def apply(packet, policy):
    """Return a copied packet plus an explicit statistical-basis receipt."""
    validate_policy(policy)
    validate_packet(packet)
    require((packet['decision']['detector_version'], packet['decision']['policy']) ==
            (policy['detector_version'], policy['combination_policy']), 'severity_detector_version_mismatch')
    updated = deepcopy(packet)
    positive = packet['decision']['basis_method_ids']
    risk = dict(status='not_applicable', level=None, label='해당 없음', label_table_version='xai-three-level-v2',
                policy_version=policy['version'], policy_sha256=digest(policy), determined_by=[], unrated_methods=[],
                known_level_is_lower_bound=False, source_risk_ref='statistical-policy:' + digest(policy))
    values, skipped = [], []
    if packet['decision']['state'] == 'warning':
        values, skipped = ranked_items(packet, policy)
        risk.update(status='unrated', label='심각도 미정', unrated_methods=sorted(positive))
        if values:
            score = max(Fraction(x['rank']) for x in values)
            lo, hi = (Fraction(policy['cutoffs'][key]) for key in ('p33', 'p67'))
            level = 'info' if score <= lo else 'warning' if score <= hi else 'critical'
            unranked = sorted({x['method_id'] for x in skipped})
            risk.update(status='rated', level=level, label=LABELS[level],
                        determined_by=sorted({x['method_id'] for x in values if Fraction(x['rank']) == score}),
                        unrated_methods=unranked, known_level_is_lower_bound=bool(unranked))
    updated['severity'] = risk
    require(updated['decision'] == packet['decision'] and updated['facts'] == packet['facts'], 'severity_changed_detector_evidence')
    validate_packet(updated)
    receipt = dict(contract='xai-statistical-severity-basis/1.0.0',
                   meaning=policy['meaning'], policy_version=policy['version'], policy_sha256=digest(policy),
                   reference=policy['reference'], cutoffs=policy['cutoffs'],
                   transaction_score=None if not values else fraction_text(max(Fraction(x['rank']) for x in values)),
                   ranked_evidence=values, unranked_warning_evidence=skipped,
                   detector_decision_unchanged=True, original_packet_sha256=digest(packet),
                   graded_packet_sha256=digest(updated), severity=risk)
    return updated, receipt
