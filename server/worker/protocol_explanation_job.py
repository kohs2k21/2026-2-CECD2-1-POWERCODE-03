"""운영 LLM 작업 `explain_protocol` — 프로토콜 경로 설명(contracts/protocol-explanation-job.md).

흐름: input_spec·llm_spec 재검사 → 읽기 전용 트랜잭션 하나에서 원천을 읽어 사실(`protocol-explain-facts-v1`, 별칭) 만들기
→ 생성(대화형 등급, 어댑터 없음, generation_client) → 별칭 상태로 자동 점검 → 통과하면 별칭을 실제 ID로 되돌려 결과.

- 원천·선택 규칙은 시험 키트 export(research/experiments/protocol-explain/export_cases.py)와 같다. 이 모듈은 research/를 import하지
  않고, 필요한 읽기 규칙만 옮겼다. 경로 집계는 하지 않는다(Trainer·Detector 산출물 그대로).
  - 관측 경로: 요청이 고른 `recommend_relations` 성공 작업 결과의 `protocolPathDrafts`·`period` + 요청 룰 세트 리비전의 `protocolRules[]`
  - 알림: 판정 `evidence.protocolReview`(protocol-rules.md 5.3) + 그 판정이 적은 룰 세트 리비전의 항목
- 사실·이름표·프롬프트·점검은 protocol_explanation.py 순수 함수 그대로다.
- 점검 실패는 재시도하지 않는다(같은 사실·greedy 생성이라 같은 출력). 결과 문장은 남기지 않고 사유 코드만 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Mapping

import protocol_explanation as pe
from generation_client import GenerationRejected, GenerationUnavailable, generate

KIND = "explain_protocol"
RESULT_FORMAT = "protocol-explanation-v1"
INTERFACE_ID_MAX = 512
# llm_spec 고정 값(API가 접수 때 같은 값을 쓴다). 다르면 다른 사실·프롬프트로 만든 결과를 이 작업 이름으로 저장하게 된다.
LLM_SPEC_FIXED = {"evidence_version": pe.FACTS_FORMAT, "prompt_version": pe.PROMPT_VERSION,
                  "output_schema_version": RESULT_FORMAT}
# 재시도 없는 생성 실패(코드 그대로 저장). generation_busy만 재시도한다(explain과 같은 흐름).
TERMINAL_GENERATION_CODES = frozenset({"generation_timeout", "generation_service_unavailable",
                                       "generation_service_not_configured"})

TARGET_NOT_FOUND = "protocol_explanation_target_not_found"
FACTS_UNAVAILABLE = "protocol_explanation_facts_unavailable"
JSON_INVALID = "protocol_explanation_json_invalid"
CHECK_FAILED = "protocol_explanation_check_failed"
INPUT_INVALID = "protocol_explanation_input_invalid"
LLM_SPEC_INVALID = "protocol_explanation_llm_spec_invalid"

JOB_SQL = """SELECT kind::text, status::text, result_summary->'protocolPathDrafts', result_summary->'period',
                    result_summary->>'sourceId'
               FROM run_jobs WHERE id = %s"""
RULES_SQL = "SELECT snapshot->'protocolRules' FROM ops_rule_set_revisions WHERE revision = %s"
# 판정이 요청 출처의 관측인지 함께 본다(explain 접수와 같은 결합).
DECISION_SQL = """SELECT d.evidence->'protocolReview'
                    FROM run_process_decisions AS d JOIN data_observations AS o ON o.id = d.process_observation_id
                   WHERE d.id = %s AND o.source_id = %s"""


class CheckFailed(Exception):
    """점검 실패. 작업은 재시도 없이 실패하고 `summary`(문장 없음, 사유 코드만)를 결과 요약으로 남긴다."""

    code = CHECK_FAILED

    def __init__(self, summary: dict[str, Any]) -> None:
        super().__init__(self.code)
        self.summary = summary


# ---------------------------------------------------------------- 입력

def _uuid(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise ValueError(INPUT_INVALID)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError(INPUT_INVALID) from exc
    if str(parsed) != value.lower():  # 중괄호·urn 등 다른 표기는 받지 않는다(API UUID 정규식과 같은 모양)
        raise ValueError(INPUT_INVALID)
    return parsed


def parse_input(spec: Any, llm_spec: Any) -> dict[str, Any]:
    """input_spec·llm_spec 재검사(접수가 정상이면 나오지 않는 방어). 형식이 다르면 원천을 읽기 전에 멈춘다."""
    if not isinstance(spec, Mapping) or set(spec) != {"kind", "sourceId", "target"} or spec.get("kind") != KIND:
        raise ValueError(INPUT_INVALID)
    source_id = _uuid(spec["sourceId"])
    target = spec["target"]
    if not isinstance(target, Mapping):
        raise ValueError(INPUT_INVALID)
    if target.get("kind") == "observed_paths":
        interface = target.get("interfaceId")
        revision = target.get("ruleSetRevision")
        if (set(target) != {"kind", "jobId", "interfaceId", "ruleSetRevision"} or not isinstance(interface, str)
                or not 1 <= len(interface) <= INTERFACE_ID_MAX or isinstance(revision, bool)
                or not isinstance(revision, int) or revision < 0):
            raise ValueError(INPUT_INVALID)
        plan = {"kind": "observed_paths", "jobId": _uuid(target["jobId"]), "interfaceId": interface,
                "ruleSetRevision": revision}
    elif target.get("kind") == "alert":
        if set(target) != {"kind", "decisionId"}:
            raise ValueError(INPUT_INVALID)
        plan = {"kind": "alert", "decisionId": _uuid(target["decisionId"])}
    else:
        raise ValueError(INPUT_INVALID)
    if not isinstance(llm_spec, Mapping) or any(llm_spec.get(key) != value for key, value in LLM_SPEC_FIXED.items()) \
            or not isinstance(llm_spec.get("model_version"), str) or not llm_spec["model_version"]:
        raise ValueError(LLM_SPEC_INVALID)
    plan["sourceId"] = source_id
    plan["target"] = dict(target)
    return plan


# ---------------------------------------------------------------- 원천 읽기(DB)

def read_sources(conn: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    """REPEATABLE READ, READ ONLY 트랜잭션 하나에서 필요한 행만 읽고 끝낸다(생성 동안 트랜잭션을 열어 두지 않음)."""
    conn.commit()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    try:
        if plan["kind"] == "observed_paths":
            job = conn.execute(JOB_SQL, (plan["jobId"],)).fetchone()
            revision = conn.execute(RULES_SQL, (plan["ruleSetRevision"],)).fetchone()
            return {"job": job, "revision": revision}
        found = conn.execute(DECISION_SQL, (plan["decisionId"], plan["sourceId"])).fetchone()
        review = found[0] if found is not None else None
        number = review.get("ruleSetRevision") if isinstance(review, Mapping) else None
        revision = None
        if isinstance(number, int) and not isinstance(number, bool):
            revision = conn.execute(RULES_SQL, (number,)).fetchone()
        return {"decision": found, "revision": revision}
    finally:
        conn.rollback()  # 읽기 전용이라 남길 것이 없다


def rules_by_interface(items: Any) -> dict[str, dict[str, Any]]:
    """스냅샷 `protocolRules[]`(없거나 v2 스냅샷이면 빈 목록) → interfaceId별 항목. 키트 export와 같은 규칙."""
    if not isinstance(items, list):
        return {}
    return {item["interfaceId"]: item for item in items
            if isinstance(item, Mapping) and isinstance(item.get("interfaceId"), str)}


def _same_source(value: Any, source_id: uuid.UUID) -> bool:
    try:
        return isinstance(value, str) and uuid.UUID(value) == source_id
    except ValueError:
        return False


def build_facts(plan: Mapping[str, Any], sources: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """읽은 행 → (사실, 별칭 표, 사실에 쓴 룰 세트 리비전). DB 없이 시험할 수 있게 분리했다."""
    try:
        if plan["kind"] == "observed_paths":
            job = sources.get("job")
            if job is None or job[0] != "recommend_relations" or job[1] != "succeeded" \
                    or not _same_source(job[4], plan["sourceId"]):
                raise ValueError(TARGET_NOT_FOUND)
            if sources.get("revision") is None:
                raise ValueError(TARGET_NOT_FOUND)
            drafts, period = job[2], job[3]
            if not isinstance(drafts, Mapping) or drafts.get("version") != 1 or not isinstance(drafts.get("interfaces"), list):
                raise ValueError(FACTS_UNAVAILABLE)  # 경로 집계 전 결과(키 없음)·지원하지 않는 버전
            interface = plan["interfaceId"]
            draft = next((item for item in drafts["interfaces"]
                          if isinstance(item, Mapping) and item.get("interfaceId") == interface), None)
            entry = rules_by_interface(sources["revision"][0]).get(interface)
            if draft is None and entry is None:
                raise ValueError(FACTS_UNAVAILABLE)  # 학습 기간에 관측되지 않았고 등록도 되지 않음 → 설명할 사실 없음
            parameters = drafts.get("parameters")
            facts, mapping = pe.observed_paths_facts(
                interface, draft, entry, period=period if isinstance(period, Mapping) else None,
                parameters=parameters if isinstance(parameters, Mapping) else None, alias=True)
            return facts, mapping, plan["ruleSetRevision"]
        if sources.get("decision") is None:
            raise ValueError(TARGET_NOT_FOUND)
        review = sources["decision"][0]
        if not isinstance(review, Mapping):
            raise ValueError(FACTS_UNAVAILABLE)  # 프로토콜 방법이 T가 아닌 판정(근거 키 없음)
        number = review.get("ruleSetRevision")
        revision = sources.get("revision")
        entry = rules_by_interface(revision[0]).get(review.get("interfaceId")) if revision is not None else None
        facts, mapping = pe.alert_facts(review, entry, alias=True)
        return facts, mapping, number if isinstance(number, int) and not isinstance(number, bool) else None
    except ValueError as exc:
        if str(exc) in (TARGET_NOT_FOUND, FACTS_UNAVAILABLE):
            raise
        raise ValueError(FACTS_UNAVAILABLE) from exc  # 원천 형식이 사실 함수 기준과 다름(protocol_explain_*)
    except (TypeError, AttributeError, KeyError) as exc:
        raise ValueError(FACTS_UNAVAILABLE) from exc


# ---------------------------------------------------------------- 생성·점검·별칭

def facts_sha256(facts: Mapping[str, Any]) -> str:
    """사실 요약 해시: 모델에 넣은 사실 JSON(사용자 메시지와 같은 직렬화 — 키 순서 유지, 공백 없음)의 UTF-8 SHA-256."""
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def generate_output(facts: Mapping[str, Any]) -> dict[str, Any]:
    """대화형 생성 1회. HTTP·같은 프로세스 경로의 실패를 같은 작업 코드로 맞춘다."""
    try:
        receipt = generate(KIND, {"facts": facts}, report_timeout=True)
    except GenerationRejected as exc:  # HTTP 422: 출력이 JSON 하나가 아님 / 그 밖의 계약 거부(토큰 예산 등)
        raise ValueError(JSON_INVALID if exc.code == "invalid_generation_output" else "generation_contract_rejected") from exc
    except GenerationUnavailable as exc:
        code = str(exc)
        if code == "generation_busy":
            raise  # 재시도 가능(runner가 generation_busy로 남기고 다시 넣음)
        raise ValueError(code if code in TERMINAL_GENERATION_CODES else "generation_service_unavailable") from exc
    except json.JSONDecodeError as exc:  # 같은 프로세스 경로: generation_service가 출력 전체를 json.loads로 읽다 실패
        raise ValueError(JSON_INVALID) from exc
    except ValueError as exc:
        # HTTP 경로의 응답 검사(끊긴 출력·객체 아님)는 엄격 JSON 실패, 그 밖(토큰 예산·요청 거부)은 계약 거부다.
        raise ValueError(JSON_INVALID if str(exc) == "invalid_generation_response" else "generation_contract_rejected") from exc
    if receipt.get("promptVersion") != pe.PROMPT_VERSION:
        raise ValueError("generation_prompt_version_mismatch")
    if receipt.get("truncated") is not False or not isinstance(receipt.get("output"), dict):
        raise ValueError(JSON_INVALID)  # 출력 한도에서 끊김·객체가 아님
    return receipt


def restore_aliases(text: str, mapping: Mapping[str, Any]) -> str:
    """별칭(IF-01·CH-01) → 실제 ID. 한 번에 바꿔 실제 ID가 별칭 모양이어도 다시 바뀌지 않는다. 경로 id(P1)는 그대로 둔다."""
    table = {**(mapping.get("interfaces") or {}), **(mapping.get("channels") or {})}
    if not mapping.get("enabled") or not table:
        return text
    pattern = re.compile(r"(?<![A-Za-z0-9_\-])(?:" + "|".join(re.escape(alias) for alias in sorted(table, key=len, reverse=True))
                         + r")(?![A-Za-z0-9_])")
    return pattern.sub(lambda match: table[match.group(0)], text)


def real_paths(facts: Mapping[str, Any], mapping: Mapping[str, Any]) -> dict[str, list[str]]:
    channels = (mapping.get("channels") or {}) if mapping.get("enabled") else {}
    return {path_id: [channels.get(channel, channel) for channel in chain]
            for path_id, chain in (facts.get("paths") or {}).items()}


def explain_protocol(conn: Any, job: Mapping[str, Any]) -> dict[str, Any]:
    """작업 1건. 성공이면 결과 `protocol-explanation-v1`, 점검 실패면 `CheckFailed`, 그 밖은 코드 ValueError."""
    plan = parse_input(job.get("input"), job.get("llmSpec"))
    facts, mapping, revision = build_facts(plan, read_sources(conn, plan))
    receipt = generate_output(facts)
    checked = pe.check_output(json.dumps(receipt["output"], ensure_ascii=False), facts)
    interface_ids = mapping.get("interfaces") or {}
    base = {"format": RESULT_FORMAT, "target": plan["target"],
            "interfaceId": interface_ids.get(facts["interface"], facts["interface"]),
            "ruleSetRevision": revision,
            "facts": {"format": pe.FACTS_FORMAT, "kind": facts["kind"], "sha256": facts_sha256(facts)},
            "promptVersion": pe.PROMPT_VERSION, "modelVersion": job["llmSpec"]["model_version"],
            "baseRevision": receipt.get("adapterSha")}
    check = {"passed": checked["passed"], "failures": list(checked["failures"]), "coverage": dict(checked["coverage"])}
    if not checked["passed"]:
        raise CheckFailed({**base, "check": check})
    output = checked["output"]
    return {**base,
            "explanation": restore_aliases(output["explanation"].strip(), mapping),
            "reviewPoints": [restore_aliases(point.strip(), mapping) for point in output["reviewPoints"]],
            "paths": real_paths(facts, mapping),
            "check": check}
