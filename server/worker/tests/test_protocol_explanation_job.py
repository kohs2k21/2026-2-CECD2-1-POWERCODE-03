"""운영 작업 explain_protocol(protocol_explanation_job.py) 단위 시험. DB·GPU·네트워크 없음 — DB 행과 생성은 가짜다."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generation_client  # noqa: E402
import protocol_explanation as pe  # noqa: E402
import protocol_explanation_job as job_module  # noqa: E402
from generation_client import GenerationRejected, GenerationUnavailable  # noqa: E402
from generation_service import DesignModel, GenerationBusy, GenerationGate, serve  # noqa: E402

SOURCE = str(uuid.uuid4())
CANDIDATE_JOB = str(uuid.uuid4())
DECISION = str(uuid.uuid4())
LLM_SPEC = {"evidence_version": pe.FACTS_FORMAT, "input_evidence_hash": "a" * 64, "model_version": "qwen-base-test",
            "prompt_version": pe.PROMPT_VERSION, "output_schema_version": job_module.RESULT_FORMAT, "reuse_key": "b" * 64}

DRAFTS = {"version": 1, "parameters": {"minTransactions": 30, "rarePercent": 1},
          "interfaces": [{"interfaceId": "IF_PAY", "status": "draft", "transactions": 106, "pathTransactions": 100,
                          "branchingTransactions": 0, "distinctPaths": 2, "excludedReasons": {"chain_broken": 6},
                          "paths": [{"channels": ["GW", "CORE"], "transactions": 96, "ratio": 0.96, "rare": False,
                                     "fromBranching": 0},
                                    {"channels": ["GW", "AUX", "CORE"], "transactions": 4, "ratio": 0.04, "rare": False,
                                     "fromBranching": 0}],
                          "omittedPaths": {"paths": 0, "transactions": 0}}]}
PERIOD = {"from": "2026-01-05T00:00:00.000Z", "until": "2026-01-19T00:00:00.000Z"}
RULES = [{"id": "hard:proto-0000abcd", "interfaceId": "IF_PAY", "enabled": True, "severity": "critical",
          "paths": [{"channels": ["GW", "CORE"]}]}]
REVIEW = {"version": 1, "kind": "path_unmatched", "interfaceId": "IF_PAY", "protocolRuleId": "hard:proto-0000abcd",
          "ruleSetRevision": 7, "routes": [{"channels": ["GW", "AUX", "CORE"], "pathKey": "GW:AUX:CORE", "matched": False}],
          "routesReason": None}


def observed_spec(**target):
    return {"kind": "explain_protocol", "sourceId": SOURCE,
            "target": {"kind": "observed_paths", "jobId": CANDIDATE_JOB, "interfaceId": "IF_PAY", "ruleSetRevision": 7,
                       **target}}


def alert_spec():
    return {"kind": "explain_protocol", "sourceId": SOURCE, "target": {"kind": "alert", "decisionId": DECISION}}


def job_row(kind="recommend_relations", status="succeeded", drafts=DRAFTS, source=SOURCE):
    return (kind, status, drafts, PERIOD, source)


class FakeConn:
    """execute(sql, params) → 정해 둔 행. 실행 순서와 트랜잭션 경계를 기록한다."""

    def __init__(self, rows):
        self.rows = rows
        self.log = []

    def execute(self, sql, params=None):
        self.log.append(("execute", " ".join(sql.split()), params))
        key = "job" if sql == job_module.JOB_SQL else "rules" if sql == job_module.RULES_SQL else \
            "decision" if sql == job_module.DECISION_SQL else None
        return SimpleNamespace(fetchone=lambda: self.rows.get(key))

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))


def run_job(spec, rows, output=None, *, error=None, receipt=None):
    """작업 1건을 가짜 DB 행·가짜 생성으로 돌린다. 생성 입력도 돌려준다."""
    calls = []

    def fake_generate(kind, payload, *, report_timeout=False):
        calls.append((kind, payload, report_timeout))
        if error is not None:
            raise error
        if receipt is not None:
            return receipt
        return {"output": output, "truncated": False, "adapterSha": "base-rev", "promptVersion": pe.PROMPT_VERSION}

    with patch.object(job_module, "generate", fake_generate):
        result = job_module.explain_protocol(FakeConn(rows), {"input": spec, "llmSpec": dict(LLM_SPEC)})
    return result, calls


def facts_of(spec, rows):
    plan = job_module.parse_input(spec, LLM_SPEC)
    return job_module.build_facts(plan, job_module.read_sources(FakeConn(rows), plan))


class InputTests(unittest.TestCase):
    def test_both_targets_parse(self):
        plan = job_module.parse_input(observed_spec(), LLM_SPEC)
        self.assertEqual((plan["kind"], plan["interfaceId"], plan["ruleSetRevision"]), ("observed_paths", "IF_PAY", 7))
        self.assertEqual(job_module.parse_input(alert_spec(), LLM_SPEC)["decisionId"], uuid.UUID(DECISION))

    def test_contract_example_is_accepted(self):
        # API 접수 시험과 같은 파일(contracts/protocol-explanation-job.example.json). llm_spec 고정 값도 계약과 같아야 한다.
        example = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "protocol-explanation-job.example.json")
                             .read_text(encoding="utf-8"))
        for name in ("observedPaths", "alert"):
            with self.subTest(name=name):
                case = example[name]
                self.assertEqual(case["request"], case["inputSpec"])  # input_spec = 요청 본문 그대로
                plan = job_module.parse_input(case["inputSpec"], example["llmSpec"])
                self.assertEqual(plan["kind"], case["inputSpec"]["target"]["kind"])
                alert = {"kind": "explain_protocol", "input": case["inputSpec"]}
                from trainer import runner
                self.assertEqual(runner._llm_result_decision_id(alert), case["inputDecisionId"])

    def test_malformed_input_is_rejected_before_reading(self):
        bad = [None, {**observed_spec(), "extra": 1}, {**observed_spec(), "kind": "explain"},
               {**observed_spec(), "sourceId": "{" + SOURCE + "}"}, observed_spec(ruleSetRevision=True),
               observed_spec(ruleSetRevision=-1), observed_spec(interfaceId=""), observed_spec(interfaceId="x" * 513),
               {**observed_spec(), "target": {"kind": "observed_paths", "jobId": CANDIDATE_JOB}},
               {**alert_spec(), "target": {"kind": "alert", "decisionId": DECISION, "ruleSetRevision": 7}},
               {**alert_spec(), "target": {"kind": "other"}}]
        for spec in bad:
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, "^protocol_explanation_input_invalid$"):
                job_module.parse_input(spec, LLM_SPEC)

    def test_llm_spec_must_name_this_prompt_and_format(self):
        for key, value in (("prompt_version", "protocol-explain-prompt-v3"), ("output_schema_version", "x"),
                           ("evidence_version", "saved-decision-v1"), ("model_version", "")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "^protocol_explanation_llm_spec_invalid$"):
                job_module.parse_input(observed_spec(), {**LLM_SPEC, key: value})


class SourceTests(unittest.TestCase):
    def test_reads_one_read_only_transaction_and_ends_it(self):
        conn = FakeConn({"job": job_row(), "rules": (RULES,)})
        job_module.read_sources(conn, job_module.parse_input(observed_spec(), LLM_SPEC))
        self.assertEqual(conn.log[0], ("commit",))  # 앞선 queue_wait 조회 트랜잭션을 닫고 시작
        self.assertEqual(conn.log[1][1], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertEqual([entry[2] for entry in conn.log[2:4]], [(uuid.UUID(CANDIDATE_JOB),), (7,)])
        self.assertEqual(conn.log[-1], ("rollback",))
        alert = FakeConn({"decision": (REVIEW,), "rules": (RULES,)})
        job_module.read_sources(alert, job_module.parse_input(alert_spec(), LLM_SPEC))
        self.assertEqual([entry[2] for entry in alert.log[2:4]], [(uuid.UUID(DECISION), uuid.UUID(SOURCE)), (7,)])

    def test_observed_facts_match_the_kit_rule(self):
        facts, mapping, revision = facts_of(observed_spec(), {"job": job_row(), "rules": (RULES,)})
        expected, expected_map = pe.observed_paths_facts("IF_PAY", DRAFTS["interfaces"][0], RULES[0], period=PERIOD,
                                                         parameters=DRAFTS["parameters"], alias=True)
        self.assertEqual((facts, mapping, revision), (expected, expected_map, 7))
        self.assertEqual(facts["interface"], "IF-01")
        self.assertNotIn("IF_PAY", json.dumps(facts))  # 모델 입력에는 별칭만
        unregistered, _, _ = facts_of(observed_spec(), {"job": job_row(), "rules": ([],)})
        self.assertFalse(unregistered["registered"])
        unobserved, _, _ = facts_of(observed_spec(interfaceId="IF_IDLE"), {
            "job": job_row(), "rules": ([{**RULES[0], "interfaceId": "IF_IDLE"}],)})
        self.assertFalse(unobserved["observed"]["available"])

    def test_alert_facts_use_the_decision_revision(self):
        facts, mapping, revision = facts_of(alert_spec(), {"decision": (REVIEW,), "rules": (RULES,)})
        self.assertEqual((facts["kind"], facts["alertKind"], revision), ("alert", "path_unmatched", 7))
        self.assertEqual(mapping["interfaces"], {"IF-01": "IF_PAY"})
        unregistered = {**REVIEW, "kind": "interface_unregistered", "protocolRuleId": None, "routes": [],
                        "routesReason": "chain_broken", "ruleSetRevision": None}
        facts, _, revision = facts_of(alert_spec(), {"decision": (unregistered,)})
        self.assertEqual((facts["routesReason"]["label"], revision), ("선행 사슬 끊김", None))
        # 근거의 룰 세트 버전 행이 없어도 미등록 알림은 허용 경로가 필요 없어 사실을 만든다(버전 값은 근거 그대로).
        facts, _, revision = facts_of(alert_spec(), {"decision": ({**unregistered, "ruleSetRevision": 7},), "rules": None})
        self.assertEqual((facts["registered"], facts["allowed"], revision), (False, None, 7))

    def test_missing_target_and_missing_facts_codes(self):
        not_found = [(observed_spec(), {"job": None, "rules": (RULES,)}),
                     (observed_spec(), {"job": job_row(kind="train"), "rules": (RULES,)}),
                     (observed_spec(), {"job": job_row(status="failed"), "rules": (RULES,)}),
                     (observed_spec(), {"job": job_row(source=str(uuid.uuid4())), "rules": (RULES,)}),
                     (observed_spec(), {"job": job_row(), "rules": None}),
                     (alert_spec(), {"decision": None})]
        for spec, rows in not_found:
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "^protocol_explanation_target_not_found$"):
                facts_of(spec, rows)
        unavailable = [(observed_spec(), {"job": job_row(drafts=None), "rules": (RULES,)}),
                       (observed_spec(), {"job": job_row(drafts={**DRAFTS, "version": 2}), "rules": (RULES,)}),
                       (observed_spec(interfaceId="IF_NONE"), {"job": job_row(), "rules": (RULES,)}),
                       (observed_spec(), {"job": job_row(drafts={**DRAFTS, "interfaces": [
                           {**DRAFTS["interfaces"][0], "status": "weird"}]}), "rules": (RULES,)}),
                       (alert_spec(), {"decision": (None,)}),
                       (alert_spec(), {"decision": ({**REVIEW, "version": 2},), "rules": (RULES,)}),
                       (alert_spec(), {"decision": (REVIEW,), "rules": None})]  # 불일치 알림인데 리비전 항목 없음
        for spec, rows in unavailable:
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "^protocol_explanation_facts_unavailable$"):
                facts_of(spec, rows)


class JobTests(unittest.TestCase):
    rows = {"job": job_row(), "rules": (RULES,)}

    def facts(self):
        return facts_of(observed_spec(), self.rows)[0]

    def grounded(self):
        facts = self.facts()
        p2 = next(pid for pid, chain in facts["paths"].items() if chain == ["CH-01", "CH-03", "CH-02"])
        return {"explanation": f"인터페이스 IF-01의 관측 경로 {p2}(CH-01 → CH-03 → CH-02)는 허용 경로에 없습니다. "
                               "선행 사슬 끊김으로 인해 경로를 만들지 못한 거래는 6건입니다.",
                "reviewPoints": [f"경로 {p2}가 정상 거래 흐름인지 확인합니다."]}, p2

    def test_success_restores_real_ids_after_checking_aliases(self):
        output, p2 = self.grounded()
        result, calls = run_job(observed_spec(), self.rows, output)
        self.assertEqual(calls, [("explain_protocol", {"facts": self.facts()}, True)])  # 별칭 사실만, 시간 초과 구분
        self.assertEqual(result["explanation"].split(" 선행")[0],
                         f"인터페이스 IF_PAY의 관측 경로 {p2}(GW → AUX → CORE)는 허용 경로에 없습니다.")
        self.assertEqual(result["reviewPoints"], [f"경로 {p2}가 정상 거래 흐름인지 확인합니다."])
        self.assertEqual(result["paths"][p2], ["GW", "AUX", "CORE"])
        self.assertEqual(result["interfaceId"], "IF_PAY")
        self.assertEqual(result["check"]["passed"], True)
        self.assertEqual(result["facts"], {"format": pe.FACTS_FORMAT, "kind": "observed_paths",
                                           "sha256": job_module.facts_sha256(self.facts())})
        self.assertEqual((result["promptVersion"], result["modelVersion"], result["baseRevision"], result["format"]),
                         (pe.PROMPT_VERSION, "qwen-base-test", "base-rev", "protocol-explanation-v1"))
        self.assertEqual(result["target"], observed_spec()["target"])
        self.assertNotIn("verdict", result)

    def test_check_failure_hides_text_and_keeps_reason_codes(self):
        output = {"explanation": "인터페이스 IF-01의 거래는 7건이며 배포로 인해 경로가 바뀐 것으로 보입니다.", "reviewPoints": []}
        with self.assertRaises(job_module.CheckFailed) as caught:
            run_job(observed_spec(), self.rows, output)
        summary = caught.exception.summary
        self.assertEqual(caught.exception.code, "protocol_explanation_check_failed")
        self.assertEqual(summary["check"]["passed"], False)
        self.assertEqual(summary["check"]["failures"], ["numbers_subset", "no_forbidden"])
        for key in ("explanation", "reviewPoints", "paths"):
            self.assertNotIn(key, summary)
        self.assertNotIn("배포", json.dumps(summary, ensure_ascii=False))

    def test_strict_json_failures_from_both_paths(self):
        for error in (GenerationRejected("invalid_generation_output"), json.JSONDecodeError("x", "doc", 0),
                      ValueError("invalid_generation_response")):
            with self.subTest(error=repr(error)), self.assertRaisesRegex(ValueError, "^protocol_explanation_json_invalid$"):
                run_job(observed_spec(), self.rows, error=error)
        for receipt in ({"output": {"explanation": "x"}, "truncated": True, "promptVersion": pe.PROMPT_VERSION},
                        {"output": ["x"], "truncated": False, "promptVersion": pe.PROMPT_VERSION}):
            with self.subTest(receipt=receipt), self.assertRaisesRegex(ValueError, "^protocol_explanation_json_invalid$"):
                run_job(observed_spec(), self.rows, receipt=receipt)

    def test_generation_codes_are_passed_through(self):
        with self.assertRaisesRegex(GenerationUnavailable, "^generation_busy$"):  # 재시도용으로 그대로
            run_job(observed_spec(), self.rows, error=GenerationUnavailable("generation_busy"))
        for code in ("generation_timeout", "generation_service_unavailable", "generation_service_not_configured"):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, f"^{code}$"):
                run_job(observed_spec(), self.rows, error=GenerationUnavailable(code))
        for error in (GenerationRejected("generation_contract_rejected"), ValueError("generation_token_budget_exceeded")):
            with self.subTest(error=repr(error)), self.assertRaisesRegex(ValueError, "^generation_contract_rejected$"):
                run_job(observed_spec(), self.rows, error=error)
        with self.assertRaisesRegex(ValueError, "^generation_prompt_version_mismatch$"):
            run_job(observed_spec(), self.rows, receipt={"output": {}, "truncated": False,
                                                         "promptVersion": "protocol-explain-prompt-v3"})

    def test_alias_restore_is_one_pass_and_bounded(self):
        mapping = {"enabled": True, "interfaces": {"IF-01": "CH-02"}, "channels": {"CH-01": "A", "CH-02": "B",
                                                                                     "CH-10": "J"}}
        self.assertEqual(job_module.restore_aliases("IF-01 → CH-01, CH-10, CH-02", mapping), "CH-02 → A, J, B")
        self.assertEqual(job_module.restore_aliases("CH-010 XCH-01 CH-01_x P1", mapping), "CH-010 XCH-01 CH-01_x P1")
        self.assertEqual(job_module.restore_aliases("IF-01", {"enabled": False, "interfaces": {"IF-01": "X"}}), "IF-01")


class GenerationServiceTests(unittest.TestCase):
    """DesignModel.generate("explain_protocol") — 대화형 등급·어댑터 끔·서버 프롬프트. GPU 없이 가짜 모델."""

    def model(self, text):
        import numpy as np
        seen = {}

        class Tokenizer:
            eos_token_id = 0

            def __call__(self, *_args, **_kwargs):
                return {"input_ids": [1, 2]}

            def apply_chat_template(self, messages, **_kwargs):
                seen["messages"] = messages
                return "text"

            def decode(self, *_args, **_kwargs):
                return text

        def set_adapter(name):
            seen["adapter"] = name

        model = DesignModel.__new__(DesignModel)
        model.manifest = {"generation": {"maximum_input_tokens": 100}, "base": {"revision": "base"}}
        model.gate = GenerationGate()
        model.tokenizer = Tokenizer()
        model.torch = SimpleNamespace(no_grad=nullcontext, manual_seed=lambda _: None,
                                      tensor=lambda value, device: np.array(value), ones_like=np.ones_like)
        model.model = SimpleNamespace(set_adapter=set_adapter, disable_adapter=lambda: seen.setdefault("disabled", True)
                                      and nullcontext(),
                                      generate=lambda input_ids, **_: np.array([[*input_ids[0], 5, 0]]))
        return model, seen

    def test_interactive_base_model_with_server_prompt(self):
        facts = facts_of(observed_spec(), {"job": job_row(), "rules": (RULES,)})[0]
        model, seen = self.model('{"explanation":"설명입니다.","reviewPoints":[]}')
        receipt = model.generate("explain_protocol", {"facts": facts}, report_timeout=True)
        self.assertEqual(receipt, {"output": {"explanation": "설명입니다.", "reviewPoints": []}, "truncated": False,
                                   "adapterSha": "base", "promptVersion": pe.PROMPT_VERSION})
        self.assertEqual(seen["messages"], pe.build_messages(facts))
        self.assertTrue(seen["disabled"])
        self.assertNotIn("adapter", seen)

    def test_request_shape_checked_before_waiting_then_wait_cap_applies(self):
        facts = facts_of(observed_spec(), {"job": job_row(), "rules": (RULES,)})[0]
        model, _ = self.model("{}")
        model.gate.acquire(False)  # 다른 생성이 GPU를 쓰는 중
        try:
            with patch.dict("os.environ", {"LLM_EXPLANATION_WAIT_SECONDS": "0.05"}):
                for value in ({}, {"facts": facts, "extra": 1}, {"facts": {**facts, "format": "x"}}):
                    with self.subTest(value=list(value)), self.assertRaisesRegex(ValueError, "^invalid_generation_request$"):
                        model.generate("explain_protocol", value)
                with self.assertRaises(GenerationBusy):
                    model.generate("explain_protocol", {"facts": facts})
        finally:
            model.gate.release()
        self.assertEqual(model.gate.snapshot(), {"running": False, "interactive": 0, "job": 0})

    def test_non_json_output_over_http_maps_to_strict_json_code(self):
        class Model:
            def generate(self, kind, value, report_timeout=False):
                assert kind == "explain_protocol" and report_timeout
                raise json.JSONDecodeError("x", "doc", 0)

        token = "t" * 32
        server = serve(Model(), token, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        # 프록시 변수만 빼고 나머지 환경은 둔다(Windows는 환경을 모두 비우면 기본 TLS 문맥 생성이 실패한다).
        kept = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
        environment = {**kept, "DESIGN_MODEL_URL": f"http://127.0.0.1:{server.server_port}", "DESIGN_MODEL_TOKEN": token}
        environment.pop("DESIGN_MODEL_HTTP_HOST", None)
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaises(GenerationRejected) as caught:
                generation_client.generate("explain_protocol", {"facts": {}}, report_timeout=True)
            self.assertEqual(caught.exception.code, "invalid_generation_output")
            with patch.object(job_module, "generate", generation_client.generate), \
                    self.assertRaisesRegex(ValueError, "^protocol_explanation_json_invalid$"):
                job_module.generate_output({"facts": "x"})


if __name__ == "__main__":
    unittest.main()
