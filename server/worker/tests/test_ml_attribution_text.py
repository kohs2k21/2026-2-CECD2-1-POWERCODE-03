"""ML 기여도 설명 문장(ml_attribution_text)과 설명 작업 연결(explanation) 시험. DB·GPU 없이 돈다.

수치는 ML 기여도 계약의 예시(설명용 수치, 실측 아님): 변수 3개 T·E·L, x = (120, 15, 4200), m = (100, 0, 800),
판정선 0.62, higher_is_anomaly, 연합 점수 ∅ 0.40 · T 0.41 · TL 0.51 · TEL 0.71 → φ E 0.175 · L 0.120 · T 0.015.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import explanation
from ml_attribution_text import INVALID, LIMITATION, josa, render

SUMMARY = ("머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 "
           "ERROR_COUNT 56%, 처리 시간 39%, 처리량(TOTAL_COUNT) 5% 순입니다.")
COUNTERFACTUAL = ("ERROR_COUNT(단위 미확인)만 학습 데이터 중앙값(0)으로 바꾸면 원점수가 판정선보다 낮아져(값 0.510) "
                  "머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.")


def player(name, actual, baseline, phi, unit=False):
    return {"playerId": name, "features": [name], "actual": {name: actual}, "baseline": {name: baseline},
            "scaleIsUnit": {name: unit}, "phi": phi, "share": phi / 0.31, "thresholdGapShare": phi / 0.22}


def example():
    return {"version": "baseline-shapley-v1", "status": "succeeded",
            "baselineKind": "train_median_robust_scaler_center", "grouping": "source-field-union-v1",
            "scoreActual": 0.71, "scoreBaseline": 0.40, "threshold": "0.62", "direction": "higher_is_anomaly",
            "baselineWarning": False, "efficiencyError": 0.0,
            "players": [player("total_count", "120", 100.0, 0.015), player("error_count", "15", 0.0, 0.175),
                        player("elapsed_ms", "4200", 800.0, 0.12)],
            "topK": ["error_count", "elapsed_ms", "total_count"],
            "counterfactual": [
                {"replaced": ["error_count"], "scoreAfter": 0.51, "warningAfterReplacement": False},
                {"replaced": ["error_count", "elapsed_ms"], "scoreAfter": 0.41, "warningAfterReplacement": False},
                {"replaced": ["error_count", "elapsed_ms", "total_count"], "scoreAfter": 0.40,
                 "warningAfterReplacement": False}],
            "clearedAtK": 1, "pins": {}, "computeMs": 1.2}


def keep(attribution, k, cleared=None, scores=None):
    """topK·반사실을 앞 k개로 줄인다."""
    attribution["topK"] = attribution["topK"][:k]
    attribution["counterfactual"] = attribution["counterfactual"][:k]
    for item, score in zip(attribution["counterfactual"], scores or ()):
        item["scoreAfter"] = score
    attribution["clearedAtK"] = cleared
    return attribution


class SentenceTests(unittest.TestCase):
    def test_contract_example(self):
        text = render(example())
        self.assertEqual(text["summary"], SUMMARY)
        self.assertEqual(text["counterfactual"], COUNTERFACTUAL)
        self.assertEqual(text["notes"], [])
        self.assertEqual(text["limitation"], LIMITATION)
        self.assertEqual([item["playerId"] for item in text["topK"]], ["error_count", "elapsed_ms", "total_count"])
        self.assertEqual(text["topK"][0]["phi"], 0.175)
        self.assertEqual(text["counterfactualScores"], [0.51, 0.41, 0.40])

    def test_fewer_than_three_and_still_anomalous(self):
        text = render(keep(example(), 2))
        self.assertEqual(text["summary"], "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 "
                                          "ERROR_COUNT 56%, 처리 시간 39% 순입니다.")
        self.assertEqual(text["counterfactual"], "상위 2개 변수를 학습 데이터 중앙값으로 바꿔도 원점수가 판정선 기준 "
                                                 "이상 범위에 남습니다(값 0.410).")

    def test_single_positive_player(self):
        text = render(keep(example(), 1, cleared=1))
        self.assertEqual(text["summary"], "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 "
                                          "ERROR_COUNT뿐입니다.")
        self.assertNotIn("100%", text["summary"])

    def test_no_positive_player(self):
        text = render(keep(example(), 0))
        self.assertEqual(text["summary"], "원점수를 이상 쪽으로 움직인 변수를 찾지 못했습니다.")
        self.assertIsNone(text["counterfactual"])

    def test_share_null_lists_names_only(self):
        attribution = example()
        for item in attribution["players"]:
            item["share"] = None
        self.assertEqual(render(attribution)["summary"],
                         "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT, 처리 시간, "
                         "처리량(TOTAL_COUNT) 순입니다. 학습 데이터 중앙값과 원점수 차이가 거의 없어 비율은 계산하지 않았습니다.")

    def test_negative_phi_note(self):
        attribution = keep(example(), 2, cleared=1)
        attribution["players"][0]["phi"] = -0.02
        self.assertEqual(render(attribution)["notes"],
                         ["처리량(TOTAL_COUNT)은 원점수를 정상 쪽으로 움직였습니다. 그래서 다른 변수 비율 합이 100%를 넘을 수 있습니다."])

    def test_cleared_at_two_with_josa(self):
        text = render(keep(example(), 2, cleared=2, scores=[0.65, 0.41]))
        self.assertEqual(text["counterfactual"],
                         "ERROR_COUNT·처리 시간을 함께 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.410) "
                         "머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다. ERROR_COUNT만 바꾸면 원점수는 0.650으로 "
                         "여전히 판정선 기준 이상 범위에 남습니다.")
        text = render(keep(example(), 2, cleared=2, scores=[0.651, 0.41]))
        self.assertIn("원점수는 0.651로 여전히", text["counterfactual"])

    def test_grouped_members_with_scale_one(self):
        group = {"playerId": "group:total_count", "features": ["total_count", "error_count", "error_ratio"],
                 "actual": {"total_count": "120", "error_count": "15", "error_ratio": "0.125"},
                 "baseline": {"total_count": 100.0, "error_count": 0.0, "error_ratio": 0.0},
                 "scaleIsUnit": {"total_count": True, "error_count": False, "error_ratio": False},
                 "phi": 0.19, "share": 0.19 / 0.31, "thresholdGapShare": 0.19 / 0.22}
        attribution = example()
        attribution["players"] = [group, attribution["players"][2]]
        keep(attribution, 2, cleared=1)
        attribution["topK"] = ["group:total_count", "elapsed_ms"]
        text = render(attribution)
        members = ("처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값(처리량(TOTAL_COUNT) 120(단위 미확인) · 평소 값과 다름, "
                   "ERROR_COUNT 15(단위 미확인) · 중앙값 0, 오류 비율 값 0.125 · 중앙값 0)")
        self.assertEqual(text["summary"], "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 "
                                          f"{members} 61%, 처리 시간 39% 순입니다.")
        self.assertEqual(text["counterfactual"], f"{members}만 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져"
                                                 "(값 0.510) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.")
        self.assertEqual(text["notes"], ["처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값은 같은 원천 값을 쓰는 변수라 "
                                         "한 묶음으로 계산했습니다."])

    def test_single_scale_one_has_no_numbers(self):
        attribution = example()
        attribution["players"][1]["scaleIsUnit"] = {"error_count": True}
        text = render(attribution)
        self.assertIn("변수는 ERROR_COUNT 평소 값과 다름 56%, 처리 시간 39%", text["summary"])
        self.assertTrue(text["counterfactual"].startswith("ERROR_COUNT만 학습 데이터 중앙값으로 바꾸면 원점수가"))
        # scaleIsUnit 키가 없는 기록은 미적용(숫자 그대로).
        attribution["players"][1].pop("scaleIsUnit")
        self.assertEqual(render(attribution)["counterfactual"], COUNTERFACTUAL)

    def test_ms_value_unit_in_counterfactual(self):
        attribution = example()
        attribution["topK"] = ["elapsed_ms", "error_count", "total_count"]
        self.assertTrue(render(attribution)["counterfactual"].startswith("처리 시간만 학습 데이터 중앙값(800ms)으로 바꾸면"))

    def test_lower_is_anomaly_words(self):
        attribution = keep(example(), 1, cleared=1, scores=[-0.51])
        attribution.update(direction="lower_is_anomaly", scoreActual=-0.71, threshold="-0.62")
        text = render(attribution)
        self.assertIn("(값 -0.710, 판정선 -0.620)를 이상 쪽으로 내린 변수는", text["summary"])
        self.assertIn("원점수가 판정선보다 높아져(값 -0.510)", text["counterfactual"])

    def test_threshold_tie_extends_digits(self):
        attribution = keep(example(), 1, cleared=1, scores=[0.61996])
        attribution.update(scoreActual=0.6203, threshold="0.6200")
        text = render(attribution)
        # 셋째 자리 0.620 = 0.620, 넷째 자리 반사실 0.6200 = 0.6200 → 다섯째 자리
        self.assertIn("(값 0.62030, 판정선 0.62000)", text["summary"])
        self.assertIn("(값 0.61996)", text["counterfactual"])
        attribution.update(scoreActual=0.6203)
        attribution["counterfactual"][0]["scoreAfter"] = 0.5
        self.assertIn("(값 0.6203, 판정선 0.6200)", render(attribution)["summary"])

    def test_baseline_warning_note(self):
        attribution = example()
        attribution["baselineWarning"] = True
        self.assertEqual(render(attribution)["notes"],
                         ["학습 데이터 중앙값에서도 원점수가 판정선을 넘어 비율을 해석할 때 주의가 필요합니다."])

    def test_josa(self):
        cases = [("처리 시간", "은/는", "은"), ("ERROR_COUNT", "은/는", "는"), ("처리량(TOTAL_COUNT)", "은/는", "은"),
                 ("오류 비율 값", "을/를", "을"), ("ERROR_COUNT", "을/를", "를"), ("0.650", "으로/로", "으로"),
                 ("0.651", "으로/로", "로"), ("0.657", "으로/로", "로"), ("0.653", "으로/로", "으로"),
                 ("0.652", "으로/로", "로"), ("10", "으로/로", "으로"), ("-0.510", "으로/로", "으로")]
        for word, pair, expected in cases:
            with self.subTest(word=word, pair=pair):
                self.assertEqual(josa(word, pair), expected)

    def test_invalid_attribution_is_rejected(self):
        broken = []
        for change in (lambda a: a.update(direction="sideways"), lambda a: a.update(topK=["missing"]),
                       lambda a: a["players"][0].update(phi=float("nan")), lambda a: a.pop("players"),
                       lambda a: a["players"][1]["actual"].pop("error_count")):
            attribution = example()
            change(attribution)
            broken.append(attribution)
        for attribution in broken:
            with self.subTest(attribution=attribution), self.assertRaisesRegex(ValueError, f"^{INVALID}$"):
                render(attribution)


class ExplanationJobTests(unittest.TestCase):
    """설명 작업: 기여도 키가 없으면 이전과 같고, 있으면 사실 하나·contributionStatus만 더한다."""

    def job(self, evidence):
        text = json.dumps(evidence)
        return {"input": {"savedEvidenceJson": text, "savedEvidence": evidence, "decisionId": "d"},
                "llmSpec": {"input_evidence_hash": hashlib.sha256(text.encode()).hexdigest()}}

    def run_job(self, evidence, picked=("fact_0",)):
        receipt = {"truncated": False, "output": {"factIds": list(picked), "nextChecks": ["review_source_operation"]}}
        with patch.object(explanation, "generate", return_value=receipt) as generate:
            result = explanation.explain(self.job(evidence))
        return result, generate.call_args.args

    def saved(self, *attributions):
        methods = [{"methodId": "rule:x", "category": "soft", "anomalous": False}]
        methods += [{"methodId": f"ml:m{i}", "category": "ml", "anomalous": True, "attribution": a}
                    for i, a in enumerate(attributions)]
        return {"decisionId": "d", "status": "complete", "methodResults": methods}

    def test_without_attribution_is_unchanged(self):
        for evidence in ({"decisionId": "d", "rules": [], "models": [{"modelKey": "m", "anomalous": True}]},
                         {"decisionId": "d", "methodResults": [{"methodId": "ml:m", "category": "ml"}]}):
            with self.subTest(evidence=evidence):
                result, args = self.run_job(evidence)
                facts = explanation.facts_for(evidence)
                self.assertEqual(args, ("explain", {"facts": facts, "allowedChecks": list(explanation.CHECKS)}))
                expected = {"decisionId": "d", "facts": facts[:1],
                            "nextChecks": [{"code": "review_source_operation",
                                            "text": explanation.CHECKS["review_source_operation"]}],
                            "causeStatus": "unconfirmed", "contributionStatus": "not_computed"}
                self.assertEqual(json.dumps(result, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))

    def test_succeeded_attribution_adds_required_fact(self):
        evidence = self.saved(example())
        result, args = self.run_job(evidence)
        sent = args[1]["facts"]
        self.assertEqual(sent[:-1], explanation.facts_for(evidence))
        self.assertEqual((sent[-1]["id"], sent[-1]["name"]), ("fact_3", "ml_attribution"))
        self.assertEqual(result["contributionStatus"], "computed")
        # 모델이 fact_0만 골라도 코드가 만든 기여도 사실은 결과에 들어간다.
        self.assertEqual([fact["id"] for fact in result["facts"]], ["fact_0", "fact_3"])
        value = result["facts"][1]["value"]
        self.assertEqual([(v["methodId"], v["summary"], v["counterfactual"], v["limitation"]) for v in value],
                         [("ml:m0", SUMMARY, COUNTERFACTUAL, LIMITATION)])
        # 모델이 그 사실을 골라도 한 번만.
        result, _ = self.run_job(evidence, picked=("fact_3", "fact_0"))
        self.assertEqual([fact["id"] for fact in result["facts"]], ["fact_0", "fact_3"])

    def test_skipped_and_failed_status_without_fact(self):
        skipped = {"version": "baseline-shapley-v1", "status": "skipped", "reason": "not_anomalous"}
        failed = {"version": "baseline-shapley-v1", "status": "failed", "failureCode": "exception", "computeMs": 0.1}
        for attributions, status in (((skipped,), "skipped"), ((failed,), "failed"), ((skipped, failed), "failed"),
                                     ((skipped, example()), "computed")):
            with self.subTest(status=status):
                evidence = self.saved(*attributions)
                result, args = self.run_job(evidence)
                self.assertEqual(result["contributionStatus"], status)
                extra = len(args[1]["facts"]) - len(explanation.facts_for(evidence))
                self.assertEqual(extra, 1 if status == "computed" else 0)

    def test_invalid_attribution_fails_the_job(self):
        broken = copy.deepcopy(example())
        broken["direction"] = None
        for attribution in ({"status": "unknown"}, "succeeded", broken):
            with self.subTest(attribution=attribution), self.assertRaisesRegex(ValueError, f"^{INVALID}$"):
                self.run_job(self.saved(attribution))


if __name__ == "__main__":
    unittest.main()
