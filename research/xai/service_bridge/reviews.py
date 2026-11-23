"""Structured records carried by the existing admin feedback API.

No second review database, invented operator labels, or automatic causal labels.
All writes use authenticated backend actors; the browser cannot supply actorId.
"""
from copy import deepcopy
from datetime import datetime
import json

from xai_runtime.artifacts import canonical, digest, require
from .adapter import SHA, uuid_text

REVIEW = 'xai-operator-review/1.0.0'
CAUSE = 'xai-cause-attestation/1.0.0'


def _text(value, limit=500):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(ord(c) < 32 and c not in '\n\t' for c in value), 'invalid_review_text')
    return value


def _ids(report, ids):
    require(isinstance(ids, list) and bool(ids) and len(ids) == len(set(ids)) and
            set(ids) <= {f['id'] for f in report['facts']}, 'unknown_or_duplicate_review_evidence')


def feedback_payload(report, record):
    """Return exact {kind,reason,outcome}, <=2000 chars for current backend.

    SHA proves only content identity, never authenticity of an incident reference.
    """
    require(isinstance(record, dict), 'invalid_review_record')
    kind = record.get('contract')
    common = {'contract', 'report_sha256', 'evidence_ids', 'notes'}
    require(record.get('report_sha256') == report['report_sha256'], 'stale_or_cross_report_review')
    _ids(report, record.get('evidence_ids'))
    _text(record.get('notes'))
    if kind == REVIEW:
        require(set(record) == common | {'variant_id','accuracy','usefulness','actionability'}, 'unknown_review_fields')
        require(record['variant_id'] in ('code', 'LLM'), 'invalid_review_variant')
        expected_variant = 'LLM' if report['versions']['execution']=='validated_LLM_selection' else 'code'
        require(record['variant_id']==expected_variant, 'review_variant_execution_mismatch')
        for key in ('accuracy','usefulness','actionability'):
            require(type(record[key]) is int and 1 <= record[key] <= 5, 'invalid_review_rating')
        feedback_kind = 'action'
    elif kind == CAUSE:
        require(set(record) == common | {'statement','claim','support'}, 'unknown_cause_fields')
        require(record['claim'] in ('hypothesis', 'operator_confirmed', 'retracted'), 'invalid_cause_claim')
        _text(record['statement'], 300)
        require(isinstance(record['support'], list) and 1 <= len(record['support']) <= 3, 'cause_requires_support')
        for support in record['support']:
            require(set(support) == {'type','reference','sha256','observed_at'}, 'invalid_cause_support')
            require(support['type'] in ('incident_report','monitoring_record','provider_confirmation'), 'invalid_cause_support_type')
            _text(support['reference'], 240)
            require(isinstance(support['sha256'], str) and SHA.fullmatch(support['sha256']), 'invalid_support_sha')
            try:
                stamp = datetime.fromisoformat(support['observed_at'].replace('Z','+00:00'))
            except (TypeError, AttributeError, ValueError) as exc:
                raise ValueError('invalid_support_time') from exc
            require(stamp.tzinfo is not None, 'support_time_requires_timezone')
        feedback_kind = 'action'
    else:
        raise ValueError('unknown_review_contract')
    reason = canonical(record)
    require(len(reason) <= 2000, 'review_exceeds_existing_backend_limit')
    # Causal confirmation is not automatically an actual-anomaly detector label.
    return dict(kind=feedback_kind, reason=reason, outcome='undetermined')


def apply_feedback(report, feedback, enterprise_actor_ids=()):
    """Read only records returned by the authenticated backend, not request bodies.

    Actor allowlisting is deployment configuration. Defaults empty: no enterprise
    reviews or confirmed causes. Current revisions replace only their own chain.
    """
    result = deepcopy(report)
    allowed = set(enterprise_actor_ids)
    parsed, ignored = [], []
    require(isinstance(feedback, list), 'invalid_feedback_response')
    # Latest API lists current feedback revisions. If revisions are supplied too,
    # suppress ancestors so a retraction cannot leave an old confirmation active.
    superseded = {r.get('supersedesId') for r in feedback if isinstance(r,dict) and r.get('supersedesId')}
    for row in feedback:
        if not isinstance(row, dict) or row.get('id') in superseded: continue
        try:
            record = json.loads(row['reason'])
            if not isinstance(record, dict) or record.get('contract') not in (REVIEW, CAUSE): continue
            feedback_payload(report, record)
            uuid_text(row['id'])
            _text(row['actorId'], 240)
            _text(row['createdAt'], 80)
        except (KeyError, TypeError, ValueError) as exc:
            ignored.append(dict(id=row.get('id'), reason=type(exc).__name__))
            continue
        parsed.append(dict(feedback_id=row['id'], actor_id=row['actorId'], created_at=row['createdAt'],
                           enterprise_review=row['actorId'] in allowed, record=record))
    reviews = [r for r in parsed if r['record']['contract']==REVIEW and r['enterprise_review']]
    causes = [r for r in parsed if r['record']['contract']==CAUSE and r['enterprise_review']]
    active = [r for r in causes if r['record']['claim']=='operator_confirmed']
    # Independent reviewers disagreeing/retracting is not silently resolved by LLM.
    conflict = any(r['record']['claim']=='retracted' for r in causes) or len({r['record']['statement'] for r in active}) > 1
    if active and not conflict:
        latest = sorted(active, key=lambda r:(r['created_at'],r['feedback_id']))[-1]
        result['causeStatus'] = 'operator_confirmed'
        result['cause'] = dict(status='operator_confirmed', statement=latest['record']['statement'],
            evidence_refs=deepcopy(latest['record']['support']), actor_id=latest['actor_id'],
            feedback_id=latest['feedback_id'],
            verification='authenticated_enterprise_attestation; supporting document authenticity not independently verified')
    elif conflict:
        result['cause'] = dict(status='disputed', statement=None, evidence_refs=[])
        result['causeStatus'] = 'disputed'
    result['enterprise_review'] = dict(status='reviewed' if reviews else 'pending',
        independent_reviews=len({r['actor_id'] for r in reviews}), review_records=len(reviews), records=parsed, ignored=ignored,
        LLM_better_than_code='not_established',
        note='담당자 판정은 별도 검토 자료이며 자동 학습·규칙·탐지 판정 변경에 사용하지 않습니다.')
    result['review_view_sha256'] = digest(result)
    return result
