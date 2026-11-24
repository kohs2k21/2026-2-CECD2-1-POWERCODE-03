"""ML 기여도 `baseline-shapley-v1`(detection-decision.md 6.1): 묶음 규칙·정확 Shapley·무결성 확인·저장 형식·판정 불변.

실제 IF는 작은 합성 학습(128·256 나무, 운영과 같은 6변수 → 참가자 4개)으로 만든다. DB·GPU 없음.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_adapters
from detector import attempt_v04, attribution
from detector.attribution import attach_attributions, compute_attribution, group_players, shapley_values
from detector.decision import decide
from detector.method_results import method_result, ml_method_result, unresolved_result, with_elapsed
from detector.operational_state import ActiveModel
from detector.policy import parse_policy_v2
from feature_fit import definitions_hash
from feature_runtime import calculate_features


def column(name):
    return {"op": "column", "table": "EC_PROCESS_MONITORING", "column": name}


def derived(ast, unit):
    return {"op": "derived", "ast": ast, "outputType": "number", "unit": unit, "provenance": {}}


# 운영 IF와 같은 6변수 구성(값은 합성). 식 기준 묶음 → {total_count, error_count, error_ratio}·success·retry·elapsed.
DEFINITIONS = [
    {"name": "total_count", "expression": {"op": "field", "field": "P.total_count"}},
    {"name": "success_count", "expression": {"op": "field", "field": "P.success_count"}},
    {"name": "error_count", "expression": {"op": "field", "field": "P.error_count"}},
    {"name": "retry_count", "expression": {"op": "field", "field": "P.retry_count"}},
    {"name": "elapsed_ms", "expression": derived({"op": "datetime_diff", "unit": "ms",
                                                   "args": [column("END_TIME"), column("START_TIME")]}, "ms")},
    {"name": "error_ratio", "expression": derived({"op": "divide", "args": [column("ERROR_COUNT"),
                                                                            column("TOTAL_COUNT")]}, "ratio")},
]
NAMES = [item["name"] for item in DEFINITIONS]
THRESHOLD_POLICY = "train-internal-p99-v1"


def raw_row(total, error, retry, elapsed_ms):
    end = f"2026-01-01T00:00:{elapsed_ms // 1000:02d}.{elapsed_ms % 1000:03d}"
    return {"status": "S" if error == 0 else "F", "start_time_raw": "2026-01-01T00:00:00", "end_time_raw": end,
            "total_count": str(total), "success_count": str(total - error), "error_count": str(error),
            "retry_count": str(retry)}


def training_raws(count=900):
    """error·retry·error_ratio는 대부분 0이라 IQR 0(scale 1), total·success·elapsed는 퍼져 있다."""
    rng = np.random.default_rng(2026)
    raws = []
    for index in range(count):
        total = 40 + int(rng.integers(0, 30))
        error = 1 if index % 50 == 0 else 0
        raws.append(raw_row(total, error, 1 if index % 40 == 0 else 0, 400 + int(rng.integers(0, 900))))
    return raws


ANOMALOUS_RAW = raw_row(400, 30, 3, 9500)


class Fixture:
    """학습한 IF 산출물·context·참조. root 아래 상대 경로로 둔다(model_reference·_run_model과 같은 입력)."""

    def __init__(self, directory, n_estimators=128):
        self.root = Path(directory)
        rows = [{"features": calculate_features(DEFINITIONS, raw)[0]} for raw in training_raws()]
        trained = model_adapters.train_iforest(rows, NAMES, str(self.root / f"m{n_estimators}"),
                                               parameters={"n_estimators": n_estimators})
        model_rel = Path(trained["modelPath"]).relative_to(self.root).as_posix()
        fit_rel = Path(trained["fitPath"]).relative_to(self.root).as_posix()
        digest = lambda path: hashlib.sha256((self.root / path).read_bytes()).hexdigest()  # noqa: E731
        self.settings = {"featureNames": NAMES, "thresholdPolicy": THRESHOLD_POLICY,
                         "definitionsHash": definitions_hash(DEFINITIONS)}
        self.threshold_spec = trained["thresholdSpec"]
        self.feature_set = str(uuid.uuid4())
        self.context = {"models": [(self.feature_set, model_rel, digest(model_rel), fit_rel, digest(fit_rel),
                                    self.settings, self.threshold_spec, "iforest")],
                        "definitions": DEFINITIONS, "featureSetId": self.feature_set, "featureHash": "f" * 64}
        self.reference = {"modelPath": str(self.root / model_rel), "fitPath": str(self.root / fit_rel),
                          "featureNames": NAMES, "definitionsHash": self.settings["definitionsHash"]}

    def judged(self, raw, threshold_spec=None):
        """(ML 방법 결과, 측정값) — `_run_model`이 내는 값과 같은 모양."""
        values, missing = calculate_features(DEFINITIONS, raw)
        score = model_adapters.score_iforest(self.reference, values)
        result = ml_method_result("ml:iforest", "process-iforest-v1", "model-version", score,
                                  threshold_spec or self.threshold_spec, THRESHOLD_POLICY, code="IF_ANOMALY")
        return with_elapsed(result, 12.5), {"features": (self.context, values, missing), "reference": self.reference}


def canonical(item):
    item = {key: value for key, value in item.items() if key != "computeMs"}
    return json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Grouping(unittest.TestCase):
    def test_production_shape_six_features_four_players(self):
        players = group_players(DEFINITIONS, NAMES)
        self.assertEqual(players, [("group:total_count", (0, 2, 5)), ("success_count", (1,)),
                                   ("retry_count", (3,)), ("elapsed_ms", (4,))])

    def test_group_id_is_first_member_in_feature_order(self):
        # 순서를 바꾸면 첫 구성원이 바뀐다. 구성원은 featureNames 순, 참가자는 가장 앞 구성원 순.
        names = ["error_ratio", "retry_count", "total_count", "error_count"]
        definitions = [item for item in DEFINITIONS if item["name"] in names]
        self.assertEqual(group_players(definitions, names),
                         [("group:error_ratio", (0, 2, 3)), ("retry_count", (1,))])

    def test_group_key_is_not_a_source(self):
        """group_centered는 value의 원천만, group_median은 원천 없음. groupBy(P.adapter_type)로 묶지 않는다."""
        def fitted(op, field):
            return {"op": op, "groupBy": "P.adapter_type", "value": {"op": "field", "field": field}}
        definitions = [
            {"name": "error_centered", "expression": fitted("group_centered", "P.error_count")},
            {"name": "retry_centered", "expression": fitted("group_centered", "P.retry_count")},
            {"name": "total_median", "expression": fitted("group_median", "P.total_count")},
            {"name": "success_median", "expression": fitted("group_median", "P.success_count")},
            {"name": "error_raw", "expression": {"op": "field", "field": "P.error_count"}},
        ]
        names = [item["name"] for item in definitions]
        self.assertEqual(group_players(definitions, names),
                         [("group:error_centered", (0, 4)), ("retry_centered", (1,)), ("total_median", (2,)),
                          ("success_median", (3,))])

    def test_all_column_leaves_and_literals(self):
        definitions = [
            {"name": "is_missing_end", "expression": derived({"op": "is_missing", "args": [column("END_TIME")]},
                                                             "boolean")},
            {"name": "elapsed_s", "expression": derived({"op": "datetime_diff", "unit": "s",
                                                         "args": [column("END_TIME"), column("START_TIME")]}, "s")},
            {"name": "constant", "expression": derived({"op": "eq", "args": [{"op": "literal", "value": 1},
                                                                             {"op": "literal", "value": 1}]},
                                                       "boolean")},
            {"name": "total_plus_retry", "expression": {"op": "add",
                                                        "left": {"op": "field", "field": "P.total_count"},
                                                        "right": {"op": "field", "field": "P.retry_count"}}},
            {"name": "retry_only", "expression": {"op": "field", "field": "P.retry_count"}},
        ]
        names = [item["name"] for item in definitions]
        self.assertEqual(group_players(definitions, names),
                         [("group:is_missing_end", (0, 1)), ("constant", (2,)), ("group:total_plus_retry", (3, 4))])


# 계약 10절 예시: 참가자 T·E·L, 연합 점수(비트 0 = T, 1 = E, 2 = L).
EXAMPLE_V = {0b000: .40, 0b001: .41, 0b010: .55, 0b100: .50, 0b011: .57, 0b101: .51, 0b110: .69, 0b111: .71}
EXAMPLE_DEFINITIONS = [{"name": "t", "expression": {"op": "field", "field": "P.total_count"}},
                       {"name": "e", "expression": {"op": "field", "field": "P.error_count"}},
                       {"name": "l", "expression": {"op": "field", "field": "P.retry_count"}}]
EXAMPLE_X = {"t": "120", "e": "15", "l": "4200"}
EXAMPLE_M = [100.0, 0.0, 800.0]


def example_inputs(sign=1.0, threshold=".62", direction="higher_is_anomaly", definitions=EXAMPLE_DEFINITIONS,
                   x=EXAMPLE_X, m=EXAMPLE_M, table=None):
    """가짜 기준값·점수 함수와 (방법 결과, 측정값). 점수 = 연합 표(마스크는 실제값과 같은 열로 복원)."""
    names = [item["name"] for item in definitions]
    actual = [float(x[name]) for name in names]
    table = table or EXAMPLE_V

    def baseline(_reference):
        return {"kind": "train_median_robust_scaler_center", "values": list(m), "scaleIsUnit": [False] * len(names)}

    def score(_reference, rows):
        out = []
        for row in rows:
            mask = sum(1 << j for j, value in enumerate(row) if value == actual[j] and value != m[j])
            out.append(sign * table[mask])
        return out

    context = {"models": [("fs", "m.joblib", "a" * 64, "f.joblib", "b" * 64, {"featureNames": names}, {}, "iforest")],
               "definitions": definitions, "featureSetId": "fs", "featureHash": "c" * 64}
    full = (1 << len(names)) - 1
    method = with_elapsed(ml_method_result("ml:iforest", "process-iforest-v1", "mv", sign * table[full],
                                           {"direction": direction, "value": threshold}, THRESHOLD_POLICY,
                                           code="IF_ANOMALY"), 3.0)
    measured = {"features": (context, dict(x), []), "reference": {"modelPath": "unused"}}
    return baseline, score, method, measured


def run_example(**options):
    baseline, score, method, measured = example_inputs(**options)
    with patch.object(model_adapters, "attribution_baseline", baseline), \
            patch.object(model_adapters, "score_iforest_batch", score):
        return method, compute_attribution(method, measured)


class ExactShapley(unittest.TestCase):
    def test_contract_example_values(self):
        method, result = run_example()
        self.assertTrue(method["anomalous"])
        self.assertEqual(result["status"], "succeeded", result)
        phi = {player["playerId"]: player["phi"] for player in result["players"]}
        for key, expected in {"t": .015, "e": .175, "l": .120}.items():
            self.assertAlmostEqual(phi[key], expected, places=12)
        share = {player["playerId"]: player["share"] for player in result["players"]}
        gap = {player["playerId"]: player["thresholdGapShare"] for player in result["players"]}
        self.assertAlmostEqual(share["e"], .175 / .31, places=12)
        self.assertAlmostEqual(gap["e"], .175 / .22, places=12)
        self.assertEqual(result["topK"], ["e", "l", "t"])
        # 계약 예시 문장의 비율(정수 % half-up): ERROR_COUNT 56%, 처리 시간 39%, 처리량(TOTAL_COUNT) 5%.
        self.assertEqual([math.floor(share[key] * 100 + .5) for key in result["topK"]], [56, 39, 5])
        self.assertLessEqual(abs(result["efficiencyError"]), 1e-9)
        self.assertEqual((result["scoreActual"], result["scoreBaseline"]), (.71, .40))
        self.assertIs(result["baselineWarning"], False)

    def test_counterfactual_reuses_coalition_scores(self):
        _, result = run_example()
        self.assertEqual(result["counterfactual"][0], {"replaced": ["e"], "scoreAfter": .51,
                                                       "warningAfterReplacement": False})
        self.assertEqual([item["scoreAfter"] for item in result["counterfactual"]], [.51, .41, .40])
        self.assertEqual(result["clearedAtK"], 1)

    def test_counterfactual_cleared_at_k2_and_never(self):
        # 판정선 0.505: E만 바꾸면 0.51(이상 유지), E·L을 바꾸면 0.41 → k=2. 판정선 0.30이면 끝까지 유지.
        _, result = run_example(threshold=".505")
        self.assertEqual([item["warningAfterReplacement"] for item in result["counterfactual"]], [True, False, False])
        self.assertEqual(result["clearedAtK"], 2)
        _, result = run_example(threshold=".30")
        self.assertIsNone(result["clearedAtK"])
        self.assertIs(result["baselineWarning"], True)
        self.assertTrue(all(player["thresholdGapShare"] is None for player in result["players"]))

    def test_lower_is_anomaly_uses_direction_for_phi_and_counterfactual(self):
        """점수 부호를 뒤집고 판정선 방향을 lower로 두면 φ·비율·반사실 결론이 같다(s = −점수). 저장 점수는 원래 방향."""
        _, higher = run_example()
        method, lower = run_example(sign=-1.0, threshold="-.62", direction="lower_is_anomaly")
        self.assertTrue(method["anomalous"])
        self.assertEqual(lower["direction"], "lower_is_anomaly")
        self.assertEqual((lower["scoreActual"], lower["scoreBaseline"]), (-.71, -.40))
        self.assertEqual([p["phi"] for p in lower["players"]], [p["phi"] for p in higher["players"]])
        self.assertEqual([p["share"] for p in lower["players"]], [p["share"] for p in higher["players"]])
        self.assertEqual([p["thresholdGapShare"] for p in lower["players"]],
                         [p["thresholdGapShare"] for p in higher["players"]])
        self.assertEqual(lower["topK"], higher["topK"])
        self.assertEqual(lower["counterfactual"][0], {"replaced": ["e"], "scoreAfter": -.51,
                                                      "warningAfterReplacement": False})
        self.assertEqual(lower["clearedAtK"], 1)
        # lower 방향에서 판정선 비교가 반대면 반사실이 해소되지 않는다.
        _, kept = run_example(sign=-1.0, threshold="-.30", direction="lower_is_anomaly")
        self.assertIsNone(kept["clearedAtK"])

    def test_negative_phi_and_single_positive_player(self):
        # T가 점수를 낮추는 표: φ_T < 0, 양수 참가자 E만 → topK 1개, 양수 share > 1.
        table = {0b00: .40, 0b01: .30, 0b10: .80, 0b11: .75}
        _, result = run_example(definitions=EXAMPLE_DEFINITIONS[:2], x={"t": "120", "e": "15"}, m=[100.0, 0.0],
                                table=table)
        phi = {p["playerId"]: p["phi"] for p in result["players"]}
        self.assertLess(phi["t"], 0)
        self.assertEqual(result["topK"], ["e"])
        self.assertGreater(result["players"][1]["share"], 1)

    def test_shapley_efficiency_at_cap(self):
        rng = np.random.default_rng(7)
        values = rng.random(1 << 12).tolist()
        phi = shapley_values(values, 12)
        self.assertLessEqual(abs(math.fsum(phi) - (values[-1] - values[0])), 1e-9)


class StoredShape(unittest.TestCase):
    def test_skipped_for_non_anomalous_and_unresolved(self):
        _, _, method, measured = example_inputs(threshold=".80")
        self.assertIs(method["anomalous"], False)
        self.assertEqual(compute_attribution(method, measured),
                         {"version": "baseline-shapley-v1", "status": "skipped", "reason": "not_anomalous"})
        unresolved = unresolved_result("ml:iforest", "process-iforest-v1", "timeout", modelVersionId="mv",
                                       score=None, scoreDetail=None)
        self.assertEqual(compute_attribution(unresolved, None),
                         {"version": "baseline-shapley-v1", "status": "skipped", "reason": "method_unresolved"})

    def test_reference_unavailable_failed_shape(self):
        _, _, method, measured = example_inputs()
        for broken in (None, {"features": measured["features"]}):
            result = compute_attribution(method, broken)
            self.assertEqual(set(result), {"version", "status", "failureCode", "computeMs"})
            self.assertEqual((result["status"], result["failureCode"]), ("failed", "reference_unavailable"))
            self.assertIs(type(result["computeMs"]), float)

    def test_player_cap_is_twelve(self):
        """참가자(묶은 뒤) 13개면 skipped/too_many_players, 12개(4,096행)면 계산한다."""
        def constant(index):
            return derived({"op": "eq", "args": [{"op": "literal", "value": index},
                                                 {"op": "literal", "value": index}]}, "boolean")

        for count, expected in ((12, "succeeded"), (13, "skipped")):
            definitions = [{"name": f"c{index:02d}", "expression": constant(index)} for index in range(count)]
            names = [item["name"] for item in definitions]
            x = {name: str(index + 1) for index, name in enumerate(names)}
            # 점수 = 0.5 + 실제값으로 바꾼 참가자 수 × 2⁻⁴(이진 정확값) → φ 모두 0.0625, 동점은 참가자 순서.
            table = {mask: .5 + .0625 * bin(mask).count("1") for mask in range(1 << count)}
            _, result = run_example(definitions=definitions, x=x, m=[0.0] * count, table=table)
            self.assertEqual(result["status"], expected, result)
            if expected == "skipped":
                self.assertEqual(result, {"version": "baseline-shapley-v1", "status": "skipped",
                                          "reason": "too_many_players"})
            else:
                self.assertEqual(len(result["players"]), 12)
                self.assertEqual({p["phi"] for p in result["players"]}, {result["players"][0]["phi"]})
                self.assertAlmostEqual(result["players"][0]["phi"], .0625, places=12)
                self.assertEqual(result["topK"], ["c00", "c01", "c02"])

    def test_size_cap_fails(self):
        _, result = run_example()
        self.assertLess(attribution.encoded_size(result), attribution.MAX_BYTES)
        # computeMs(실측 시간) 자릿수만큼 실행마다 크기가 몇 바이트 달라지므로 그보다 넉넉히 작은 상한을 쓴다.
        with patch.object(attribution, "MAX_BYTES", attribution.encoded_size(result) - 256):
            _, capped = run_example()
        self.assertEqual((capped["status"], capped["failureCode"]), ("failed", "size_exceeded"))

    def test_nan_injection_fails_and_decision_row_still_serializes(self):
        baseline, score, method, measured = example_inputs()

        def nan_baseline(reference):
            item = baseline(reference)
            item["values"][2] = float("nan")
            return item

        with patch.object(model_adapters, "attribution_baseline", nan_baseline), \
                patch.object(model_adapters, "score_iforest_batch", score):
            result = compute_attribution(method, measured)
        self.assertEqual((result["status"], result["failureCode"]), ("failed", "non_finite_value"))
        json.dumps({**method, "attribution": result}, allow_nan=False)

        def nan_scores(reference, rows):
            return [float("nan")] * len(rows)

        with patch.object(model_adapters, "attribution_baseline", baseline), \
                patch.object(model_adapters, "score_iforest_batch", nan_scores):
            self.assertEqual(compute_attribution(method, measured)["failureCode"], "invalid_score")

    def test_numpy_values_become_plain_python(self):
        baseline, score, method, measured = example_inputs()

        def numpy_baseline(reference):
            item = baseline(reference)
            return {**item, "values": np.asarray(item["values"]), "scaleIsUnit": np.asarray([True, False, True])}

        def numpy_scores(reference, rows):
            return np.asarray(score(reference, rows), dtype=np.float64)

        with patch.object(model_adapters, "attribution_baseline", numpy_baseline), \
                patch.object(model_adapters, "score_iforest_batch", numpy_scores):
            result = compute_attribution(method, measured)
        self.assertEqual(result["status"], "succeeded", result)

        def kinds(value):
            if isinstance(value, dict):
                return set().union(*(kinds(item) for item in value.values())) if value else set()
            if isinstance(value, list):
                return set().union(*(kinds(item) for item in value)) if value else set()
            return {type(value)}

        self.assertLessEqual(kinds(result), {str, float, int, bool, type(None)})
        self.assertIs(result["players"][0]["scaleIsUnit"]["t"], True)

    def test_baseline_unavailable_and_integrity_failures(self):
        baseline, score, method, measured = example_inputs()

        def no_center(_reference):
            raise ValueError("attribution_baseline_unavailable")

        with patch.object(model_adapters, "attribution_baseline", no_center), \
                patch.object(model_adapters, "score_iforest_batch", score):
            self.assertEqual(compute_attribution(method, measured),
                             {"version": "baseline-shapley-v1", "status": "skipped", "reason": "baseline_unavailable"})
        drifted = {**method, "score": method["score"] + 1e-6}
        with patch.object(model_adapters, "attribution_baseline", baseline), \
                patch.object(model_adapters, "score_iforest_batch", score):
            self.assertEqual(compute_attribution(drifted, measured)["failureCode"], "score_not_reproduced")
            with patch.object(attribution, "shapley_values", lambda values, g: [0.0] * g):
                self.assertEqual(compute_attribution(method, measured)["failureCode"], "efficiency_mismatch")


class DecisionUnchanged(unittest.TestCase):
    POLICY = parse_policy_v2({"version": 2, "policyKey": "category-k-of-n-or-v1",
                              "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000})

    def methods(self):
        _, _, ml, measured = example_inputs()
        soft = with_elapsed(method_result("soft:elapsed_ms-3f9a2c1d", "soft-threshold-v1", "succeeded", False), 1.0)
        return [soft, ml], {"ml:iforest": measured}

    def test_attribution_exception_keeps_every_other_key_and_decision(self):
        methods, measured = self.methods()
        before = copy.deepcopy(methods)
        with patch.object(attribution, "_compute", side_effect=RuntimeError("boom")):
            attach_attributions(methods, measured)
        self.assertNotIn("attribution", methods[0])
        failed = methods[1].pop("attribution")
        self.assertEqual((failed["status"], failed["failureCode"]), ("failed", "exception"))
        self.assertEqual(methods, before)
        self.assertEqual(decide(self.POLICY, methods), decide(self.POLICY, before))

    def test_success_adds_only_the_attribution_key(self):
        methods, measured = self.methods()
        before = copy.deepcopy(methods)
        baseline, score, _, _ = example_inputs()
        with patch.object(model_adapters, "attribution_baseline", baseline), \
                patch.object(model_adapters, "score_iforest_batch", score):
            attach_attributions(methods, measured)
        self.assertEqual(methods[1]["attribution"]["status"], "succeeded")
        self.assertEqual(decide(self.POLICY, methods)["composite"], decide(self.POLICY, before)["composite"])
        methods[1].pop("attribution")
        self.assertEqual(methods, before)


class RealIsolationForest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="attribution-")
        cls.fixture = Fixture(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        model_adapters._load_pair.cache_clear()
        cls.directory.cleanup()

    def test_reproduction_efficiency_and_counterfactual(self):
        method, measured = self.fixture.judged(ANOMALOUS_RAW)
        self.assertTrue(method["anomalous"], method)
        result = compute_attribution(method, measured)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["scoreActual"], method["score"])  # 묶음 점수 = 단건 점수(재현)
        tol = max(1e-9, 1e-9 * abs(method["score"]))
        self.assertLessEqual(abs(result["efficiencyError"]), tol)
        self.assertEqual([p["playerId"] for p in result["players"]],
                         ["group:total_count", "success_count", "retry_count", "elapsed_ms"])
        self.assertEqual(result["threshold"], self.fixture.threshold_spec["value"])
        self.assertEqual(result["grouping"], "source-field-union-v1")
        self.assertEqual(result["baselineKind"], "train_median_robust_scaler_center")
        # 반사실 점수 = 상위 참가자 변수를 기준값으로 바꾼 벡터를 단건 점수로 다시 잰 값.
        center = model_adapters.attribution_baseline(self.fixture.reference)["values"]
        values = measured["features"][1]
        members = {p["playerId"]: p["features"] for p in result["players"]}
        for item in result["counterfactual"]:
            replaced = {name for pid in item["replaced"] for name in members[pid]}
            vector = {name: (repr(center[NAMES.index(name)]) if name in replaced else values[name]) for name in NAMES}
            self.assertAlmostEqual(item["scoreAfter"], model_adapters.score_iforest(self.fixture.reference, vector),
                                   places=12)
            boundary = float(self.fixture.threshold_spec["value"])
            self.assertEqual(item["warningAfterReplacement"], item["scoreAfter"] >= boundary)
        pins = result["pins"]
        self.assertEqual((pins["modelArtifactHash"], pins["fitArtifactHash"]),
                         (self.fixture.context["models"][0][2], self.fixture.context["models"][0][4]))
        self.assertEqual(pins["featureNamesHash"],
                         hashlib.sha256(json.dumps(NAMES, separators=(",", ":")).encode()).hexdigest())
        self.assertLess(attribution.encoded_size(result), 4096)

    def test_scale_is_unit_per_member(self):
        method, measured = self.fixture.judged(ANOMALOUS_RAW)
        result = compute_attribution(method, measured)
        scaler = model_adapters._prepared(self.fixture.reference)[1]
        expected = {name: bool(scaler.scale_[index] == 1.0) for index, name in enumerate(NAMES)}
        # 합성 학습: error·retry·error_ratio는 IQR 0 → scale 1, total·success·elapsed는 아님.
        self.assertEqual(expected, {"total_count": False, "success_count": False, "error_count": True,
                                    "retry_count": True, "elapsed_ms": False, "error_ratio": True})
        for player in result["players"]:
            self.assertEqual(set(player["scaleIsUnit"]), set(player["features"]))
            self.assertEqual(set(player["actual"]), set(player["features"]))
            self.assertEqual(set(player["baseline"]), set(player["features"]))
            for name in player["features"]:
                self.assertIs(player["scaleIsUnit"][name], expected[name])
                self.assertIs(type(player["actual"][name]), str)
                self.assertIs(type(player["baseline"][name]), float)
        group = result["players"][0]
        self.assertEqual(group["scaleIsUnit"], {"total_count": False, "error_count": True, "error_ratio": True})

    def test_deterministic_canonical_json(self):
        method, measured = self.fixture.judged(ANOMALOUS_RAW)
        first, second = compute_attribution(method, measured), compute_attribution(method, measured)
        self.assertEqual(canonical(first), canonical(second))
        self.assertEqual(list(first), ["version", "status", "baselineKind", "grouping", "scoreActual", "scoreBaseline",
                                       "threshold", "direction", "baselineWarning", "efficiencyError", "players",
                                       "topK", "counterfactual", "clearedAtK", "pins", "computeMs"])
        self.assertEqual(list(first["players"][0]), ["playerId", "features", "actual", "baseline", "scaleIsUnit",
                                                     "phi", "share", "thresholdGapShare"])

    def test_transformed_space_is_the_same(self):
        """scaler 변환 뒤 기준값은 0벡터라 연합을 변환 공간에서 만들어도 점수가 같다(계약 2절 참고)."""
        forest, scaler, _ = model_adapters._prepared(self.fixture.reference)
        center = np.asarray(model_adapters.attribution_baseline(self.fixture.reference)["values"])
        self.assertEqual(float(np.max(np.abs(scaler.transform(center[None, :])))), 0.0)
        values = np.asarray([float(v) for v in calculate_features(DEFINITIONS, ANOMALOUS_RAW)[0].values()])
        bits = np.asarray([[mask >> j & 1 for j in range(len(NAMES))] for mask in range(64)], dtype=bool)
        raw_space = model_adapters.score_iforest_batch(self.fixture.reference,
                                                       np.where(bits, values[None, :], center[None, :]).tolist())
        transformed = -forest.score_samples(np.where(bits, scaler.transform(values[None, :]), 0.0))
        self.assertEqual(float(np.max(np.abs(np.asarray(raw_space) - transformed))), 0.0)

    def test_normal_row_is_skipped(self):
        method, measured = self.fixture.judged(raw_row(55, 0, 0, 800))
        self.assertIs(method["anomalous"], False, method)
        self.assertEqual(compute_attribution(method, measured)["reason"], "not_anomalous")

    def test_run_model_adopts_verified_reference(self):
        model = attempt_v04._Model(ActiveModel(uuid.uuid4(), "iforest"), self.fixture.context)
        outcome = attempt_v04._run_model(model, ANOMALOUS_RAW, self.fixture.root)
        self.assertEqual(outcome.result["status"], "succeeded")
        self.assertEqual(Path(outcome.measured["reference"]["modelPath"]).resolve(),
                         Path(self.fixture.reference["modelPath"]).resolve())
        self.assertEqual(outcome.measured["features"][0], self.fixture.context)
        result = compute_attribution(with_elapsed(outcome.result, 1.0), outcome.measured)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["scoreActual"], outcome.result["score"])


if __name__ == "__main__":
    unittest.main()
