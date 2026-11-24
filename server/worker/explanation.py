"""Grounded explanation: only saved facts and bounded, explicitly tentative checks."""
import hashlib
import json
import logging
from generation_client import generate
from ml_attribution_text import INVALID as ATTRIBUTION_INVALID, render as render_attribution

CHECKS = {
    "review_input_completeness": "입력 결측·수집 상태를 확인하세요.",
    "review_rule_threshold": "적중한 Rule의 승인 기준과 입력값을 확인하세요.",
    "review_model_baseline": "모델 기준 기간과 현재 데이터의 분포 차이를 확인하세요.",
    "review_source_operation": "원천 시스템 운영 로그를 확인하세요. 원인은 아직 확정되지 않았습니다.",
}


def facts_for(evidence):
    return [{"id": f"fact_{index}", "name": key, "value": value}
            for index, (key, value) in enumerate(evidence.items())]


# 근거의 ML 기여도 상태(methodResults[ml].attribution.status) → 결과 contributionStatus. 키가 없으면 not_computed.
CONTRIBUTION = {"succeeded": "computed", "failed": "failed", "skipped": "skipped"}
ATTRIBUTION_FACT = "ml_attribution"


def attribution_fact(evidence, index):
    """근거에 기여도가 있는 ML 방법 결과가 있으면 (계산 성공분 사실 또는 None, contributionStatus).

    사실은 하나(name ml_attribution)이고 값은 계산 성공한 ML 방법마다 코드가 저장값으로 만든 문장과 쓴 수치다.
    상태가 여럿이면 computed > failed > skipped. 기여도 키가 하나도 없으면 (None, not_computed) — 이전 동작 그대로.
    형식이 계약과 다른 기여도는 사실에서 빼고 failed로 센다(경고 explanation_attribution_invalid). 설명 작업은 계속한다.
    """
    methods = evidence.get("methodResults") if isinstance(evidence, dict) else None
    found = [(m.get("methodId"), m["attribution"]) for m in (methods if isinstance(methods, list) else ())
             if isinstance(m, dict) and m.get("category") == "ml" and "attribution" in m]
    statuses, value = set(), []
    for method, attribution in found:
        status = attribution.get("status") if isinstance(attribution, dict) else None
        try:
            if status not in CONTRIBUTION:
                raise ValueError(ATTRIBUTION_INVALID)
            if status == "succeeded":
                value.append({"methodId": method, **render_attribution(attribution)})
        except ValueError as error:
            if str(error) != ATTRIBUTION_INVALID:
                raise
            logging.getLogger(__name__).warning("%s: %s", ATTRIBUTION_INVALID, method)
            status = "failed"
        statuses.add(status)
    status = next((CONTRIBUTION[s] for s in ("succeeded", "failed", "skipped") if s in statuses), "not_computed")
    return ({"id": f"fact_{index}", "name": ATTRIBUTION_FACT, "value": value} if value else None), status


def validate_output(output, facts, required=(), contribution_status="not_computed"):
    if not isinstance(output, dict) or set(output) != {"factIds", "nextChecks"}:
        raise ValueError("explanation_schema_rejected")
    ids, checks = output["factIds"], output["nextChecks"]
    allowed = {f["id"] for f in facts}
    if not isinstance(ids, list) or not ids or len(ids) > len(facts) or any(not isinstance(v,str) or v not in allowed for v in ids) or len(set(ids)) != len(ids):
        raise ValueError("explanation_unknown_fact")
    if not isinstance(checks, list) or len(checks) > 4 or any(not isinstance(v,str) or v not in CHECKS for v in checks) or len(set(checks)) != len(checks):
        raise ValueError("explanation_unknown_check")
    # required: 코드가 만든 사실(기여도 문장)은 모델이 고르지 않아도 결과에 넣는다(사실 순서 그대로).
    return {"facts": [f for f in facts if f["id"] in ids or f["id"] in required],
            "nextChecks": [{"code": code,"text": CHECKS[code]} for code in checks],
            "causeStatus": "unconfirmed", "contributionStatus": contribution_status}


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
    extra, contribution = attribution_fact(evidence, len(facts))
    if extra is not None:
        facts.append(extra)
    receipt = generate("explain", {"facts": facts, "allowedChecks": list(CHECKS)})
    if receipt.get("truncated") is not False or not isinstance(receipt.get("output"),dict):
        raise ValueError("explanation_generation_incomplete")
    required = (extra["id"],) if extra is not None else ()
    return {"decisionId": job["input"]["decisionId"], **validate_output(receipt["output"], facts, required, contribution)}
