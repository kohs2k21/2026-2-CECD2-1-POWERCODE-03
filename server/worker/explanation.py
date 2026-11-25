"""설명 작업 `explain`. `llm_spec.evidence_version`으로 두 형식을 나눈다.

- `summary-sentences-v1`(AI 근거 요약 다듬기): 백엔드가 만든 코드 문장을 경량 모델이 한 문단으로 다듬고, 코드가 사실 보존을 확인한다
  (evidence_polish.py). 확인 실패·출력 잘림은 코드 문장으로 완료, 생성 실패는 재시도 없는 실패
- `saved-decision-v1`(이전 형식, 접수가 바뀌기 전에 쌓인 작업): 저장 사실 ID와 정해진 점검 항목만 고르는 기존 동작 그대로
"""
import hashlib
import json

import evidence_polish as ep
from generation_client import GenerationRejected, GenerationUnavailable, generate

CHECKS = {
    "review_input_completeness": "입력 결측·수집 상태를 확인하세요.",
    "review_rule_threshold": "적중한 Rule의 승인 기준과 입력값을 확인하세요.",
    "review_model_baseline": "모델 기준 기간과 현재 데이터의 분포 차이를 확인하세요.",
    "review_source_operation": "원본 로그를 확인하세요. 원인은 아직 확정되지 않았습니다.",
}

# 다듬기 작업의 llm_spec 고정 값. 다르면 다른 입력 형식·확인 규칙·결과 형식으로 만든 결과를 같은 이름으로 저장하게 된다.
POLISH_LLM_SPEC = {"evidence_version": ep.EVIDENCE_VERSION, "prompt_version": ep.PROMPT_VERSION,
                   "output_schema_version": ep.OUTPUT_SCHEMA_VERSION}
# 생성 실패 → 작업 실패 코드(재시도 없음: ValueError로 올려 runner가 종료 실패로 남긴다).
POLISH_GENERATION_CODES = frozenset({"generation_busy", "generation_timeout"})


def facts_for(evidence):
    # methodResults 원본은 모델에 넘기지 않는다. 키가 없는 근거는 이전과 같다.
    items = [(key, value) for key, value in evidence.items() if key != "methodResults"]
    return [{"id": f"fact_{index}", "name": key, "value": value}
            for index, (key, value) in enumerate(items)]


def validate_output(output, facts, contribution_status="not_computed"):
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
            "causeStatus": "unconfirmed", "contributionStatus": contribution_status}


def explain_saved(job):
    """이전 형식(`saved-decision-v1`). Store exact bytes as text as PostgreSQL jsonb reorders object keys."""
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


def _polish_receipt(sentences):
    """`polish_explanation` 1회. HTTP·같은 프로세스 경로의 실패를 작업 코드로 맞춘다(모두 재시도 없음)."""
    try:
        return generate(ep.GENERATION_KIND, {"sentences": sentences}, report_timeout=True)
    except GenerationRejected as exc:  # HTTP 422: 요청 형식·토큰 예산 거부
        raise ValueError("generation_contract_rejected") from exc
    except GenerationUnavailable as exc:
        code = str(exc)
        raise ValueError(code if code in POLISH_GENERATION_CODES else "generation_service_unavailable") from exc
    except ValueError as exc:  # 같은 프로세스 경로의 요청 거부·토큰 예산, HTTP 응답 형식 어긋남
        raise ValueError("generation_contract_rejected") from exc
    except Exception as exc:
        # 같은 프로세스 경로의 예상 밖 오류(CUDA 등)도 재시도하지 않는다. 재시도 경로로 가면 다음 시도가
        # max_attempts(1)로 끝나 오류 코드가 max_attempts로 덮인다.
        raise ValueError("generation_service_unavailable") from exc


def explain_polish(job):
    """`summary-sentences-v1` 작업 1건 → 결과 `evidence-polish-result-v1`."""
    spec = job.get("llmSpec")
    if (not isinstance(spec, dict) or any(spec.get(key) != value for key, value in POLISH_LLM_SPEC.items())
            or not isinstance(spec.get("model_version"), str) or not spec["model_version"]):
        raise ValueError(ep.INPUT_REJECTED)
    plan = ep.parse_input(job.get("input"))
    receipt = _polish_receipt(plan["sentences"])
    if not isinstance(receipt, dict):
        raise ValueError("generation_contract_rejected")
    # 다른 모델로 만든 문단은 이 작업의 계보(model_version)로 저장하지 않는다.
    if receipt.get("modelVersion") != spec["model_version"]:
        raise ValueError("llm_model_mismatch")
    if receipt.get("promptVersion") != ep.PROMPT_VERSION:
        raise ValueError("generation_prompt_version_mismatch")
    truncated, output = receipt.get("truncated"), receipt.get("output")
    if not isinstance(truncated, bool) or not isinstance(output, str):
        raise ValueError("generation_contract_rejected")
    return ep.build_result(plan, spec["model_version"], output, truncated)


def explain(job):
    spec = job.get("llmSpec")
    if isinstance(spec, dict) and spec.get("evidence_version") == ep.EVIDENCE_VERSION:
        return explain_polish(job)
    return explain_saved(job)
