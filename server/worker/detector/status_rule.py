"""Source-pinned observation status policy; a warning is not an outage claim."""
VERSION = 'process-status-hr-v1'


def default_policy(source_id):
    return {'version': VERSION, 'sourceId': str(source_id),
            'mapping': {'S': 'no_hit', 'F': 'critical', 'P': 'warning', 'N': 'warning'}}


def evaluate_status(policy, raw):
    if policy is None:
        return {'status': 'not_ready', 'anomalous': None, 'evidence': [{'reason': 'status_policy_unavailable'}]}
    if not isinstance(policy, dict) or set(policy) != {'version', 'sourceId', 'mapping'} or policy.get('version') != VERSION or \
            not isinstance(policy.get('sourceId'), str) or not policy['sourceId'] or \
            policy.get('mapping') != default_policy(policy['sourceId'])['mapping']:
        raise ValueError('unsupported_status_policy')
    value = raw.get('status')
    severity = policy['mapping'].get(value) if isinstance(value, str) else None
    known = severity is not None
    hit = known and severity != 'no_hit'
    return {'status': 'succeeded' if known else 'not_ready', 'anomalous': hit if known else None,
            'code': 'PROCESS_STATUS_' + value if hit else None,
            'severity': [severity] if hit else [],
            'evidence': [{'ruleKey': 'process-status', 'version': VERSION, 'sourceId': policy['sourceId'],
                          'observedStatus': value, 'result': 'hit' if hit else 'no_hit' if known else 'unavailable',
                          'severity': severity if hit else None,
                          'reason': None if known else 'status_unknown'}]}
