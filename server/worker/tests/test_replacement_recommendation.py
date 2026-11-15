"""모델 교체 추천 v1(`replacement-recommendation-v1`) 순수 계산 시험. DB 없음.

계약: config-comparison-evaluation.md 결과 절 `replacementRecommendations[]` 규칙·"교체 추천 v1 대응(11/15)" 절.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer import replacement_recommendation as rr

ACTIVE = "99999999-9999-4999-8999-999999999999"
C1 = "33333333-3333-4333-8333-333333333333"
C2 = "44444444-4444-4444-8444-444444444444"
C3 = "55555555-5555-4555-8555-555555555555"
SET = "22222222-2222-4222-8222-222222222222"


def label_rows(anomaly, normal, unlabeled=0):
    """행 id → 라벨. 이상 행 먼저, 정상 행, 라벨 없는 행 순."""
    labels = {}
    for index in range(anomaly + normal + unlabeled):
        labels[f"r{index:04d}"] = "anomaly" if index < anomaly else "normal" if index < anomaly + normal else None
    return labels


def predict(labels, tp=0, fp=0, drop=()):
    """이상 라벨 앞 tp행·정상 라벨 앞 fp행을 이상으로 판정. drop = 판정이 없는 행(ML 미판정·평가 제외)."""
    result, seen = {}, {"anomaly": 0, "normal": 0}
    for key, label in labels.items():
        if key in drop:
            continue
        positive = False
        if label == "anomaly":
            positive = seen["anomaly"] < tp
            seen["anomaly"] += 1
        elif label == "normal":
            positive = seen["normal"] < fp
            seen["normal"] += 1
        result[key] = positive
    return result


def item(config, role="candidate", status="succeeded", *, applicability="valid", error=None, p95=10.0,
         model_key="iforest", composite=None, unavailable=0, predicted=None, excluded=0):
    if status != "succeeded":
        return {"configVersionId": config, "modelKey": model_key, "role": role, "status": status,
                "errorCode": error, "evaluationId": None}
    return {"configVersionId": config, "modelKey": model_key, "role": role, "status": "succeeded", "errorCode": None,
            "evaluationId": f"eval-{config[:4]}", "applicability": applicability,
            "counts": {"excludedCount": excluded},
            "categoryResults": {"ml": {"unavailableCount": unavailable, "predictedAnomalyCount": predicted}},
            "composite": composite,
            "executionCost": {"metrics": {"itemP95Ms": {"value": p95, "unit": "ms"}}}}


def run(items, predictions, labels, *, baselines=None, purpose="validation", deadline=3000, versions=None,
        pairwise=()):
    baselines = [{"modelKey": "iforest", "configVersionId": ACTIVE}] if baselines is None else baselines
    return rr.recommend(comparison_id="cmp", job_id="job", evaluation_set={"id": SET, "purpose": purpose},
                        baselines=baselines, active_revision=7, items=items, predictions=predictions, labels=labels,
                        pairwise=pairwise, deadline_ms=deadline, versions=versions or {},
                        generated_at="2026-11-15T00:00:00.000Z")


def one(*args, **kwargs):
    [element] = run(*args, **kwargs)
    return element


def outcomes(element):
    return [(entry["configVersionId"], entry["outcome"], entry["excludedReason"], entry["lostAt"])
            for entry in element["candidates"]]


LABELED = label_rows(12, 20, 5)


class ReasonOrderTests(unittest.TestCase):
    """추천 없음 사유 9종 각각 도달, 먼저 걸리는 순서(활성 단계 → 후보 단계)."""

    def test_no_model_key_gives_no_element(self):
        items = [item(C1, status="failed", error="config_not_found", model_key=None)]
        self.assertEqual(run(items, {}, LABELED, baselines=[]), [])

    def test_active_missing(self):
        element = one([item(C1)], {C1: predict(LABELED, tp=5)}, LABELED, baselines=[])
        self.assertEqual((element["noRecommendationReason"], element["decision"], element["baselineConfigVersionId"],
                          element["baselineEvaluationId"], element["baselineActiveRevision"]),
                         ("active_missing", "not_applicable", None, None, 7))
        self.assertEqual(element["summary"], "현재 운영 중인 버전이 없어 비교 기준이 없습니다. 운영 버전은 직접 선택해 적용하세요.")
        # 비교는 멈췄지만 후보 자격은 채운다(후보 탓이 아님).
        self.assertEqual(outcomes(element), [(C1, "eligible_not_compared", None, None)])

    def test_active_item_failed_names_the_error(self):
        items = [item(ACTIVE, "active", "failed", error="training_evaluation_overlap"), item(C1)]
        element = one(items, {C1: predict(LABELED, tp=5)}, LABELED)
        self.assertEqual((element["noRecommendationReason"], element["decision"], element["baselineEvaluationId"]),
                         ("active_item_failed", "insufficient_evidence", None))
        self.assertEqual(element["summary"], "현재 운영 버전의 평가가 실패해 비교할 수 없습니다(사유: training_evaluation_overlap).")

    def test_active_results_unavailable_means_no_active_decided_rows(self):
        items = [item(ACTIVE, "active"), item(C1)]
        element = one(items, {ACTIVE: {}, C1: predict(LABELED, tp=5)}, LABELED)
        self.assertEqual((element["noRecommendationReason"], element["decision"]),
                         ("active_results_unavailable", "insufficient_evidence"))
        self.assertEqual(element["summary"], rr.NO_RECOMMENDATION_SUMMARY["active_results_unavailable"])

    def test_candidate_reasons_are_not_hidden_behind_active_results_unavailable(self):
        active = {ACTIVE: predict(LABELED, tp=5)}
        # 후보 없음
        element = one([item(ACTIVE, "active")], active, LABELED)
        self.assertEqual((element["noRecommendationReason"], element["decision"], element["candidates"]),
                         ("no_candidates", "not_applicable", []))
        # 후보 전부 실패
        element = one([item(ACTIVE, "active"), item(C1, status="failed", error="training_evaluation_overlap"),
                       item(C2, status="failed", error="config_not_found", model_key=None)], active, LABELED)
        self.assertEqual((element["noRecommendationReason"], element["decision"]),
                         ("all_candidates_failed", "insufficient_evidence"))
        self.assertEqual(outcomes(element), [(C1, "excluded", "item_failed:training_evaluation_overlap", None),
                                             (C2, "excluded", "item_failed:config_not_found", None)])
        # 후보 전부 자격 제외(판정 행이 없어 교집합을 비우는 후보 포함)
        element = one([item(ACTIVE, "active"), item(C1, applicability="unmeasured"), item(C2),
                       item(C3, status="failed", error="evaluation_failed:model_unavailable")],
                      {**active, C1: predict(LABELED), C2: {}}, LABELED)
        self.assertEqual((element["noRecommendationReason"], element["decision"], element["comparedRowCount"]),
                         ("all_candidates_ineligible", "insufficient_evidence", None))
        self.assertEqual(outcomes(element), [(C1, "excluded", "applicability_unmeasured", None),
                                             (C2, "excluded", "common_rows_empty", None),
                                             (C3, "excluded", "item_failed:evaluation_failed:model_unavailable", None)])
        self.assertEqual(element["summary"], "실행 가능하고 판정 불가 행이 없는 후보가 없습니다.")

    def test_failed_candidate_of_another_model_key_is_dropped(self):
        # R18: 다른 modelKey 후보는 이 원소의 후보 목록에 넣지 않는다 — 그 후보뿐이면 no_candidates.
        items = [item(ACTIVE, "active"), item(C1, status="failed", error="method_not_executable:ml:eif", model_key="eif")]
        element = one(items, {ACTIVE: predict(LABELED, tp=5)}, LABELED)
        self.assertEqual((element["noRecommendationReason"], element["decision"], element["candidates"]),
                         ("no_candidates", "not_applicable", []))

    def test_set_purpose_other_than_validation_excludes_candidates(self):
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(LABELED, tp=5), C1: predict(LABELED, tp=9)}, LABELED, purpose="test")
        self.assertEqual(element["noRecommendationReason"], "all_candidates_ineligible")
        self.assertEqual(outcomes(element), [(C1, "excluded", "evaluation_set_not_validation", None)])

    def test_decision_is_mapped_from_every_reason(self):
        self.assertEqual(set(rr.DECISIONS) - {None}, set(rr.NO_RECOMMENDATION_SUMMARY))
        self.assertEqual({rr.decision_for(code) for code in rr.DECISIONS},
                         {"recommend_replace", "keep_current", "insufficient_evidence", "not_applicable"})
        self.assertEqual([code for code, value in rr.DECISIONS.items() if value == "keep_current"],
                         ["no_candidate_better_than_active", "cost_limit_exceeded"])
        self.assertEqual([code for code, value in rr.DECISIONS.items() if value == "not_applicable"],
                         ["active_missing", "no_candidates"])
        self.assertEqual({code for code, value in rr.DECISIONS.items() if value == "insufficient_evidence"},
                         {"active_item_failed", "active_results_unavailable", "all_candidates_failed",
                          "all_candidates_ineligible", "basis_metrics_unavailable"})
        self.assertEqual([code for code, value in rr.DECISIONS.items() if value == "recommend_replace"], [None])
        # PRD 3절 문구 그대로(버전 표기에 "리비전"·"r<번호>" 없음).
        for text in rr.NO_RECOMMENDATION_SUMMARY.values():
            self.assertNotIn("리비전", text)
            self.assertIsNone(re.search(r"\br\d+", text))


class LabeledBasisTests(unittest.TestCase):
    def test_labeled_win_recommends_with_basis_costs_and_summary(self):
        composite = {"completeOnly": {"recall": {"value": 0.5}, "hitCount": 9}, "decidable": {"recall": {"value": 0.6}}}
        items = [item(ACTIVE, "active", p95=11.3, composite=composite),
                 item(C1, p95=21.3, composite={"completeOnly": {"recall": {"value": 0.75}}, "decidable": {"recall": {"value": 0.8}}})]
        versions = {C1: {"displayName": "Isolation Forest 11/01~11/07", "createdAt": "2026-11-08"}}
        element = one(items, {ACTIVE: predict(LABELED, tp=8, fp=4), C1: predict(LABELED, tp=10, fp=2)}, LABELED,
                      versions=versions)
        self.assertEqual((element["decision"], element["noRecommendationReason"], element["reason"],
                          element["targetVersionId"], element["targetEvaluationId"]),
                         ("recommend_replace", None, "better_than_active_on_all_basis_metrics", C1, "eval-3333"))
        self.assertEqual((element["comparedRowCount"], element["comparedLabelCounts"]),
                         (37, {"anomalyLabelCount": 12, "normalLabelCount": 20}))
        self.assertEqual([(row["metric"], row["target"]["numerator"], row["target"]["denominator"],
                           row["baseline"]["numerator"], row["outcome"], row["comparedTo"]) for row in element["basis"]],
                         [("ml.completeOnly.recall", 10, 12, 8, "better", ACTIVE),
                          ("ml.completeOnly.precision", 10, 12, 8, "better", ACTIVE),
                          ("ml.completeOnly.falsePositiveRate", 2, 20, 4, "better", ACTIVE)])
        self.assertEqual(element["excludedMetrics"], [])
        self.assertEqual(element["checks"], {"itemStatus": "succeeded", "executable": True, "methodUnavailableCount": 0,
                                             "applicability": "valid", "costLimit": "passed"})
        self.assertEqual([(cost["target"], cost["baseline"], cost["limit"], cost["outcome"]) for cost in element["costs"]],
                         [(21.3, 11.3, 50, "within_limit")])
        self.assertEqual(element["references"],
                         [{"metric": "composite.completeOnly.recall", "target": 0.75, "baseline": 0.5, "note": "composite_reference_only"},
                          {"metric": "composite.decidable.recall", "target": 0.8, "baseline": 0.6, "note": "composite_reference_only"}])
        self.assertEqual(outcomes(element), [(C1, "selected", None, None)])
        # R13 틀, 대상은 displayName(v3), 활성은 대체 이름(A). 낮을수록 좋은 오탐률은 `<`.
        self.assertEqual(element["summary"],
                         "Isolation Forest 11/01~11/07이 현재 운영 Isolation Forest 버전 99999999보다 "
                         "모델 재현율 10/12(0.833) > 8/12(0.667), 모델 정밀도 10/12(0.833) > 8/12(0.667), "
                         "모델 오탐률 2/20(0.100) < 4/20(0.200), 지연 P95 21ms(한도 내)")
        self.assertNotIn("리비전", element["summary"])

    def test_labeled_loss_keeps_current(self):
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(LABELED, tp=8, fp=4), C1: predict(LABELED, tp=10, fp=6)}, LABELED)
        self.assertEqual((element["decision"], element["noRecommendationReason"], element["targetVersionId"],
                          element["basis"], element["checks"]),
                         ("keep_current", "no_candidate_better_than_active", None, [], None))
        # 재현율은 낫지만 오탐률이 나쁘다 — 지배 규칙에서 탈락, 첫 열위 지표.
        self.assertEqual(outcomes(element), [(C1, "excluded", "worse_on:ml.completeOnly.precision", None)])
        self.assertEqual(element["summary"], "현재 운영 버전보다 모든 근거 지표에서 같거나 나은 후보가 없습니다.")

    def test_labeled_tie_is_equal_to_active(self):
        same = predict(LABELED, tp=8, fp=4)
        element = one([item(ACTIVE, "active"), item(C1)], {ACTIVE: same, C1: dict(same)}, LABELED)
        self.assertEqual((element["decision"], element["noRecommendationReason"]), ("keep_current", "no_candidate_better_than_active"))
        self.assertEqual(outcomes(element), [(C1, "excluded", "equal_to_active", None)])

    def test_unlabeled_set_is_basis_metrics_unavailable_with_copied_evidence(self):
        labels = label_rows(0, 0, 30)
        pair = {"a": ACTIVE, "b": C1, "comparableCount": 30, "both": 2, "aOnly": 1, "bOnly": 0, "neither": 27}
        items = [item(ACTIVE, "active", p95=11.3, predicted=3, excluded=3, composite={"completeOnly": {"hitCount": 4}}),
                 item(C1, p95=21.3, predicted=2, excluded=3)]
        element = one(items, {ACTIVE: predict(labels), C1: predict(labels)}, labels, pairwise=[pair])
        self.assertEqual((element["decision"], element["noRecommendationReason"], element["comparedRowCount"]),
                         ("insufficient_evidence", "basis_metrics_unavailable", 30))
        self.assertEqual(element["excludedMetrics"],
                         [{"metric": "ml.completeOnly.recall", "reason": "verified_anomaly_labels_unavailable"},
                          {"metric": "ml.completeOnly.precision", "reason": "verified_normal_labels_unavailable"},
                          {"metric": "ml.completeOnly.falsePositiveRate", "reason": "verified_normal_labels_unavailable"}])
        self.assertEqual(outcomes(element), [(C1, "eligible_not_compared", None, None)])
        self.assertEqual(element["summary"], "이 평가 세트에는 정답 라벨이 부족해 정량 비교 근거를 만들 수 없습니다.")
        self.assertEqual(element["unlabeledEvidence"], {"interpretation": "checker_agreement_not_accuracy", "pairs": [{
            "baseline": ACTIVE, "candidate": C1, "comparableCount": 30, "both": 2, "baselineOnly": 1,
            "candidateOnly": 0, "neither": 27, "hitCount": {"baseline": 4, "candidate": None},
            "predictedAnomalyCount": {"baseline": 3, "candidate": 2}, "excludedCount": {"baseline": 3, "candidate": 3},
            "mlUnavailableCount": {"baseline": 0, "candidate": 0}, "itemP95Ms": {"baseline": 11.3, "candidate": 21.3}}]})

    def test_unlabeled_evidence_never_changes_the_decision(self):
        # 라벨 있는 세트: pairwise 값이 달라도 원소는 unlabeledEvidence 복사값만 다르다(판단 불변).
        agree = {"a": ACTIVE, "b": C1, "comparableCount": 37, "both": 10, "aOnly": 0, "bOnly": 0, "neither": 27}
        disagree = {"a": ACTIVE, "b": C1, "comparableCount": 37, "both": 0, "aOnly": 20, "bOnly": 17, "neither": 0}
        cases = (({ACTIVE: predict(LABELED, tp=8, fp=4), C1: predict(LABELED, tp=10, fp=2)}, "recommend_replace"),
                 ({ACTIVE: predict(LABELED, tp=8, fp=4), C1: predict(LABELED, tp=6, fp=2)}, "keep_current"))
        for predictions, decision in cases:
            results = [one([item(ACTIVE, "active"), item(C1)], predictions, LABELED, pairwise=pairwise)
                       for pairwise in ((), [agree], [disagree])]
            self.assertEqual({result["decision"] for result in results}, {decision})
            stripped = [{key: value for key, value in result.items() if key != "unlabeledEvidence"} for result in results]
            self.assertEqual(stripped[0], stripped[1])
            self.assertEqual(stripped[0], stripped[2])
            self.assertEqual([result["unlabeledEvidence"]["pairs"][0]["baselineOnly"] for result in results], [None, 0, 20])

    def test_minimum_samples(self):
        # anomaly 9 < 10: recall만 제외, precision·FPR로 비교.
        labels = label_rows(9, 20)
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(labels, tp=5, fp=4), C1: predict(labels, tp=5, fp=2)}, labels)
        self.assertEqual(element["excludedMetrics"], [{"metric": "ml.completeOnly.recall", "reason": "insufficient_denominator"}])
        self.assertEqual([row["metric"] for row in element["basis"]], ["ml.completeOnly.precision", "ml.completeOnly.falsePositiveRate"])
        self.assertEqual(element["decision"], "recommend_replace")
        # normal 9 < 10(결정 5): precision·FPR 제외, recall만.
        labels = label_rows(12, 9)
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(labels, tp=5, fp=4), C1: predict(labels, tp=6, fp=8)}, labels)
        self.assertEqual(element["excludedMetrics"],
                         [{"metric": "ml.completeOnly.precision", "reason": "insufficient_denominator"},
                          {"metric": "ml.completeOnly.falsePositiveRate", "reason": "insufficient_denominator"}])
        self.assertEqual((element["decision"], [row["metric"] for row in element["basis"]]),
                         ("recommend_replace", ["ml.completeOnly.recall"]))
        # 둘 다 미달 → 근거 지표 없음.
        labels = label_rows(9, 9)
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(labels, tp=5), C1: predict(labels, tp=9)}, labels)
        self.assertEqual((element["noRecommendationReason"], len(element["excludedMetrics"])), ("basis_metrics_unavailable", 3))

    def test_precision_without_predicted_anomalies_is_excluded_for_the_element(self):
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(LABELED, tp=0, fp=0), C1: predict(LABELED, tp=3, fp=0)}, LABELED)
        self.assertIn({"metric": "ml.completeOnly.precision", "reason": "no_predicted_anomalies"}, element["excludedMetrics"])
        self.assertEqual((element["decision"], [row["metric"] for row in element["basis"]]),
                         ("recommend_replace", ["ml.completeOnly.recall", "ml.completeOnly.falsePositiveRate"]))


class CommonIntersectionTests(unittest.TestCase):
    def test_counts_are_recounted_on_the_common_intersection(self):
        # 후보 C1은 이상 행 2개·정상 행 1개를 판정하지 못했다 → 모든 지표를 C 위에서 다시 센다(활성 포함).
        missing = {"r0000", "r0001", "r0012"}
        element = one([item(ACTIVE, "active"), item(C1)],
                      {ACTIVE: predict(LABELED, tp=10, fp=4), C1: predict(LABELED, tp=12, fp=3, drop=missing)}, LABELED)
        self.assertEqual((element["comparedRowCount"], element["comparedLabelCounts"]),
                         (34, {"anomalyLabelCount": 10, "normalLabelCount": 19}))
        recall = element["basis"][0]
        # 활성은 앞 10개 이상 행을 잡았고 그중 2개가 C 밖이라 8/10, 후보는 C 안 이상 행 10개 전부.
        # 정상 행 r0012가 C 밖이라 활성 오탐도 4 → 3으로 다시 센다(후보와 같은 행).
        self.assertEqual((recall["target"]["numerator"], recall["baseline"]["numerator"], recall["target"]["denominator"]),
                         (10, 8, 10))
        fpr = element["basis"][2]
        self.assertEqual((fpr["target"]["numerator"], fpr["baseline"]["numerator"], fpr["outcome"]), (3, 3, "equal"))
        self.assertEqual(element["decision"], "recommend_replace")

    def test_candidate_that_would_empty_the_intersection_is_excluded(self):
        labels = label_rows(12, 20)
        first = [key for index, key in enumerate(labels) if index % 2 == 0]
        second = [key for index, key in enumerate(labels) if index % 2 == 1]
        active = predict(labels, tp=6, fp=2)
        c1 = {key: value for key, value in predict(labels, tp=12, fp=0).items() if key in first}
        c2 = {key: value for key, value in predict(labels, tp=12, fp=0).items() if key in second}
        element = one([item(ACTIVE, "active"), item(C1), item(C2)], {ACTIVE: active, C1: c1, C2: c2}, labels)
        # UUID 순서상 C1이 먼저 C를 정하고, 행이 겹치지 않는 C2는 common_rows_empty.
        self.assertEqual(outcomes(element), [(C1, "selected", None, None), (C2, "excluded", "common_rows_empty", None)])
        self.assertEqual(element["comparedRowCount"], 16)
        self.assertEqual([pair["candidate"] for pair in element["unlabeledEvidence"]["pairs"]], [C1])


class CostTests(unittest.TestCase):
    def test_cost_over_relative_or_deadline_limit_keeps_current(self):
        better = {ACTIVE: predict(LABELED, tp=8, fp=4), C1: predict(LABELED, tp=10, fp=2)}
        # 활성 10ms → 상대 한도 max(50, 20) = 50, 기한 3000 × 0.5 = 1500 → 50.
        element = one([item(ACTIVE, "active", p95=10.0), item(C1, p95=60.0)], better, LABELED)
        self.assertEqual((element["decision"], element["noRecommendationReason"]), ("keep_current", "cost_limit_exceeded"))
        self.assertEqual(outcomes(element), [(C1, "excluded", "cost_limit_exceeded:itemP95Ms", None)])
        self.assertEqual(element["summary"], "정확도가 나은 후보가 있으나 판정 지연 한도를 넘어 추천하지 않습니다. 직접 선택할 수 있습니다.")
        # 활성 100ms → 상대 200, 기한 300 × 0.5 = 150 → 150.
        element = one([item(ACTIVE, "active", p95=100.0), item(C1, p95=160.0)], better, LABELED, deadline=300)
        self.assertEqual(element["noRecommendationReason"], "cost_limit_exceeded")
        element = one([item(ACTIVE, "active", p95=100.0), item(C1, p95=140.0)], better, LABELED, deadline=300)
        self.assertEqual((element["decision"], element["costs"][0]["limit"]), ("recommend_replace", 150.0))
        # 활성 p95·정책 기한 둘 다 없으면 한도 없음 — 통과로 적지 않고 미측정, 추천은 허용, 지연 구절 생략.
        element = one([item(ACTIVE, "active", p95=None), item(C1, p95=20.0)], better, LABELED, deadline=None)
        self.assertEqual((element["decision"], element["checks"]["costLimit"], element["costs"][0]["limit"],
                          element["costs"][0]["outcome"]), ("recommend_replace", "unmeasured", None, "unmeasured"))
        self.assertNotIn("지연 P95", element["summary"])
        # 활성 미측정 → 절대 한도만.
        element = one([item(ACTIVE, "active", p95=None), item(C1, p95=1400.0)], better, LABELED)
        self.assertEqual((element["decision"], element["costs"][0]["limit"]), ("recommend_replace", 1500.0))

    def test_unmeasured_candidate_cost_is_allowed(self):
        element = one([item(ACTIVE, "active"), item(C1, p95=None)],
                      {ACTIVE: predict(LABELED, tp=8, fp=4), C1: predict(LABELED, tp=10, fp=4)}, LABELED)
        self.assertEqual((element["decision"], element["checks"]["costLimit"], element["costs"][0]["outcome"]),
                         ("recommend_replace", "unmeasured", "unmeasured"))
        self.assertNotIn("지연 P95", element["summary"])


class MultiCandidateTests(unittest.TestCase):
    def pick(self, c1, c2, *, p95=(10.0, 10.0), versions=None):
        items = [item(ACTIVE, "active"), item(C1, p95=p95[0]), item(C2, p95=p95[1])]
        return one(items, {ACTIVE: predict(LABELED, tp=6, fp=6), C1: c1, C2: c2}, LABELED, versions=versions)

    def test_lexicographic_steps(self):
        # 1 recall tp
        element = self.pick(predict(LABELED, tp=10, fp=5), predict(LABELED, tp=11, fp=5))
        self.assertEqual(outcomes(element), [(C1, "eligible_not_selected", None, 1), (C2, "selected", None, None)])
        # 2 fp 작은 쪽
        element = self.pick(predict(LABELED, tp=10, fp=2), predict(LABELED, tp=10, fp=3))
        self.assertEqual(outcomes(element), [(C1, "selected", None, None), (C2, "eligible_not_selected", None, 2)])
        # 3 precision 큰 쪽: anomaly 라벨 9 < 10이라 recall이 빠지고 fp가 같아 정밀도로 가른다.
        labels = label_rows(9, 20)
        element = one([item(ACTIVE, "active"), item(C1), item(C2)],
                      {ACTIVE: predict(labels, tp=3, fp=6), C1: predict(labels, tp=5, fp=2),
                       C2: predict(labels, tp=7, fp=2)}, labels)
        self.assertEqual([row["metric"] for row in element["basis"]],
                         ["ml.completeOnly.precision", "ml.completeOnly.falsePositiveRate"])
        self.assertEqual(outcomes(element), [(C1, "eligible_not_selected", None, 3), (C2, "selected", None, None)])
        # 5 itemP95Ms 작은 쪽(1~4 동등)
        same = predict(LABELED, tp=10, fp=2)
        element = self.pick(same, dict(same), p95=(30.0, 20.0))
        self.assertEqual(outcomes(element), [(C1, "eligible_not_selected", None, 5), (C2, "selected", None, None)])
        # 5에서 한쪽 미측정이면 건너뛰고 6 createdAt 빠른 쪽 — 항목 순서와 반대여도
        element = self.pick(same, dict(same), p95=(None, 20.0),
                            versions={C1: {"createdAt": "2026-11-10T00:00:00Z"}, C2: {"createdAt": "2026-11-02T00:00:00Z"}})
        self.assertEqual(outcomes(element), [(C1, "eligible_not_selected", None, 6), (C2, "selected", None, None)])
        self.assertEqual(element["targetVersionId"], C2)
        # DB가 주는 datetime(timestamptz) 값 그대로도 비교한다.
        element = self.pick(same, dict(same), versions={
            C1: {"createdAt": datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)},
            C2: {"createdAt": datetime(2026, 11, 3, 8, 59, 59, 999999, tzinfo=timezone.utc)}})
        self.assertEqual(outcomes(element), [(C1, "eligible_not_selected", None, 6), (C2, "selected", None, None)])
        # 6단계까지 같으면 항목 순서 앞쪽, 뒤는 lostAt 6
        element = self.pick(same, dict(same))
        self.assertEqual(outcomes(element), [(C1, "selected", None, None), (C2, "eligible_not_selected", None, 6)])

    def test_losing_candidates_keep_their_reason_next_to_the_selection(self):
        items = [item(ACTIVE, "active"), item(C1), item(C2, status="failed", error="training_evaluation_overlap"), item(C3)]
        element = one(items, {ACTIVE: predict(LABELED, tp=6, fp=6), C1: predict(LABELED, tp=10, fp=2),
                              C3: predict(LABELED, tp=5, fp=2)}, LABELED)
        self.assertEqual(outcomes(element), [(C1, "selected", None, None),
                                             (C2, "excluded", "item_failed:training_evaluation_overlap", None),
                                             (C3, "excluded", "worse_on:ml.completeOnly.recall", None)])


class ShapeTests(unittest.TestCase):
    def test_version_name_fallback(self):
        self.assertEqual(rr.version_name("3222f105-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "iforest", {}), "Isolation Forest 버전 3222f105")
        self.assertEqual(rr.version_name(C1, "iforest", {C1: {"displayName": "  "}}), "Isolation Forest 버전 33333333")
        self.assertEqual(rr.version_name(C1, "iforest", {C1: {"displayName": "IF 11/01~11/07"}}), "IF 11/01~11/07")
        self.assertEqual(rr.version_name(C1, "unknown_key", {}), "unknown_key 버전 33333333")

    def test_subject_particle_follows_the_last_character(self):
        cases = {"Isolation Forest 버전 3222f105": "가", "Isolation Forest 버전 3222f100": "이", "Isolation Forest 버전 3222105f": "가",
                 "Isolation Forest 버전 33333332": "가", "IF 버전 aaaaaaaL": "이", "IF 11/01~11/07": "이",
                 "아이에프 모델": "이", "아이에프 버전 하나": "가", "버전(가)": "이(가)", "버전 ①": "이(가)", "": "이(가)"}
        for name, particle in cases.items():
            with self.subTest(name=name):
                self.assertEqual(rr.subject_particle(name), particle)
        for digit, particle in zip("0123456789", "이이가이가가이이이가"):
            self.assertEqual(rr.subject_particle(f"v{digit}"), particle)
        # summary에 실제로 쓰인다: 대상 이름 끝 "f100"(영) → 이, "105f"(에프) → 가. 5(오)는 받침이 없어 가.
        for target, expected in (("3222f100-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Isolation Forest 버전 3222f100이 현재 운영"),
                                 ("3222105f-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Isolation Forest 버전 3222105f가 현재 운영")):
            element = one([item(ACTIVE, "active"), item(target)],
                          {ACTIVE: predict(LABELED, tp=8, fp=4), target: predict(LABELED, tp=10, fp=2)}, LABELED)
            self.assertTrue(element["summary"].startswith(expected), element["summary"])

    def test_thresholds_and_fixed_keys_are_recorded(self):
        element = one([item(ACTIVE, "active")], {ACTIVE: predict(LABELED, tp=5)}, LABELED)
        self.assertEqual(element["thresholds"], {
            "minLabeledAnomalyCount": 10, "minLabeledNormalCount": 10, "minScenarioDenominator": 10, "ratioEpsilon": 0.0,
            "latencyP95RelativeLimit": 2.0, "latencyFloorMs": 50, "latencyDeadlineRatio": 0.5,
            "costUnmeasuredPolicy": "allow", "staleAcceptPolicy": "reject",
            "tieBreak": ["recall_tp", "fpr_fp", "precision", "scenario_min", "itemP95Ms", "created_at_asc"]})
        self.assertEqual((element["ruleVersion"], element["comparisonId"], element["comparisonJobId"],
                          element["evaluationSetId"], element["generatedAt"]),
                         ("replacement-recommendation-v1", "cmp", "job", SET, "2026-11-15T00:00:00.000Z"))
        self.assertEqual(sorted(element), sorted([
            "modelKey", "ruleVersion", "decision", "comparisonId", "comparisonJobId", "evaluationSetId",
            "baselineConfigVersionId", "baselineEvaluationId", "baselineActiveRevision", "targetVersionId",
            "targetEvaluationId", "summary", "reason", "noRecommendationReason", "comparedRowCount", "comparedLabelCounts",
            "basis", "excludedMetrics", "checks", "costs", "candidates", "references", "unlabeledEvidence", "thresholds",
            "generatedAt"]))


if __name__ == "__main__":
    unittest.main()
