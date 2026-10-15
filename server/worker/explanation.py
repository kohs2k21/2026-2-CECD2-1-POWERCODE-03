"""Grounded explanation: only saved facts and bounded, explicitly tentative checks."""
import hashlib
import json
from generation_client import generate

CHECKS = {
    "review_input_completeness": "입력 결측·수집 상태를 확인하세요.",
    "review_rule_threshold": "적중한 Rule의 승인 기준과 입력값을 확인하세요.",
    "review_model_baseline": "모델 기준 기간과 현재 데이터의 분포 차이를 확인하세요.",
    "review_source_operation": "원천 시스템 운영 로그를 확인하세요. 원인은 아직 확정되지 않았습니다.",
}


def facts_for(evidence):
    return [{"id": f"fact_{index}", "name": key, "value": value}
            for index, (key, value) in enumerate(evidence.items())]


def validate_output(output, facts):
    if not isinstance(output, dict) or set(output) != {"factIds", "nextChecks"}:
        raise ValueError("explanation_schema_rejected")
    ids, checks = output["factIds"], output["nextChecks"]
    allowed = {f["id"] for f in facts}
    if not isinstance(ids, list) or not ids or len(ids) > len(facts) or any(not isinstance(v,str) or v not in allowed for v in ids) or len(set(ids)) != len(ids):
        raise ValueError("explanation_unknown_fact")
    if not isinstance(checks, list) or len(checks) > 4 or any(not isinstance(v,str) or v not in CHECKS for v in checks) or len(set(checks)) != len(checks):
        raise ValueError("explanation_unknown_check")
    return {"facts": [f for f in facts if f["id"] in ids],
            "nextChecks": [{"code": code,"text": CHECKS[code]} for code in checks],
            "causeStatus": "unconfirmed", "contributionStatus": "not_computed"}


def explain(job):
    # Store exact bytes as text as PostgreSQL jsonb reorders object keys.
    raw = job["input"]["savedEvidenceJson"]
    if not isinstance(raw,str) or len(raw.encode())>256000:
        raise ValueError("explanation_input_rejected")
    evidence = json.loads(raw)
    if evidence != job["input"]["savedEvidence"]:
        raise ValueError("explanation_evidence_mismatch")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if digest != job["llmSpec"]["input_evidence_hash"]:
        raise ValueError("explanation_evidence_hash_mismatch")
    facts = facts_for(evidence)
    receipt = generate("explain", {"facts": facts, "allowedChecks": list(CHECKS)})
    if receipt.get("truncated") is not False or not isinstance(receipt.get("output"),dict):
        raise ValueError("explanation_generation_incomplete")
    return {"decisionId": job["input"]["decisionId"], **validate_output(receipt["output"], facts)}
