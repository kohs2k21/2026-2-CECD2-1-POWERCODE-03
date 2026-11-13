"""실행 폴더 판정: 사례별 PASS/FAIL·사유, summary.json·summary.md.

    python judge.py --run <output-root>/<YYYYMMDD-HHMMSS>-<run-name>

기대 상태는 합성 사례면 index.json, 실데이터 사례면 CSV에서 다시 계산한 사실(capture_layout.facts_from_case)로 정한다.
p95는 번들 코드와 별도로 CSV에서 다시 계산한다. 설명 문장 검사는 번들 검증기 결과(rule_bundle attempts errors)를
그대로 쓰고 다시 구현하지 않는다. 실데이터 사례는 judgment·summary에 수치(p95·임계값)와 인터페이스를 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capture_layout as layout  # noqa: E402

FORMAT = "rule-v3-baseline-summary-v1"
MAX_SECONDS = 240
REASON_MAX = 320
SERVICE_RATIONALE_MAX = 500  # threshold_recommendation.RATIONALE_MAX
TEXT_CODES = {"field_boundary_unfinished_explanation", "unfinished_explanation", "quoted_count_scope",
              "current_activation_count_contradiction", "condition_operator_prose_conflict",
              "complete_output_future_value_unavailability_conflict",
              "calculated_unavailable_not_missing_test_unassessed"}
COLUMNS = ["사례", "기대 상태", "결과 상태", "p95 재계산 일치", "수리 전/후", "초", "length_limit", "reason 형식",
           "서비스 조건 변환", "PASS/FAIL·사유"]


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def service_item(decision: dict[str, Any] | None, code_p95: Decimal | None, real: bool) -> dict[str, Any]:
    """계획된 서비스 변환(state→status, reason→rationale, condition.op→operator, threshold=코드 p95)의 시험 변환."""
    if not decision:
        return {"status": None, "operator": None, "threshold": None, "rationale": None}
    proposed = decision.get("state") == "proposed"
    threshold = layout.decimal_text(code_p95) if proposed and code_p95 is not None else None
    return {"status": decision.get("state"), "operator": (decision.get("condition") or {}).get("op") if proposed else None,
            "threshold": ("withheld_real_data" if real and threshold is not None else threshold),
            "rationale": decision.get("reason")}


def check_service(item: dict[str, Any], decision: dict[str, Any] | None, code_p95: Decimal | None) -> list[str]:
    problems = []
    if decision is None:
        return ["no_decision"]
    if item["status"] == "proposed":
        if item["operator"] != "gt":
            problems.append("operator_not_gt")
        if code_p95 is None or layout.canonical_number((decision.get("condition") or {}).get("value")) != \
                layout.canonical_number(code_p95):
            problems.append("condition_value_not_code_p95")
    elif item["operator"] is not None or item["threshold"] is not None:
        problems.append("operator_or_threshold_not_null")
    if not item["rationale"] or len(item["rationale"]) > SERVICE_RATIONALE_MAX:
        problems.append("rationale_length")
    return problems


def judge_case(run_dir: Path, case_id: str, spec: dict[str, Any], cases_dir: Path, mode: str) -> dict[str, Any]:
    real = spec.get("kind") == "real"
    case_dir = run_dir / case_id
    result = read_json(case_dir / "case_result.json") if (case_dir / "case_result.json").is_file() else None
    facts = layout.facts_from_case(cases_dir / spec["path"], spec["field"])
    expected = spec.get("expected") or facts["expected"]
    reasons: list[str] = []
    out: dict[str, Any] = {"case_id": case_id, "field": spec["field"], "kind": spec.get("kind"), "mode": mode,
                           "expected_states": expected["states"], "expected_needed": expected["needed"],
                           "expected_baseline_state": expected["baseline_state"]}
    if spec.get("expected") and (facts["expected"]["states"] != expected["states"] or
                                 facts["expected"]["needed"] != expected["needed"]):
        reasons.append("index_expected_differs_from_recomputed_facts")
    if spec.get("expected") and expected.get("reference_p95_decimal") is not None and \
            layout.decimal_text(facts["p95"]) != expected["reference_p95_decimal"]:
        reasons.append("index_p95_differs_from_recomputed")
    out["counts"] = {k: facts[k] for k in ("reference_rows", "reference_valid", "current_rows", "current_valid")}
    out["reference_constant"] = facts["constant"]
    if result is None:
        out.update(result_state=None, passed=False, reasons=["case_not_run"])
        return out
    steps = result.get("steps", {})
    if "held_window_check" in steps:
        out.update(result_state=None, passed=False, reasons=["held_window_overlap_not_run"])
        return out
    if not result["input_sha256"]["files_match_index"]:
        reasons.append("input_sha_mismatch")
    if (steps.get("validate_feature") or {}).get("exit") != 0:
        reasons.append("validate_feature_failed")
    if "replay" in steps and steps["replay"]["exit"] != 0:
        reasons.append("replay_failed")
    elif "replay" not in steps and "validate_feature_failed" not in reasons:
        reasons.append("replay_not_run")
    cpu = result.get("cpu_checks") or {}
    if cpu:
        out["cpu"] = {k: cpu.get(k) for k in ("baseline_state", "current_valid", "bundle_p95_matches_independent",
                                              "expected_states_accepted", "oracle_validator_errors")}
        if cpu.get("baseline_state") != expected["baseline_state"]:
            reasons.append("bundle_baseline_state_unexpected")
        if cpu.get("bundle_p95_matches_independent") is False:
            reasons.append("bundle_p95_differs_from_independent")
        if not cpu.get("expected_states_accepted"):
            reasons.append("bundle_validator_rejects_expected_state")
    elif "validate_feature_failed" not in reasons:
        reasons.append("cpu_checks_missing")

    p95_match: bool | None = cpu.get("bundle_p95_matches_independent")
    out["service_precheck_note"] = ("service code returns no_applicable without LLM for a constant reference"
                                    if facts["constant"] else None)
    if mode == "cpu_only":
        out.update(result_state=None, p95_match=p95_match, repair=None, seconds=None, length_limit=None,
                   reason_format=None, service_conversion=None)
    else:
        rule_path = case_dir / "rule" / "rule_bundle.json"
        rule = read_json(rule_path) if rule_path.is_file() else None
        if (steps.get("rule") or {}).get("exit") != 0:
            reasons.append(f"rule_exit_{(steps.get('rule') or {}).get('exit')}")
        attempts = result.get("attempts") or []
        decision = None
        if rule and isinstance(rule.get("prediction"), dict) and rule["prediction"].get("decisions"):
            decision = rule["prediction"]["decisions"][0]
        state = decision.get("state") if decision else None
        if state not in expected["states"]:
            reasons.append("state_unexpected")
        if decision is not None and sorted(decision.get("needed") or []) != sorted(expected["needed"]):
            reasons.append("needed_unexpected")
        baseline = (rule or {}).get("input", {}).get("baseline", {})
        if baseline.get("state") != expected["baseline_state"]:
            reasons.append("rule_input_baseline_state_unexpected")
        independent = layout.canonical_number(facts["p95"]) if facts["p95"] is not None else None
        if decision and decision.get("condition") and state in ("proposed", "deferred"):
            condition = decision["condition"]
            p95_match = condition.get("op") == "gt" and \
                layout.canonical_number(condition.get("value")) == layout.canonical_number(baseline.get("value")) == independent
        elif baseline.get("state") == "verified":
            p95_match = layout.canonical_number(baseline.get("value")) == independent
        else:
            p95_match = None
        if p95_match is False:
            reasons.append("p95_mismatch")
        rule_attempts = (rule or {}).get("attempts") or []
        first_ok = bool(rule_attempts and rule_attempts[0].get("passed"))
        final_ok = bool(rule and rule.get("status") == "rule_verified")
        repair = {"attempts": len(rule_attempts), "first_attempt_passed": first_ok, "final_passed": final_ok,
                  "first_attempt_error_codes": sorted({e.get("code") for e in (rule_attempts[0].get("errors") or [])})
                  if rule_attempts else []}
        seconds = [a.get("seconds") for a in attempts]
        if not attempts or any(s is None or s > MAX_SECONDS for s in seconds):
            reasons.append("seconds_over_limit_or_missing")
        length_hit = any(a.get("length_limit_hit") is not False for a in attempts) if attempts else None
        if length_hit is not False:
            reasons.append("length_limit_hit_or_missing")
        reason_text = (decision or {}).get("reason") or ""
        final_codes = {e.get("code") for e in ((rule_attempts[-1].get("errors") or []) if rule_attempts else [])}
        reason_problems = []
        if not 1 <= len(reason_text) <= REASON_MAX:
            reason_problems.append("length")
        if final_codes & TEXT_CODES:
            reason_problems.append("validator:" + ",".join(sorted(final_codes & TEXT_CODES)))
        reason_warning = None if reason_text.rstrip()[-1:] in (".", "!", "?", "。", ")") else "no_terminal_punctuation"
        if reason_problems:
            reasons.append("reason_format")
        item = service_item(decision, facts["p95"], real)
        service_problems = check_service(item, decision, facts["p95"])
        if service_problems:
            reasons.append("service_conversion")
        out.update(result_state=state, result_needed=(decision or {}).get("needed"), p95_match=p95_match,
                   repair=repair, seconds=seconds, length_limit=length_hit,
                   reason_format={"ok": not reason_problems, "problems": reason_problems, "length": len(reason_text),
                                  "warning": reason_warning},
                   service_conversion={"ok": not service_problems, "problems": service_problems,
                                       "item": item if not real else {**item, "rationale": "(see rule_bundle.json)"}})
    out["passed"] = not reasons
    out["reasons"] = reasons
    if not real:
        out["reference_p95_decimal"] = layout.decimal_text(facts["p95"])
    return out


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def row_for(j: dict[str, Any]) -> list[str]:
    repair = j.get("repair")
    if repair is None:
        repair_text = "-"
    elif not repair["attempts"]:
        repair_text = "응답 없음"
    elif repair["first_attempt_passed"]:
        repair_text = "1차 통과"
    else:
        repair_text = "1차 실패→수리 " + ("통과" if repair["final_passed"] else "실패")
    seconds = "+".join(f"{s:.1f}" if isinstance(s, (int, float)) else "?" for s in j["seconds"]) if j.get("seconds") else "-"
    reason = j.get("reason_format")
    reason_text = "-" if reason is None else ("OK" if reason["ok"] else "FAIL:" + ",".join(reason["problems"])) + \
        (f" ({reason['warning']})" if reason and reason.get("warning") else "")
    service = j.get("service_conversion")
    service_text = "-" if service is None else ("OK" if service["ok"] else "FAIL:" + ",".join(service["problems"]))
    verdict = ("PASS" if j["passed"] else "FAIL") + ("" if j["passed"] else "·" + ",".join(j["reasons"]))
    result_state = j.get("result_state") or ("미실행(CPU)" if j["mode"] == "cpu_only" else "-")
    return [j["case_id"], "/".join(j["expected_states"]), result_state, _cell(j.get("p95_match")), repair_text,
            seconds, _cell(j.get("length_limit")), reason_text, service_text, verdict]


def judge_run(run_dir: Path, index_path: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    run = read_json(run_dir / "run.json")
    index_path = Path(index_path or run["cases_index"])
    index = read_json(index_path)
    by_id = {c["case_id"]: c for c in index["cases"]}
    judgments = []
    for case_id in run["selected_cases"]:
        j = judge_case(run_dir, case_id, by_id[case_id], index_path.parent, run["mode"])
        if (run_dir / case_id).is_dir():
            write_json(run_dir / case_id / "judgment.json", j)
        judgments.append(j)
    passed = sum(j["passed"] for j in judgments)
    summary = {"format": FORMAT, "run_dir_name": run_dir.name, "mode": run["mode"], "status": run.get("status"),
               "variant": run.get("variant", {}).get("label"), "cases_kind": index.get("kind"),
               "passed": passed, "total": len(judgments), "max_seconds_per_attempt": MAX_SECONDS,
               "input_assumptions": index.get("input_assumptions", []),
               "rule_verified_is_not_pass": "rule_verified means the bundle validator accepted the output; PASS also "
                                            "needs the expected state/needed, code p95 and the other columns",
               "cases": [{"case_id": j["case_id"], "field": j["field"], "row": dict(zip(COLUMNS, row_for(j))),
                          "passed": j["passed"], "reasons": j["reasons"]} for j in judgments]}
    write_json(run_dir / "summary.json", summary)
    lines = [f"# Rule v3 기준선 판정 — {run_dir.name}", "", f"**통과 {passed}/{len(judgments)}**", "",
             f"- 모드: {run['mode']} · 실행 상태: {run.get('status')} · 변형: {summary['variant']} · 사례 종류: {index.get('kind')}",
             f"- 시간 기준: 시도당 {MAX_SECONDS}초 이하 · reason 1~{REASON_MAX}자 · 실데이터 사례는 상태·개수·참/거짓만 표시",
             "- 번들 rule_verified(검증기 통과)는 PASS와 다르다: PASS는 기대 상태·needed·코드 p95 등 아래 열을 모두 만족해야 한다",
             *[f"- 입력 가정: {a}" for a in index.get("input_assumptions", [])], "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for j in judgments:
        lines.append("| " + " | ".join(c.replace("|", "/") for c in row_for(j)) + " |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--index", type=Path, help="기본값: run.json의 cases_index")
    args = parser.parse_args(argv)
    summary = judge_run(args.run, args.index)
    print(f"통과 {summary['passed']}/{summary['total']}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
