"""AI 근거 요약 다듬기(evidence_polish·explanation 다듬기 경로·생성 서비스 polish_explanation) 시험. DB·GPU 없이 돈다.

공용 벡터 `server/contracts/evidence-polish-check-vectors.json`을 읽어 확인 결과와 코드 문장 추출값을 대조한다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evidence_polish as ep
import explanation
import generation_client
from generation_client import GenerationRejected, GenerationUnavailable, generate
from generation_service import (DesignModel, GenerationBusy, GenerationGate, PolishModelUnavailable, polish_model_id,
                                serve)

VECTORS = Path(__file__).resolve().parents[2] / "contracts" / "evidence-polish-check-vectors.json"
# evidence-template-v3 코드 문장(공용 벡터 (a) example_a_hard_success_with_error)과 그 다듬은 문단.
HARD = "이상 규칙 - 처리 상태가 S(성공)인데 오류 건수(ERROR_COUNT)가 0보다 커서 이상으로 판단했습니다."
HARD_GOOD = HARD.replace("S(성공)인데", "S(성공)이지만")
MODEL = "Qwen/Qwen3.5-2B@0123abcd"


def spec(sentences=(HARD,), value_tokens=(), **extra):
    sentences = list(sentences)
    value = {"kind": "explain", "sourceId": "src", "decisionId": "dec", "configVersionId": "cfg",
             "templateVersion": "evidence-template-v3", "sentences": sentences,
             "stateWords": ep.Extracted(" ".join(sentences), list(value_tokens)).states, "valueTokens": list(value_tokens)}
    value.update(extra)
    return value


def llm_spec(**extra):
    return {"evidence_version": "summary-sentences-v1", "prompt_version": "evidence-polish-v3",
            "output_schema_version": "evidence-polish-result-v1", "model_version": MODEL,
            "input_evidence_hash": "h", "reuse_key": "k", **extra}


def job(input_spec=None, llm=None):
    return {"kind": "explain", "input": input_spec or spec(), "llmSpec": llm or llm_spec()}


class VectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(VECTORS.read_text(encoding="utf-8"))

    def test_prompt_version_matches(self):
        self.assertEqual(self.document["promptVersion"], ep.PROMPT_VERSION)

    def test_every_case(self):
        for case in self.document["cases"]:
            with self.subTest(case["id"]):
                plan = ep.parse_input(spec(case["sentences"], case["valueTokens"]))
                result = ep.build_result(plan, MODEL, case["candidate"], False)
                expected = case["expected"]
                self.assertEqual((result["paragraph"]["source"], result["check"]["code"]),
                                 (expected["source"], expected["code"]))
                self.assertEqual(result["check"]["status"], "passed" if expected["code"] is None else "failed")
                code = plan["code"]
                self.assertEqual(code.states, expected["stateWords"])
                self.assertEqual([list(n) for n in code.numbers], expected["numbers"])
                self.assertEqual(code.order, expected["orderWords"])
                self.assertEqual(sorted(list(t) for t in code.tokens), expected["maskedTokens"])
                if expected["source"] == "code":
                    self.assertEqual(result["paragraph"]["text"], " ".join(case["sentences"]))
                self.assertEqual(case["kind"] == "pass", expected["source"] == "llm")

    def test_each_check_code_has_a_failing_case_and_pass_cases_exist(self):
        codes = {case["expected"]["code"] for case in self.document["cases"] if case["kind"] == "fail"}
        self.assertEqual(codes, set(ep.CHECK_CODES))
        self.assertGreaterEqual(sum(case["kind"] == "pass" for case in self.document["cases"]), 10)
        fallback = {case["id"] for case in self.document["cases"] if case["kind"] == "expected_fallback"}
        self.assertGreaterEqual(len(fallback), 6)

    def test_backend_summary_vectors_are_accepted_end_to_end(self):
        # 백엔드 공용 벡터 (a)의 이상 요약(접수 입력 = 문장·판정 낱말·원천 값)을 워커가 그대로 받고, 코드 문단 자체는 확인을 통과한다.
        # 틀 버전이 두 벡터에서 같아야 한다(백엔드 틀·워커 이름표·순서 낱말이 같은 버전을 가리킴).
        summary = json.loads((VECTORS.parent / "evidence-summary-vectors.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["templateVersion"], self.document["templateVersion"])
        accepted = 0
        for case in summary["cases"]:
            expected = case["expected"]
            if not expected or case["input"]["evidence"]["composite"].get("anomalous") is not True:
                continue
            with self.subTest(case["id"]):
                plan = ep.parse_input({**spec(expected["sentences"], expected["valueTokens"]), "stateWords": expected["stateWords"],
                                       "templateVersion": summary["templateVersion"]})
                self.assertIsNone(ep.check_candidate(plan["codeText"], plan))
                # 이름표 어휘 밖 라틴 식별자로 남는 것은 사전에 없는 변수 이름뿐이다(상태·건수 이름은 이름표로 가려짐)
                left = [value for kind, value in plan["code"].tokens if kind == "identifier"]
                self.assertTrue(all(value == "queue_depth" for value in left), left)
                accepted += 1
        self.assertGreaterEqual(accepted, 60)


class MaskingAndNumberTests(unittest.TestCase):
    def test_number_normalization(self):
        for raw, normal in (("4,200", "4200"), ("4200.0", "4200"), ("0.1250", "0.125"), ("0000", "0000"), ("0", "0"),
                            ("-3.50", "-3.5"), ("100.000", "100")):
            with self.subTest(raw):
                self.assertEqual(ep.normalize_number(raw), normal)
        self.assertNotEqual(ep.normalize_number("0000"), ep.normalize_number("0"))

    def test_identifier_and_time_boundaries(self):
        found = ep.Extracted("인터페이스 IF-001. 그리고 A:B: 시각 2026-11-25T09:00:00Z, ERROR_COUNT_X 값", [])
        self.assertEqual(found.tokens, [("time", "2026-11-25T09:00:00Z"), ("identifier", "IF-001"),
                                        ("identifier", "A:B"), ("identifier", "ERROR_COUNT_X")])
        self.assertEqual(found.numbers, [])

    def test_ms_unit_boundary(self):
        unit = ep.Extracted("800 ms를 900ms와", [])
        self.assertEqual(unit.numbers, [("800", "ms"), ("900", "ms")])
        self.assertEqual(unit.tokens, [])
        word = ep.Extracted("800 msg를 ms 값", [])
        self.assertEqual(word.numbers, [("800", "")])
        self.assertEqual(word.tokens, [("identifier", "msg"), ("identifier", "ms")])

    def test_placeholders_are_private_use_and_raw_private_use_is_replaced(self):
        found = ep.Extracted("이상 규칙이  값", [])
        self.assertEqual(found.masked[0], "")
        self.assertIn("￿", found.masked)
        self.assertEqual(found.placeholders, frozenset({""}))

    def test_value_tokens_masked_first_longest_first(self):
        found = ep.Extracted("단계 승인 확인 2단계와 확인 2", ["확인 2", "승인 확인 2단계"])
        self.assertEqual(sorted(found.tokens), [("value", "승인 확인 2단계"), ("value", "확인 2")])
        self.assertEqual(found.numbers, [])

    def test_v2_labels_and_order_words(self):
        found = ep.Extracted("처리 상태가 S(성공)인데 오류 건수(ERROR_COUNT)·재시도 횟수(RETRY_COUNT)·성공 건수(SUCCESS_COUNT)와 "
                             "처리량(TOTAL_COUNT)·오류 비율, 상태 Z(데이터없음)·N(미처리)", [])
        self.assertEqual(sorted(found.tokens), sorted([("label", "S(성공)"), ("label", "오류 건수(ERROR_COUNT)"), ("label", "재시도 횟수(RETRY_COUNT)"),
                                                       ("label", "성공 건수(SUCCESS_COUNT)"), ("label", "처리량(TOTAL_COUNT)"), ("label", "오류 비율"),
                                                       ("label", "Z(데이터없음)"), ("label", "N(미처리)")]))
        self.assertEqual(found.states, [])
        old = ep.Extracted("ERROR_COUNT·SUCCESS_COUNT·RETRY_COUNT·오류 비율 값", [])
        self.assertEqual(sorted(old.tokens), [("label", "ERROR_COUNT"), ("label", "RETRY_COUNT"), ("label", "SUCCESS_COUNT"), ("label", "오류 비율 값")],
                         "v1 labels stay so v1 sentences are checked the same way")
        order = ep.Extracted("기준을 넘어, 기준에 이르러, 기준에 못 미쳐, 평소보다 긴·짧은·높은·낮은 값, 평소와 다른 값, 앞 단계 없이", [])
        self.assertEqual(order.order, ["기준", "넘어", "기준", "이르러", "기준", "못", "미쳐", "평소", "긴", "짧은", "높은", "낮은", "평소", "다른", "없이"])
        self.assertEqual(ep.PROMPT_VERSION, "evidence-polish-v3")

    def test_method_prefixes(self):
        # v3 앞머리 `{방법 이름} - `: 방법 이름 자리표 + ` - `만, 문장 첫머리(문단 맨 앞·`.` 뒤 공백) 여부와 함께
        text = ("이상 규칙 - 가. 이상 징후 규칙·머신러닝 모델 - 나, 관계 규칙 - 다. 처리량(TOTAL_COUNT) - 라. "
                "정상 프로토콜 - 마. 머신러닝 모델은 바.")
        self.assertEqual(ep.Extracted(text, []).prefixes,
                         [("이상 규칙", True), ("이상 징후 규칙·머신러닝 모델", True), ("관계 규칙", False)])
        self.assertEqual(ep.Extracted("이상 규칙 - 가.", ["이상 규칙"]).prefixes, [], "a value token is not a method prefix")
        plan = ep.parse_input(spec([HARD]))
        self.assertIsNone(ep.check_candidate(HARD_GOOD, plan))
        for changed in (HARD_GOOD.replace("이상 규칙 - ", "이상 규칙은 "), HARD_GOOD.replace("이상 규칙 - ", "이상 규칙 -"),
                        HARD_GOOD.replace("이상 규칙 - ", "이상 규칙: "), "다듬으면 " + HARD_GOOD):
            with self.subTest(changed):
                self.assertIn(ep.check_candidate(changed, plan), ("polish_name_changed", "polish_markup"))
        self.assertEqual(ep.check_candidate("다듬으면 " + HARD_GOOD, plan), "polish_name_changed")
        # `-`는 코드 문단에 있을 때만 허용 기호(v2 문장처럼 앞머리가 없는 코드 문단에서는 그대로 실패)
        old = ep.parse_input(spec(["이상 규칙은 처리 상태가 F(실패)여서 이상으로 판단했습니다."]))
        self.assertEqual(ep.check_candidate("이상 규칙 - 처리 상태가 F(실패)여서 이상으로 판단했습니다.", old), "polish_markup")

    def test_length_cap(self):
        self.assertEqual(ep.length_cap("가" * 10), 12)
        self.assertEqual(ep.length_cap("가" * 301), 361)
        self.assertEqual(ep.length_cap("가" * 299), 359)
        plan = ep.parse_input(spec([HARD]))
        cap = ep.length_cap(HARD)
        padded = HARD_GOOD[:-1] + " " * (cap - len(HARD_GOOD) + 1) + "."
        self.assertEqual(len(padded), cap + 1)
        self.assertEqual(ep.check_candidate(padded, plan), "polish_too_long")
        self.assertIsNone(ep.check_candidate(padded[:-2] + ".", plan))

    def test_forbidden_counts_against_code(self):
        sentences = ["이상 규칙이 ERROR_COUNT가 0보다 커서 이상 원인 후보로 판단했습니다."]
        plan = ep.parse_input(spec(sentences))
        self.assertIsNone(ep.check_candidate("이상 규칙은 ERROR_COUNT가 0보다 커서 이상 원인 후보로 판단했습니다.", plan))
        self.assertEqual(ep.check_candidate("이상 규칙은 ERROR_COUNT가 0보다 커서 이상 원인 후보로 판단했고 원인입니다.", plan),
                         "polish_forbidden")


class InputTests(unittest.TestCase):
    def rejected(self, value):
        with self.assertRaisesRegex(ValueError, "^explanation_input_rejected$"):
            ep.parse_input(value)

    def test_valid_input_and_code_text(self):
        plan = ep.parse_input(spec([HARD, "머신러닝 모델 - 결과를 내지 못해 평가 불가입니다."]))
        self.assertEqual(plan["codeText"], HARD + " 머신러닝 모델 - 결과를 내지 못해 평가 불가입니다.")

    def test_shape_and_limits(self):
        base = spec()
        for key in ("kind", "sourceId", "decisionId", "configVersionId", "templateVersion", "sentences", "stateWords",
                    "valueTokens"):
            with self.subTest(missing=key):
                self.rejected({k: v for k, v in base.items() if k != key})
        self.rejected({**base, "savedEvidence": {}})
        self.rejected({**base, "kind": "explain_protocol"})
        self.rejected({**base, "decisionId": ""})
        # 구성 없는 판정(v04): configVersionId null은 받고, 빈 문자열·숫자는 거부(키 없음은 위 missing에서 거부).
        ep.parse_input({**base, "configVersionId": None})
        self.rejected({**base, "configVersionId": ""})
        self.rejected({**base, "configVersionId": 1})
        self.rejected(spec([]))
        self.rejected(spec(["가."] * 9))
        self.rejected(spec(["가" * 301]))
        self.rejected(spec(["가" * 300] * 4 + ["나."]))
        self.rejected(spec(["  "]))
        self.rejected(spec(["첫 줄.\n둘째 줄."]))
        self.rejected(spec(["자리표  문자."]))
        ep.parse_input(spec(["가" * 300] * 4))

    def test_value_tokens(self):
        sentence = "이상 규칙 - 처리 상태가 F(실패)여서 이상으로 판단했습니다."
        ep.parse_input(spec([sentence], ["F(실패)"]))
        self.rejected(spec([sentence], ["P(처리중)"]))
        self.rejected(spec([sentence], [""]))
        self.rejected(spec([sentence], ["가" * 121]))
        self.rejected(spec([sentence], ["F"] * 17))
        self.rejected({**spec([sentence]), "valueTokens": "F"})

    def test_state_words_must_match_worker_extraction(self):
        self.rejected({**spec(), "stateWords": []})
        self.rejected({**spec(), "stateWords": ["이상", "이상"]})
        self.rejected({**spec(), "stateWords": ["정상"]})

    def test_allowed_numbers_multiset(self):
        sentences = ["머신러닝 모델 - 평소 800 ms보다 긴 처리 시간 4,200 ms가 가장 크게 영향을 주어(62%) 이상 징후로 판단했습니다."]
        ep.parse_input(spec(sentences, allowedNumbers=["800", "4200.0", "62"]))
        ep.parse_input(spec(sentences, allowedNumbers=["62", "4,200", "800"]))
        self.rejected(spec(sentences, allowedNumbers=["4200", "800"]))
        self.rejected(spec(sentences, allowedNumbers=["4200", "800", "62", "62"]))
        self.rejected(spec(sentences, allowedNumbers=["4200 ms", "800", "62"]))
        self.rejected(spec(sentences, allowedNumbers="4200"))

    def test_truncated_output_is_not_checked(self):
        plan = ep.parse_input(spec())
        result = ep.build_result(plan, MODEL, HARD_GOOD, True)
        self.assertEqual(result, {"outputSchemaVersion": "evidence-polish-result-v1", "decisionId": "dec",
                                  "templateVersion": "evidence-template-v3", "codeText": HARD,
                                  "paragraph": {"text": HARD, "source": "code"},
                                  "check": {"status": "not_run", "code": "generation_incomplete"}, "modelVersion": MODEL})

    def test_prompt_has_only_code_sentences(self):
        messages = ep.build_messages([HARD, "둘째 문장."])
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(messages[1]["content"], HARD + "\n둘째 문장.")


class ExplainPolishJobTests(unittest.TestCase):
    def run_job(self, receipt=None, error=None, value=None):
        calls = []

        def fake(kind, payload, **options):
            calls.append((kind, payload, options))
            if error is not None:
                raise error
            return receipt

        with patch.object(explanation, "generate", fake):
            result = explanation.explain(value or job())
        return result, calls

    def receipt(self, output=HARD_GOOD, truncated=False, model=MODEL, prompt="evidence-polish-v3"):
        return {"output": output, "truncated": truncated, "modelVersion": model, "promptVersion": prompt}

    def test_passed_polish(self):
        result, calls = self.run_job(self.receipt())
        self.assertEqual(calls, [("polish_explanation", {"sentences": [HARD]}, {"report_timeout": True})])
        self.assertEqual(result["paragraph"], {"text": HARD_GOOD, "source": "llm"})
        self.assertEqual(result["check"], {"status": "passed", "code": None})
        self.assertEqual(result["modelVersion"], MODEL)
        self.assertEqual(result["outputSchemaVersion"], "evidence-polish-result-v1")

    def test_config_less_decision_and_backend_key_set(self):
        # 백엔드 접수 키 집합(allowedNumbers 없음)과 같고, 구성 없는 판정(configVersionId null)도 다듬는다.
        self.assertEqual(ep.INPUT_KEYS, {"kind", "sourceId", "decisionId", "configVersionId", "templateVersion",
                                         "sentences", "stateWords", "valueTokens"})
        result, _ = self.run_job(self.receipt(), value=job({**spec(), "configVersionId": None}))
        self.assertEqual(result["paragraph"], {"text": HARD_GOOD, "source": "llm"})

    def test_check_failure_completes_with_code_text(self):
        result, _ = self.run_job(self.receipt(HARD_GOOD.replace("커서", "작아")))
        self.assertEqual(result["paragraph"], {"text": HARD, "source": "code"})
        self.assertEqual(result["check"], {"status": "failed", "code": "polish_direction_changed"})

    def test_truncated_completes_with_code_text(self):
        result, _ = self.run_job(self.receipt("이상 규칙은 처리", truncated=True))
        self.assertEqual(result["check"], {"status": "not_run", "code": "generation_incomplete"})

    def test_generation_failures_are_terminal_codes(self):
        for error, code in ((GenerationUnavailable("generation_busy"), "generation_busy"),
                            (GenerationUnavailable("generation_timeout"), "generation_timeout"),
                            (GenerationUnavailable("generation_service_unavailable"), "generation_service_unavailable"),
                            (GenerationUnavailable("generation_service_not_configured"), "generation_service_unavailable"),
                            (GenerationRejected(), "generation_contract_rejected"),
                            (ValueError("generation_token_budget_exceeded"), "generation_contract_rejected"),
                            (RuntimeError("CUDA error: device-side assert"), "generation_service_unavailable"),
                            (KeyError("input_ids"), "generation_service_unavailable")):
            with self.subTest(code=code, error=type(error).__name__), self.assertRaisesRegex(ValueError, f"^{code}$"):
                self.run_job(error=error)

    def test_model_and_prompt_mismatch(self):
        with self.assertRaisesRegex(ValueError, "^llm_model_mismatch$"):
            self.run_job(self.receipt(model="Qwen/Qwen3.5-4B@ffffffff"))
        with self.assertRaisesRegex(ValueError, "^llm_model_mismatch$"):
            self.run_job(self.receipt(model=None, truncated=True))
        with self.assertRaisesRegex(ValueError, "^generation_prompt_version_mismatch$"):
            self.run_job(self.receipt(prompt="evidence-polish-v0"))
        for receipt in ({**self.receipt(), "output": {"text": HARD_GOOD}}, {**self.receipt(), "truncated": None}, "text"):
            with self.subTest(receipt=receipt), self.assertRaisesRegex(ValueError, "^generation_contract_rejected$"):
                self.run_job(receipt)

    def test_llm_spec_and_input_rejected_before_generation(self):
        for llm in (llm_spec(prompt_version="evidence-polish-v0"), llm_spec(output_schema_version="x"),
                    llm_spec(model_version=""), {k: v for k, v in llm_spec().items() if k != "model_version"}):
            with self.subTest(llm=llm), self.assertRaisesRegex(ValueError, "^explanation_input_rejected$"):
                self.run_job(self.receipt(), value=job(llm=llm))
        _, calls = None, []
        with self.assertRaisesRegex(ValueError, "^explanation_input_rejected$"):
            _, calls = self.run_job(self.receipt(), value=job({**spec(), "stateWords": []}))
        self.assertEqual(calls, [])

    def test_old_format_jobs_keep_the_fact_selection_path(self):
        evidence = {"decisionId": "d", "ruleStatus": "succeeded"}
        text = json.dumps(evidence)
        import hashlib
        old = {"input": {"savedEvidenceJson": text, "savedEvidence": evidence, "decisionId": "d"},
               "llmSpec": {"evidence_version": "saved-decision-v1", "input_evidence_hash": hashlib.sha256(text.encode()).hexdigest()}}
        result, calls = self.run_job({"output": {"factIds": ["fact_1"], "nextChecks": ["review_source_operation"]},
                                      "truncated": False}, value=old)
        self.assertEqual(calls[0][0], "explain")
        self.assertEqual(result["facts"], [{"id": "fact_1", "name": "ruleStatus", "value": "succeeded"}])
        self.assertEqual(result["contributionStatus"], "not_computed")


class _PolishTokenizer:
    eos_token_id = 0

    def __init__(self, text):
        self.text, self.messages = text, None

    def __call__(self, *_args, **_kwargs):
        return {"input_ids": [1, 2]}

    def apply_chat_template(self, messages, **_kwargs):
        self.messages = messages
        return "text"

    def decode(self, *_args, **_kwargs):
        return self.text


class PolishGenerationServiceTests(unittest.TestCase):
    token = "t" * 32

    def model(self, text=HARD_GOOD, tail=(5, 0), loaded=True):
        import numpy as np
        model = DesignModel.__new__(DesignModel)
        model.gate = GenerationGate()
        model.torch = SimpleNamespace(no_grad=nullcontext, manual_seed=lambda _: None,
                                      tensor=lambda value, device: np.array(value), ones_like=np.ones_like)
        tokenizer = _PolishTokenizer(text)
        generator = SimpleNamespace(generate=lambda input_ids, **_: np.array([[*input_ids[0], *tail]]))
        model.polish = (tokenizer, generator) if loaded else None
        model.polish_model_id = MODEL if loaded else None
        return model, tokenizer

    def test_plain_text_receipt_with_model_id(self):
        model, tokenizer = self.model()
        receipt = model.generate("polish_explanation", {"sentences": [HARD]})
        self.assertEqual(receipt, {"output": HARD_GOOD, "truncated": False, "modelVersion": MODEL,
                                   "promptVersion": "evidence-polish-v3"})
        self.assertEqual(tokenizer.messages, ep.build_messages([HARD]))
        self.assertEqual(model.gate.snapshot(), {"running": False, "interactive": 0, "job": 0})

    def test_truncated_when_budget_is_used_without_eos(self):
        model, _ = self.model(tail=(5,) * 512)
        self.assertTrue(model.generate("polish_explanation", {"sentences": [HARD]})["truncated"])

    def test_request_shape_checked_before_model_and_gate(self):
        model, _ = self.model(loaded=False)
        for value in ({}, {"sentences": []}, {"sentences": [1]}, {"sentences": [HARD], "facts": {}}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_generation_request$"):
                model.generate("polish_explanation", value)
        with self.assertRaises(PolishModelUnavailable):
            model.generate("polish_explanation", {"sentences": [HARD]})

    def test_interactive_wait_cap_applies(self):
        model, _ = self.model()
        model.gate.acquire(False)
        try:
            with patch.dict("os.environ", {"LLM_EXPLANATION_WAIT_SECONDS": "0.05"}), self.assertRaises(GenerationBusy):
                model.generate("polish_explanation", {"sentences": [HARD]})
        finally:
            model.gate.release()

    def serve(self, model):
        server = serve(model, self.token, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, {"DESIGN_MODEL_URL": f"http://127.0.0.1:{server.server_port}", "DESIGN_MODEL_TOKEN": self.token}

    def test_http_health_reports_polish_model_and_generate_returns_text(self):
        import urllib.request
        model, _ = self.model()
        server, environment = self.serve(model)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(environment["DESIGN_MODEL_URL"] + "/health", timeout=10) as response:
            self.assertEqual(json.loads(response.read()), {"status": "ready", "polishModel": MODEL})
        with patch.dict("os.environ", environment, clear=True):
            receipt = generate("polish_explanation", {"sentences": [HARD]}, report_timeout=True)
        self.assertEqual(receipt["output"], HARD_GOOD)
        self.assertEqual(receipt["modelVersion"], MODEL)

    def test_http_without_polish_model_is_service_unavailable(self):
        import urllib.request
        model, _ = self.model(loaded=False)
        server, environment = self.serve(model)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(environment["DESIGN_MODEL_URL"] + "/health", timeout=10) as response:
            self.assertEqual(json.loads(response.read()), {"status": "ready", "polishModel": None})
        with patch.dict("os.environ", environment, clear=True), \
                self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$"):
            generate("polish_explanation", {"sentences": [HARD]}, report_timeout=True)

    def test_local_model_without_polish_maps_to_service_unavailable(self):
        model, _ = self.model(loaded=False)
        generation_client.set_local_model(model)
        self.addCleanup(generation_client.set_local_model, None)
        with self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$"):
            generate("polish_explanation", {"sentences": [HARD]}, report_timeout=True)
        with self.assertRaisesRegex(ValueError, "^generation_service_unavailable$"):
            explanation.explain(job())

    def test_model_id_and_disabled_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text("{}")
            (root / "sub").mkdir()
            (root / "sub/weights.bin").write_bytes(b"\x00\x01")
            first = polish_model_id(root, "Qwen/Qwen3.5-2B")
            self.assertRegex(first, r"^Qwen/Qwen3\.5-2B@[0-9a-f]{8}$")
            self.assertEqual(first, polish_model_id(root, "Qwen/Qwen3.5-2B"))
            (root / "config.json").write_text('{"x":1}')
            self.assertNotEqual(first, polish_model_id(root, "Qwen/Qwen3.5-2B"))
            # 내려받기·버전 관리 부산물은 hash에 들어가지 않는다.
            current = polish_model_id(root, "Qwen/Qwen3.5-2B")
            (root / ".cache/huggingface").mkdir(parents=True)
            (root / ".cache/huggingface/download.lock").write_text("x")
            (root / ".gitattributes").write_text("*.bin filter=lfs")
            (root / ".git").mkdir()
            (root / ".git/HEAD").write_text("ref")
            (root / "weights.bin.lock").write_text("")
            (root / "part.incomplete").write_text("")
            self.assertEqual(current, polish_model_id(root, "Qwen/Qwen3.5-2B"))
            (root / "tokenizer.json").write_text("{}")
            self.assertNotEqual(current, polish_model_id(root, "Qwen/Qwen3.5-2B"))
            empty = root / "empty"
            empty.mkdir()
            (empty / ".gitattributes").write_text("x")
            with self.assertRaisesRegex(ValueError, "^polish_model_files_missing$"):
                polish_model_id(empty, "m")
            model = DesignModel.__new__(DesignModel)
            with self.assertLogs("generation_service", "ERROR") as logs:
                model.attach_polish(root, None)
            self.assertIsNone(model.polish)
            self.assertIsNone(model.polish_model_id)
            self.assertIn("polish_disabled: polish_model_name_missing", logs.output[0])
            model.attach_polish(None, None)
            self.assertIsNone(model.polish_error)


if __name__ == "__main__":
    unittest.main()
