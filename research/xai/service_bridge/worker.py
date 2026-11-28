"""Entry point for the existing explain job; code fallback needs no GPU.

Only a new saved-detector-v4-service/1.0.0 job uses this handler. Legacy jobs keep
their original handler and pinned input/output contract.
"""
from copy import deepcopy
import hashlib
import json

from xai_runtime.artifacts import digest, require
from .adapter import explain_detail

INPUT = 'saved-detector-v4-service/1.0.0'


def explain_job(job, selection=None):
    spec, data = job['llmSpec'], job['input']
    require(spec['evidence_version'] == INPUT, 'unsupported_service_job_contract')
    require(spec['output_schema_version'] == 'xai-service-report/1.0.0', 'unsupported_service_output_contract')
    require(spec['prompt_version'] == 'xai-service-code/1.0.0' and spec['model_version'] == 'code-grounded-v1',
            'unsupported_service_execution')
    raw = data['savedEvidenceJson']
    require(isinstance(raw, str) and len(raw.encode()) <= 256000, 'service_job_input_too_large')
    require(hashlib.sha256(raw.encode()).hexdigest() == spec['input_evidence_hash'], 'service_job_hash_mismatch')
    saved = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
    require(saved == data['savedEvidence'], 'service_job_saved_evidence_mismatch')
    require(data['sourceId'] == saved['sourceId'] and data['decisionId'] == saved['decisionId'], 'service_job_identity_mismatch')
    report = explain_detail(saved, selection)
    # Preserve existing frontend resultSummary keys; extend with complete report.
    return dict(decisionId=data['decisionId'], facts=[dict(id=f['id'], name=f['method']['methodId'],
        value=dict(text=f['text'], **{k:deepcopy(f[k]) for k in ('state','kind','source','method','contributes_to_final')}))
        for f in report['facts']], nextChecks=report['nextChecks'], causeStatus=report['causeStatus'],
        contributionStatus='detector_basis_only_not_causal_attribution', report=report,
        execution=dict(kind='code', model_calls=0, input_sha256=digest(saved)))
