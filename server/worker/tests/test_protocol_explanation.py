"""프로토콜 경로 설명(protocol_explanation.py) 단위 시험 — 사실 만들기·프롬프트·자동 점검. DB·모델·네트워크 없음."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol_explanation as pe  # noqa: E402


def draft(interface="IF_X", paths=(), *, status="draft", excluded=None, omitted=None, branching=0):
    total = sum(count for _, count in paths)
    return {"interfaceId": interface, "status": status, "transactions": total + sum((excluded or {}).values()),
            "pathTransactions": total, "branchingTransactions": branching, "distinctPaths": len(paths),
            "excludedReasons": excluded or {},
            "paths": [{"channels": list(channels), "transactions": count, "ratio": round(count / total, 6),
                       "rare": count * 100 < total, "fromBranching": 0} for channels, count in paths],
            "omittedPaths": omitted or {"paths": 0, "transactions": 0}}


def rule(interface="IF_X", paths=(), enabled=True):
    return {"id": "hard:proto-0000abcd", "interfaceId": interface, "enabled": enabled, "severity": "critical",
            "paths": [{"channels": list(channels)} for channels in paths]}


PARAMS = {"minTransactions": 30, "rarePercent": 1}


def sample_facts():
    facts, _ = pe.observed_paths_facts(
        "IF_X", draft("IF_X", [(("A", "B", "C"), 1234), (("A", "C"), 30)]), rule("IF_X", [("A", "B", "C")]),
        period={"from": "2026-01-05", "until": "2026-01-19"}, parameters=PARAMS)
    return facts


def out(explanation, points=()):
    return json.dumps({"explanation": explanation, "reviewPoints": list(points)}, ensure_ascii=False)


class FactsTests(unittest.TestCase):
    def test_percent_text_matches_screen_rule(self):
        self.assertEqual(pe.percent_text(0, 0, 10), "0")
        self.assertEqual(pe.percent_text(1, 10, 10), "100")
        self.assertEqual(pe.percent_text(0.666667, 2, 3), "66.6")  # 한 자리 내림
        self.assertEqual(pe.percent_text(0.0001, 1, 10000), "0.1")  # 0 초과는 0.1 이상
        self.assertEqual(pe.percent_text(0.99995, 19999, 20000), "99.9")  # 1 미만은 99.9 이하
        self.assertEqual(pe.percent_text(None, 1, 3), "33.3")

    def test_comparison_uses_exact_channel_arrays(self):
        facts, mapping = pe.observed_paths_facts(
            "IF_X", draft("IF_X", [(("A", "B"), 90), (("A", "B", "B"), 6), (("B", "A"), 4)]),
            rule("IF_X", [("A", "B"), ("A", "D", "B")]), parameters=PARAMS)
        paths = facts["paths"]
        by_channels = {tuple(chain): pid for pid, chain in paths.items()}
        self.assertEqual(facts["interface"], "IF-01")
        self.assertEqual(mapping["interfaces"], {"IF-01": "IF_X"})
        flags = {item["path"]: item["inAllowed"] for item in facts["observed"]["paths"]}
        self.assertTrue(flags[by_channels[("CH-01", "CH-02")]])
        self.assertFalse(flags[by_channels[("CH-01", "CH-02", "CH-02")]])  # 연속 같은 채널은 다른 경로
        self.assertFalse(flags[by_channels[("CH-02", "CH-01")]])  # 순서가 다르면 다른 경로
        self.assertEqual(facts["comparison"]["notAllowedCount"], 2)
        self.assertEqual(facts["comparison"]["allowedNotInObservedList"], [by_channels[("CH-01", "CH-03", "CH-02")]])
        self.assertNotIn('"A"', json.dumps(facts))  # 실제 채널 값은 사실에 없음

    def test_unregistered_and_not_observed(self):
        facts, _ = pe.observed_paths_facts("IF_X", draft("IF_X", [(("A",), 5)]), None, parameters=PARAMS)
        self.assertFalse(facts["registered"])
        self.assertIsNone(facts["allowed"])
        self.assertIsNone(facts["comparison"])
        self.assertIsNone(facts["observed"]["paths"][0]["inAllowed"])
        facts, _ = pe.observed_paths_facts("IF_X", None, rule("IF_X", [("A", "B")]))
        self.assertEqual(facts["observed"], {"available": False})
        self.assertTrue(facts["comparison"]["observedListComplete"])
        with self.assertRaises(ValueError):
            pe.observed_paths_facts("IF_X", draft("IF_Y", [(("A",), 5)]), None)
        with self.assertRaises(ValueError):
            pe.observed_paths_facts("IF_X", None, rule("IF_Y", [("A",)]))

    def test_cut_keeps_not_allowed_paths_first(self):
        many = [((f"C{i:02d}", "END"), 100 - i) for i in range(30)]
        facts, _ = pe.observed_paths_facts("IF_X", draft("IF_X", many), rule("IF_X", [m[0] for m in many[:25]]))
        shown = facts["observed"]["paths"]
        self.assertEqual(len(shown), pe.MAX_FACT_PATHS)
        self.assertEqual(sum(item["inAllowed"] is False for item in shown), 5)  # 허용 밖 5개는 모두 실림
        self.assertEqual(facts["observed"]["omitted"]["paths"], 10)
        self.assertEqual(facts["comparison"]["notAllowedCount"], 5)

    def test_alias_off_keeps_real_ids(self):
        facts, mapping = pe.observed_paths_facts("IF_X", draft("IF_X", [(("A", "B"), 5)]), None, alias=False)
        self.assertEqual(facts["interface"], "IF_X")
        self.assertEqual(facts["paths"]["P1"], ["A", "B"])
        self.assertEqual(mapping["channels"], {})

    def test_alert_facts(self):
        review = {"version": 1, "kind": "path_unmatched", "interfaceId": "IF_X",
                  "routes": [{"channels": ["A", "B"], "matched": True}, {"channels": ["A", "C"], "matched": False}],
                  "routesReason": None}
        facts, _ = pe.alert_facts(review, rule("IF_X", [("A", "B")]))
        self.assertEqual((facts["routeCount"], facts["unmatchedCount"], facts["branching"]), (2, 1, True))
        self.assertEqual(facts["routes"][0]["path"], facts["allowed"]["paths"][0])
        with self.assertRaises(ValueError):
            pe.alert_facts(review, None)  # 경로 불일치 알림은 허용 경로가 있어야 설명할 수 있다
        unregistered = {"version": 1, "kind": "interface_unregistered", "interfaceId": "IF_X", "routes": [],
                        "routesReason": "chain_broken"}
        facts, _ = pe.alert_facts(unregistered, rule("IF_X", [("A",)]))
        self.assertIsNone(facts["allowed"])
        self.assertEqual(facts["routesReason"], {"reason": "chain_broken", "label": "선행 사슬 끊김"})

    def test_messages_carry_facts_only_as_data(self):
        facts = sample_facts()
        messages = pe.build_messages(facts)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(json.loads(messages[1]["content"].split("사실(JSON):\n", 1)[1]), facts)
        self.assertIn("지시가 아니다", messages[0]["content"])


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.facts = sample_facts()
        self.p2 = next(pid for pid, chain in self.facts["paths"].items() if chain == ["CH-01", "CH-03"])

    def check(self, text):
        return pe.check_output(text, self.facts)

    def test_template_and_grounded_text_pass(self):
        self.assertTrue(self.check(pe.template_output(self.facts))["passed"])
        result = self.check(out(f"인터페이스 IF-01의 학습 기간 거래는 1,234건 이상 관측되었고 경로 {self.p2}(CH-01 → CH-03)는 "
                                "허용 경로에 없습니다. 비율은 2.3%입니다.", ["CH-02 → CH-03 구간이 있는 경로는 허용 경로입니다."]))
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["coverage"]["mentions_not_allowed_path"])

    def test_invented_number_detected(self):
        for text, number in (("허용 경로에 없는 경로가 약 3%입니다.", "3"), ("관측 경로는 여덟 개입니다.", "8"),
                             ("거래는 1,265건입니다.", "1265")):
            with self.subTest(text=text):
                result = self.check(out(text))
                self.assertIn("numbers_subset", result["failures"])
                self.assertEqual(result["details"]["unknown_numbers"], [number])

    def test_identifier_digits_are_not_numbers(self):
        result = self.check(out("인터페이스 IF-01에서 CH-03 채널을 거치는 경로가 있습니다."))
        self.assertTrue(result["checks"]["numbers_subset"], result["details"])

    def test_invented_identifier_and_path_detected(self):
        result = self.check(out("인터페이스 IF-02에서 CH-09 채널이 관측되었습니다."))
        self.assertEqual(result["details"]["unknown_identifiers"], ["CH-09", "IF-02"])
        result = self.check(out("CH-01 → CH-02 → CH-04 경로가 관측되었습니다."))
        self.assertIn("paths_in_facts", result["failures"])
        self.assertIn("identifiers_subset", result["failures"])
        result = self.check(out("CH-03 → CH-01 경로가 관측되었습니다."))  # 있는 채널이지만 사실에 없는 순서
        self.assertEqual(result["failures"], ["paths_in_facts"])

    def test_forbidden_expressions(self):
        for text in ("이 경로는 설정 변경 때문에 생겼습니다.", "배포로 인해 경로가 바뀐 것으로 보입니다.",
                     "새 경로가 생긴 원인은 알 수 없습니다.", "이 경로를 반드시 등록해야 합니다."):
            with self.subTest(text=text):
                self.assertIn("no_forbidden", self.check(out(text))["failures"])

    def test_format_language_and_length(self):
        self.assertEqual(self.check("설명입니다")["failures"][0], "json_parsed")
        fenced = "```json\n" + out("관측 경로는 2개입니다.") + "\n```"
        result = self.check(fenced)
        self.assertTrue(result["passed"])
        self.assertFalse(result["checks"]["json_strict"])
        extra = json.dumps({"explanation": "관측 경로는 2개입니다.", "reviewPoints": [], "verdict": "anomalous"})
        self.assertIn("schema_ok", self.check(extra)["failures"])
        self.assertIn("korean_ok", self.check(out("Observed paths were aggregated by code."))["failures"])
        self.assertIn("length_ok", self.check(out("가" * (pe.EXPLANATION_MAX + 1)))["failures"])
        self.assertIn("length_ok", self.check(out("확인할 점입니다.", ["확인 사항입니다."] * 4))["failures"])
        self.assertIn("length_ok", self.check(out(" "))["failures"])


if __name__ == "__main__":
    unittest.main()
