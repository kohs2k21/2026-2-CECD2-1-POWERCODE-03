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
HARD = "이상 규칙이 처리 상태 S와 ERROR_COUNT를 함께 확인했고, 성공 상태인데 ERROR_COUNT가 0보다 커서 이상으로 판단했습니다."
HARD_GOOD = HARD.replace("규칙이", "규칙은")
MODEL = "Qwen/Qwen3.5-2B@0123abcd"


def spec(sentences=(HARD,), value_tokens=(), **extra):
    sentences = list(sentences)
    value = {"kind": "explain", "sourceId": "src", "decisionId": "dec", "configVersionId": "cfg",
             "templateVersion": "evidence-template-v1", "sentences": sentences,
             "stateWords": ep.Extracted(" ".join(sentences), list(value_tokens)).states, "valueTokens": list(value_tokens)}
    value.update(extra)
    return value


def llm_spec(**extra):
    return {"evidence_version": "summary-sentences-v1", "prompt_version": "evidence-polish-v1",
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
        plan = ep.parse_input(spec([HARD, "일부 검사(관계 규칙)는 결과를 내지 못해 평가 불가입니다."]))
        self.assertEqual(plan["codeText"], HARD + " 일부 검사(관계 규칙)는 결과를 내지 못해 평가 불가입니다.")

    def test_shape_and_limits(self):
        base = spec()
        for key in ("kind", "sourceId", "decisionId", "configVersionId", "templateVersion", "sentences", "stateWords",
                    "valueTokens"):
            with self.subTest(missing=key):
                self.rejected({k: v for k, v in base.items() if k != key})
        self.rejected({**base, "savedEvidence": {}})
        self.rejected({**base, "kind": "explain_protocol"})
        self.rejected({**base, "decisionId": ""})
        self.rejected(spec([]))
        self.rejected(spec(["가."] * 9))
        self.rejected(spec(["가" * 301]))
        self.rejected(spec(["가" * 300] * 4 + ["나."]))
        self.rejected(spec(["  "]))
        self.rejected(spec(["첫 줄.\n둘째 줄."]))
        self.rejected(spec(["자리표  문자."]))
        ep.parse_input(spec(["가" * 300] * 4))

    def test_value_tokens(self):
        sentence = "이상 규칙이 이번 처리 상태 F를 성공 상태 S와 비교했고, 달라 이상으로 판단했습니다."
        ep.parse_input(spec([sentence], ["F"]))
        self.rejected(spec([sentence], ["P"]))
        self.rejected(spec([sentence], [""]))
        self.rejected(spec([sentence], ["가" * 121]))
        self.rejected(spec([sentence], ["F"] * 17))
        self.rejected({**spec([sentence]), "valueTokens": "F"})

    def test_state_words_must_match_worker_extraction(self):
        self.rejected({**spec(), "stateWords": []})
        self.rejected({**spec(), "stateWords": ["이상", "이상"]})
        self.rejected({**spec(), "stateWords": ["정상"]})

    def test_allowed_numbers_multiset(self):
        sentences = ["머신러닝 모델이 이번 처리 시간 4,200 ms를 평소 값 800 ms와 비교했고, 더 길어 판단에 가장 크게 영향을 주었습니다(영향 62%)."]
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
                                  "templateVersion": "evidence-template-v1", "codeText": HARD,
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

    def receipt(self, output=HARD_GOOD, truncated=False, model=MODEL, prompt="evidence-polish-v1"):
        return {"output": output, "truncated": truncated, "modelVersion": model, "promptVersion": prompt}

    def test_passed_polish(self):
        result, calls = self.run_job(self.receipt())
        self.assertEqual(calls, [("polish_explanation", {"sentences": [HARD]}, {"report_timeout": True})])
        self.assertEqual(result["paragraph"], {"text": HARD_GOOD, "source": "llm"})
        self.assertEqual(result["check"], {"status": "passed", "code": None})
        self.assertEqual(result["modelVersion"], MODEL)
        self.assertEqual(result["outputSchemaVersion"], "evidence-polish-result-v1")

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
                            (ValueError("generation_token_budget_exceeded"), "generation_contract_rejected")):
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
                                   "promptVersion": "evidence-polish-v1"})
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
            empty = root / "empty"
            empty.mkdir()
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
