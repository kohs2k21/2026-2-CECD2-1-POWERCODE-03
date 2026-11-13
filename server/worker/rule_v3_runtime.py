"""Rule v3 묶음(feature_rule_spec) 운영 실행. 번들 파일은 고치지 않고 export(`research_bundle --rule-v3`)를 읽는다.

- 모델: 생성 서비스(`DesignModel`)가 올린 Base·토크나이저를 그대로 쓰는 `LocalModels` 하위 클래스. 번들 `generate`가
  같은 PeftModel에 Rule v3 LoRA를 `Rule` 이름으로 더 올린다(서비스 어댑터 `feature`·`rule`과 이름이 다르다).
- GPU 차례: 생성 시도 1번마다 `GenerationGate`의 작업 차례를 잡는다(설명이 시도 사이에 먼저 들어간다).
- 시간 상한: 차례 안에서만 Base `generation_config.max_time`을 `LLM_RECOMMENDATION_SECONDS`(기본 240)로 둔다.
  끊긴 시도는 `TimeoutError("generation_timeout")`로 올려 번들 수리 시도로 넘어가지 않게 한다.
- 근거 문장: 번들 Rule system 문장 끝에 한국어 지시 한 줄을 붙인다(주말 GPU 시험 `--reason-language ko`와 같은 문장).
- 흐름: 번들 CLI `validate-feature` → `rule`과 같은 함수 순서를 직접 부른다(`Dataset` → `compile_feature` →
  `validate_feature` → `rule_flow`, 수리 최대 1회). CLI 경로와 Rule 입력이 같음은 시험이 확인한다.
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from research_bundle import rule_v3_package

KO_LINE = ("Additional run instruction (outside the frozen bundle): write every decision reason field in Korean; "
           "keep all other fields, identifiers and numbers exactly as specified.")
PROVIDER = "rule_v3"
MAX_MODEL_CALLS = 2  # 첫 시도 + 수리 1회(번들 rule_flow 상한과 같음)
RATIONALE_MAX = 500
HANGUL_MINIMUM = Decimal("0.5")

# 번들 validate_rule 오류 코드 분류. 이 밖의 코드(설명 문장 검사, official_facts.rule_description_conflicts)는 문장 쪽이다.
SCHEMA_CODES = frozenset({"schema", "JSON_or_length"})
TARGET_CODES = frozenset({
    "immutable_reference", "role_coverage", "unprovided_evidence", "unsupported_needed_resource",
    "verified_unit_already_provided", "scope_reference", "proposed_contract", "no_applicable_contract",
    "request_evidence_contract", "deferred_contract", "Boolean_condition_type", "numeric_condition_type",
    "condition_evaluation", "condition_feature_type_conflict", "public_diagnostic_replaced", "no_evaluable_observations",
    "observations_are_available", "HR_contract", "observed_grounding", "unusable_reference", "baseline_grounding",
    "no_current_numeric_observations", "structurally_unreachable", "provided_baseline_requested_again"})

# 근거 문장의 숫자. 앞에 글자·숫자·밑줄·소수점이 붙은 숫자(`p95`, `r000001`)는 세지 않는다.
# 뒤에 붙은 단위(`7210.5ms`, `20개`)는 값이므로 센다.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:,\d{3})*(?:\.\d+)?(?!\d|\.\d)")
_PERCENTILE_KEY = re.compile(r"^p(\d{1,2})$")


class RuleV3NotConfigured(RuntimeError):
    """Rule v3 export가 없거나, 같은 프로세스의 생성 모델이 없다(외부 모델 `--once` 모드)."""

    def __init__(self) -> None:
        super().__init__("rule_v3_not_configured")


def load(root: Path):
    """export를 검증해 `specmodel`을 올리고 Rule system 문장에 한국어 지시를 한 번만 붙인다."""
    package = rule_v3_package(Path(root))
    rule_logic = package.rule_logic
    if not getattr(rule_logic.rule_system, "_korean_reason", False):
        original = rule_logic.rule_system

        def korean_rule_system():
            return original() + "\n" + KO_LINE

        korean_rule_system._korean_reason = True
        rule_logic.rule_system = korean_rule_system
    return package


def shared_model_class(base_class):
    """`LocalModels`(또는 시험용 같은 모양의 클래스)를 받아 공유 모델 하위 클래스를 만든다."""

    class SharedRuleModel(base_class):
        def __init__(self, base, model, tokenizer, compiler, gate, integrity):
            super().__init__(base, None, None)
            # 미리 채우면 번들 `_cpu`의 Base 전 파일 SHA 검사와 `_load`의 두 번째 적재를 건너뛴다.
            # integrity는 실제로 한 검사만 적는다(서비스의 Base 메타데이터 SHA 확인, 전 파일 검사 아님).
            self.model, self.tokenizer, self.compiler, self.integrity = model, tokenizer, compiler, integrity
            self.gate = gate
            self.records: list[dict[str, Any]] = []

        def _config(self):
            base = self.model.get_base_model() if hasattr(self.model, "get_base_model") else self.model
            return base.generation_config

        def generate(self, kind, messages, schema, dest, budget, call, maximum=1536, arm="Base"):
            if kind != "rule":
                raise ValueError("rule_v3_rule_only")
            limit = float(os.environ.get("LLM_RECOMMENDATION_SECONDS", "240"))
            with self.gate.slot(False):
                config = self._config()
                previous = getattr(config, "max_time", None)
                config.max_time = limit
                try:
                    out = super().generate(kind, messages, schema, dest, budget, call, maximum=maximum, arm=arm)
                finally:
                    config.max_time = previous
            timed_out = out.get("parse_error") is not None and not out.get("length_limit_hit") and \
                float(out.get("seconds") or 0) >= limit
            self.records.append({"call": call, "seconds": out.get("seconds"), "outputTokens": out.get("output_tokens"),
                                 "lengthLimitHit": bool(out.get("length_limit_hit")),
                                 "parseError": out.get("parse_error") is not None, "timedOut": timed_out,
                                 "limitSeconds": limit, "adapterSha": (out.get("model") or {}).get("adapter_weight_sha"),
                                 "peakBytes": (out.get("GPU_memory") or {}).get("peak")})
            if timed_out:
                raise TimeoutError("generation_timeout")
            return out

    return SharedRuleModel


def grammar_compiler(tokenizer, base: Path):
    """번들 `_cpu`와 같은 xgrammar 컴파일러(서비스 시작 시 1회)."""
    import xgrammar
    from transformers import AutoConfig
    config = AutoConfig.from_pretrained(str(base), local_files_only=True)
    if config.architectures != ["Qwen3_5ForConditionalGeneration"]:
        raise ValueError("rule_v3_base_class_mismatch")
    return xgrammar.GrammarCompiler(xgrammar.TokenizerInfo.from_huggingface(
        tokenizer, vocab_size=config.text_config.vocab_size), max_threads=4)


class RuleV3Runtime:
    def __init__(self, package, models):
        self.package = package
        self.models = models
        manifest = json.loads((Path(package.local_models.PACKAGE) / "model_manifest.json").read_text(encoding="utf-8"))
        self.adapter_sha = manifest["Rule"]["weight_sha256"]

    def run_field(self, paths: Mapping[str, Path], out: Path) -> dict[str, Any]:
        """필드 하나의 번들 Rule 실행. 번들 `InputFailure`·`ModelFailure`·`TimeoutError`는 그대로 나간다."""
        p = self.package
        dataset, reference = p.dataset.Dataset(paths["application"]), p.dataset.Dataset(paths["reference"])
        proposal, request, plan = (json.loads(Path(paths[k]).read_text(encoding="utf-8"))
                                   for k in ("proposal", "request", "rule_plan"))
        compiled = p.calculations.compile_feature(dataset, proposal, "provided_definition_CPU")
        if not compiled["passed"]:
            raise p.io.InputFailure("rule_v3_feature_not_compiled")
        errors = p.validation.validate_feature(dataset, request, {"schema": "feature5.2", "candidates": [proposal],
                                                                  "stop_reason": None}, compiled)
        if errors:
            raise p.io.InputFailure("rule_v3_feature_not_validated")
        out = p.io.new_output(out)
        budget = p.io.Budget(out, MAX_MODEL_CALLS)
        self.models.records = []
        result = p.pipeline.rule_flow(dataset, reference, proposal, compiled, plan, self.models, budget, out)
        return {"result": result, "attempts": list(self.models.records)}


def attach(root: Path, service, base: Path) -> RuleV3Runtime:
    """생성 서비스 모델에 Rule v3 경로를 붙인다. 실패하면 서비스 시작이 실패한다(구성 오류를 숨기지 않음)."""
    package = load(root)
    files = json.loads((Path(package.local_models.PACKAGE) / "models/base_files.json").read_text(encoding="utf-8"))
    integrity = {"repo_id": files.get("repo_id"), "revision": files.get("revision"), "verified_files": 0,
                 "service_metadata_verified": True}
    cls = shared_model_class(package.local_models.LocalModels)
    models = cls(base, service.model, service.tokenizer, grammar_compiler(service.tokenizer, base), service.gate,
                 integrity)
    return RuleV3Runtime(package, models)


def classify(errors) -> tuple[str, dict[str, bool]]:
    """`rule_failed` 마지막 시도 오류 → (`rejectedReason`, `reviews`)."""
    codes = {error.get("code") for error in errors or []}
    if not codes or codes & SCHEMA_CODES:
        return "invalid_generation_output", {"schema": False, "facts": False, "expectedTarget": False}
    prose = bool(codes - TARGET_CODES)
    if codes & TARGET_CODES:
        return "expected_target_mismatch", {"schema": True, "facts": not prose, "expectedTarget": False}
    return "factual_review_failed", {"schema": True, "facts": False, "expectedTarget": True}


def hangul_ratio(text: str) -> Decimal:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return Decimal(0)
    hangul = sum("가" <= ch <= "힣" or "ᄀ" <= ch <= "ᇿ" or "㄰" <= ch <= "㆏"
                 for ch in letters)
    return Decimal(hangul) / Decimal(len(letters))


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def text_numbers(text: str) -> list[Decimal]:
    return [value for value in (_decimal(m.group(0)) for m in _NUMBER.finditer(text)) if value is not None]


def fact_numbers(value: Any, found: set[Decimal] | None = None) -> set[Decimal]:
    """Rule 입력(피처·측정·기준선·범위·공식 계산 사실)에 나오는 모든 숫자. 키 `p95`류는 그 분위 숫자를 더한다."""
    found = set() if found is None else found
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, (int, float, Decimal)):
        number = _decimal(str(value))
        if number is not None:
            found.add(number)
    elif isinstance(value, str):
        found.update(text_numbers(value))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            match = _PERCENTILE_KEY.match(str(key))
            if match:
                found.add(Decimal(match.group(1)))
            found.update(text_numbers(str(key)))
            fact_numbers(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            fact_numbers(item, found)
    return found


def rationale_checks(text: str, facts: Any) -> dict[str, bool]:
    """서버 점검: (a) 글자 중 한글 50% 이상 (b) 문장 숫자가 모두 입력 사실에 있는 숫자."""
    known = fact_numbers(facts)
    return {"korean": hangul_ratio(text) >= HANGUL_MINIMUM,
            "numbers": all(number in known for number in text_numbers(text))}
