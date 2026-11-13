"""DB·GPU 없는 시험: export 본체(build_export)·출력 폴더 거부·judge 판정(가짜 GPU 출력)."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capture_layout as layout  # noqa: E402
import export_server_captures as export  # noqa: E402
import judge  # noqa: E402
import make_cases  # noqa: E402

BASE = datetime(2026, 8, 3, 10, 0, 0)


def raw(at):
    return at.isoformat(sep=" ", timespec="milliseconds")


def observations(transactions=40, tie_pairs=True):
    """거래 2개씩 같은 원천 시각(묶음 시각 동률), F 행·anomaly 라벨·시각 해석 실패 행 포함."""
    rows = []
    for tx in range(transactions):
        at = BASE + timedelta(seconds=10 * (tx // 2 if tie_pairs else tx))
        for proc in range(2):
            end = at if proc == 0 else at + timedelta(seconds=3)
            rows.append({"transaction_id": f"SECRET-TX-{tx:03d}", "process_id": f"P{proc}", "status": "S",
                         "start_time_raw": raw(end - timedelta(milliseconds=100 + tx)), "end_time_raw": raw(end),
                         "total_count": 10, "success_count": 9, "error_count": tx % 3, "retry_count": tx % 2})
    rows.append({**rows[0], "status": "F"})
    rows.append({**rows[1], "start_time_raw": "N/A", "end_time_raw": "??"})
    labelled = {**rows[2], "process_id": "PL"}
    rows.append(labelled)
    obs = [(f"o{i}", r, {"label": "anomaly"} if r is labelled else None) for i, r in enumerate(rows)]
    return obs


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rv3-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        lines = []
        index = export.build_export(self.tmp / "out", observations(), source_tz=None, timezone_basis="assumed_Asia/Seoul",
                                    snapshot_id="00000000-0000-0000-0000-000000000001", log=lines.append, **kw)
        return index, lines

    def test_global_cases_counts_and_filters(self):
        index, lines = self.build()
        self.assertEqual(index["format"], "rule-v3-baseline-export-v1")
        self.assertEqual([c["field"] for c in index["cases"]], list(layout.SERVICE_FIELDS))
        self.assertEqual([c["case_id"] for c in index["cases"]], [f"real-{i:03d}" for i in range(1, 7)])
        g = index["counts"]["global"]
        self.assertEqual((g["excluded_status_not_S"], g["excluded_anomaly_label"]), (1, 1))
        self.assertEqual(g["grouping"]["excluded_unparsed_time"], 1)
        cut = g["cut"]
        self.assertEqual(cut["reference_rows"] + cut["current_rows"], 80)
        self.assertLess(cut["reference_blocks"], cut["reference_groups"])  # 동률 묶음이 블록으로 합쳐짐
        for case in index["cases"]:
            folder = self.tmp / "out" / case["path"]
            for rel, digest in case["files"].items():
                self.assertEqual(layout.sha256_file(folder / rel), digest)
            ref = layout.read_json(folder / "reference/capture.json")
            app = layout.read_json(folder / "application/capture.json")
            self.assertLessEqual(ref["period"]["end_exclusive"], app["period"]["start_inclusive"])
            self.assertEqual(ref["split"], "train")
            self.assertEqual(ref["data_class"], "approved_real")

    def test_ties_and_transactions_not_split(self):
        split = layout.split_observations(observations(), None)
        # 같은 원천 시각 쌍(거래 2k, 2k+1)은 같은 쪽: TX_ID는 순서대로 부여되므로 쌍의 행 수로 확인한다.
        ref_end = split["reference_period"]["end_exclusive"]
        ref_times = {r["END_TIME"] for r in split["reference"]["rows"]}
        cur_times = {r["END_TIME"] for r in split["current"]["rows"]}
        self.assertFalse(ref_times & cur_times)  # 같은 시각이 양쪽에 없음
        self.assertTrue(min(cur_times) >= ref_end)
        self.assertEqual(len(split["reference"]["transactions"]) % 2, 0)  # 동률 쌍이 갈리지 않음
        self.assertEqual(split["counts"]["cut"]["reference_rows"], 2 * len(split["reference"]["transactions"]) - 0)

    def test_no_values_in_log_and_index(self):
        index, lines = self.build()
        text = "\n".join(lines) + json.dumps(index)
        self.assertNotIn("SECRET-TX", text)
        self.assertNotIn("2026-08-03", text.replace(index["created_at"], ""))
        self.assertNotIn("p95", " ".join(lines))

    def test_interface_cases_private_map(self):
        def lookup(transactions):
            return export.interface_map([(tx, "IF_SECRET_A" if int(tx[-3:]) % 2 else "IF_SECRET_B") for tx in transactions])
        index, lines = self.build(per_interface_top=1, interface_of=lookup)
        self.assertEqual(len(index["cases"]), 12)
        self.assertEqual(index["cases"][6]["scope"], "interface")
        self.assertNotIn("IF_SECRET", json.dumps(index) + "\n".join(lines))
        private = layout.read_json(self.tmp / "out/private/interface_map.json")
        self.assertIn(private["interface_rank_to_interface_id"]["1"], ("IF_SECRET_A", "IF_SECRET_B"))

    def test_output_inside_repo_refused(self):
        with self.assertRaises(SystemExit):
            export.check_output_dir(layout.REPO / "research" / "tmp-export")
        existing = self.tmp / "exists"
        existing.mkdir()
        with self.assertRaises(SystemExit):
            export.check_output_dir(existing)

    def test_source_timezone_rule(self):
        self.assertEqual(export.source_timezone({"status": "verified", "timezone": "UTC"}), ("UTC", "verified"))
        self.assertEqual(export.source_timezone({"status": "verified"}), ("", "verified_without_timezone"))
        self.assertEqual(export.source_timezone({}), (None, "assumed_Asia/Seoul"))
        self.assertEqual(export.source_timezone(None), (None, "assumed_Asia/Seoul"))


def spanning(first, count, step_hours=3):
    """KST 원문, 거래당 PROCESS 1행. first부터 step_hours 간격."""
    rows = []
    for tx in range(count):
        end = first + timedelta(hours=step_hours * tx)
        rows.append((f"o{tx}", {"transaction_id": f"T{tx:04d}", "status": "S", "start_time_raw": raw(end - timedelta(seconds=1)),
                                "end_time_raw": raw(end), "total_count": 5, "success_count": 5, "error_count": 0,
                                "retry_count": tx % 2}, None))
    return rows


class HeldWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rv3-held-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def export(self, obs, name, **kw):
        return export.build_export(self.tmp / name, obs, source_tz=None, timezone_basis="assumed_Asia/Seoul",
                                   snapshot_id="s", log=lambda _: None, **kw)

    def test_default_reports_overlap(self):
        index = self.export(spanning(datetime(2026, 9, 25), 80), "default")  # 9/25~10/5 KST
        self.assertTrue(index["held_window"]["overlap_before_rule"])
        self.assertIsNone(index["held_window"]["side"])
        self.assertTrue(all(c["held_window_overlap"] for c in index["cases"]))

    def test_overlap_case_is_not_run_and_judged_fail(self):
        import run_cases
        self.export(spanning(datetime(2026, 9, 25), 80), "default")
        with contextlib.redirect_stdout(io.StringIO()):
            run_cases.main(["--cases-dir", str(self.tmp / "default"), "--output-root", str(self.tmp / "runs"),
                            "--run-name", "held", "--cpu-only", "--cases", "real-001"])
        run_dir = next((self.tmp / "runs").glob("*-held"))
        summary = layout.read_json(run_dir / "summary.json")
        self.assertEqual(summary["cases"][0]["reasons"], ["held_window_overlap_not_run"])
        self.assertFalse((run_dir / "real-001" / "feature").exists())
        self.assertIn("입력 가정", (run_dir / "summary.md").read_text(encoding="utf-8"))
        self.assertTrue(layout.read_json(run_dir / "run.json")["input_assumptions"])

    def test_avoid_keeps_after_side_and_counts_dropped(self):
        obs = spanning(datetime(2026, 9, 25), 120)  # 9/25~10/9 KST
        index = self.export(obs, "after", avoid_held_window=True)
        held = index["held_window"]
        self.assertEqual((held["side"], held["avoid_failed"]), ("after", False))
        self.assertFalse(any(c["held_window_overlap"] for c in index["cases"]))
        g = index["counts"]["global"]["grouping"]
        self.assertEqual(held["dropped_rows"], held["dropped_held_rows"] + g["outside_source_window"])
        self.assertGreater(held["dropped_held_transactions"], 0)
        self.assertEqual(g["rows_grouped"] + held["dropped_rows"], 120)
        self.assert_no_held_timestamps(self.tmp / "after", index)
        ref = layout.read_json(self.tmp / "after/real-001/reference/capture.json")["period"]
        self.assertGreaterEqual(ref["start_inclusive"], "2026-10-03")
        self.assertEqual(index["source_window"]["from"], "2026-10-03T00:00:00+00:00")

    def test_avoid_falls_back_to_before_side(self):
        obs = spanning(datetime(2026, 9, 20), 100)  # 9/20~10/2 KST, 뒤쪽 없음
        index = self.export(obs, "before", avoid_held_window=True)
        self.assertEqual(index["held_window"]["side"], "before")
        app = layout.read_json(self.tmp / "before/real-001/application/capture.json")["period"]
        self.assertLessEqual(app["end_exclusive"], layout.HELD_LOW)  # UTC 9/29 15:00 = KST 9/30 00:00 이전
        self.assertFalse(any(c["held_window_overlap"] for c in index["cases"]))

    def test_kst_september_30_early_morning_is_flagged(self):
        # 마지막 행 KST 9/30 03:00 = UTC 9/29 18:00. 번들 UTC 문자열 검사(hi > "2026-09-30")는 통과시키지만
        # 보류 계보는 KST 날짜이므로 겹침으로 잡아야 한다.
        obs = spanning(datetime(2026, 9, 20), 82)  # 9/20 00:00 ~ 9/30 03:00 KST, 3시간 간격
        index = self.export(obs, "kst")
        app = layout.read_json(self.tmp / "kst/real-001/application/capture.json")["period"]
        self.assertFalse(app["start_inclusive"] < "2026-10-03" and app["end_exclusive"] > "2026-09-30")  # 번들 검사만으로는 통과
        self.assertTrue(index["held_window"]["overlap_before_rule"])
        self.assertTrue(all(c["held_window_overlap"] for c in index["cases"]))
        avoided = self.export(obs, "kst-avoid", avoid_held_window=True)
        self.assertEqual(avoided["held_window"]["side"], "before")
        self.assertEqual(avoided["held_window"]["dropped_rows"], 2)  # KST 9/30 00:00·03:00 두 행
        self.assertFalse(any(c["held_window_overlap"] for c in avoided["cases"]))

    def assert_no_held_timestamps(self, out, index):
        """모든 사례 캡처의 START·END가 보류 창 [HELD_LOW, 2026-10-03T00:00Z) 밖이어야 한다."""
        high = "2026-10-03T00:00:00.000000+00:00"
        for case in index["cases"]:
            for side in ("reference", "application"):
                capture, tables = layout.read_capture_rows(out / case["path"] / side / "capture.json")
                for row in tables["PROCESS"]:
                    for column in ("START_TIME", "END_TIME"):
                        stamp = row.get(column)
                        if stamp:
                            self.assertFalse(layout.HELD_LOW <= stamp < high, (case["case_id"], side, column))

    def leak_observations(self):
        # 검토자 재현: 마지막 거래의 첫 행 KST 9/29 23:59:59, 같은 거래 둘째 행 KST 9/30 00:00:05(보류 날짜).
        # 묶음 시각(최솟값)만 보는 기간 검사는 둘째 행을 못 본다.
        obs = spanning(datetime(2026, 9, 20), 80)
        base = {"transaction_id": "TLAST", "status": "S", "total_count": 5, "success_count": 5, "error_count": 0,
                "retry_count": 0}
        obs.append(("x1", {**base, "start_time_raw": raw(datetime(2026, 9, 29, 23, 59, 58)),
                           "end_time_raw": raw(datetime(2026, 9, 29, 23, 59, 59))}, None))
        obs.append(("x2", {**base, "start_time_raw": raw(datetime(2026, 9, 30, 0, 0, 1)),
                           "end_time_raw": raw(datetime(2026, 9, 30, 0, 0, 5))}, None))
        return obs

    def test_later_row_of_transaction_inside_window_is_flagged_and_dropped(self):
        obs = self.leak_observations()
        split = layout.split_observations(obs, None)
        self.assertFalse(layout.held_window_overlap(split["reference_period"]) or
                         layout.held_window_overlap(split["current_period"]))  # 묶음 시각 기간만으로는 못 잡음
        index = self.export(obs, "leak")
        self.assertTrue(index["held_window"]["overlap_before_rule"])
        self.assertTrue(all(c["held_window_overlap"] for c in index["cases"]))
        avoided = self.export(obs, "leak-avoid", avoid_held_window=True)
        held = avoided["held_window"]
        self.assertEqual((held["side"], held["dropped_held_transactions"], held["dropped_held_rows"]), ("before", 1, 2))
        self.assertFalse(any(c["held_window_overlap"] for c in avoided["cases"]))
        self.assert_no_held_timestamps(self.tmp / "leak-avoid", avoided)

    def test_row_with_start_inside_window_and_end_after_is_flagged_and_dropped(self):
        obs = spanning(datetime(2026, 10, 3, 12), 60)  # KST 10/3 12:00부터: 전부 보류 창 뒤
        obs.insert(0, ("s1", {"transaction_id": "TSPAN", "status": "S", "total_count": 5, "success_count": 5,
                              "error_count": 0, "retry_count": 0,
                              "start_time_raw": raw(datetime(2026, 10, 2, 23, 0)),    # UTC 10/2 14:00: 창 안
                              "end_time_raw": raw(datetime(2026, 10, 3, 10, 0))}, None))  # UTC 10/3 01:00: 창 뒤
        index = self.export(obs, "span")
        self.assertTrue(index["held_window"]["overlap_before_rule"])  # 행 시각(END)·기간은 창 뒤지만 START가 창 안
        self.assertTrue(all(c["held_window_overlap"] for c in index["cases"]))
        avoided = self.export(obs, "span-avoid", avoid_held_window=True)
        held = avoided["held_window"]
        self.assertEqual((held["side"], held["dropped_held_transactions"], held["dropped_held_rows"]), ("after", 1, 1))
        self.assertEqual(held["dropped_outside_side_rows"], 0)
        self.assertFalse(any(c["held_window_overlap"] for c in avoided["cases"]))
        self.assert_no_held_timestamps(self.tmp / "span-avoid", avoided)

    def test_boundary_strings(self):
        self.assertFalse(layout.held_window_overlap({"start_inclusive": "2026-09-01T00:00:00.000000+00:00",
                                                     "end_exclusive": layout.HELD_LOW}))
        self.assertTrue(layout.held_window_overlap({"start_inclusive": "2026-09-01T00:00:00.000000+00:00",
                                                    "end_exclusive": "2026-09-29T15:00:00.000001+00:00"}))
        self.assertFalse(layout.held_window_overlap({"start_inclusive": "2026-10-03T00:00:00.000000+00:00",
                                                     "end_exclusive": "2026-10-09T00:00:00.000000+00:00"}))
        self.assertTrue(layout.held_window_overlap({"start_inclusive": "2026-10-02T23:59:59.999999+00:00",
                                                    "end_exclusive": "2026-10-09T00:00:00.000000+00:00"}))

    def test_argument_window_counts_outside_rows(self):
        obs = spanning(datetime(2026, 8, 1), 60)
        lower = export.parse_bound("2026-08-02T00:00:00+09:00")
        index = self.export(obs, "arg", source_window=(lower, None))
        self.assertEqual(index["counts"]["global"]["grouping"]["outside_source_window"], 8)  # 8/1 KST 0~21시 8행
        self.assertFalse(index["held_window"]["overlap_before_rule"])
        with self.assertRaises(SystemExit):
            export.parse_bound("2026-08-02T00:00:00")  # 오프셋 없음 거부


class JudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rv3-judge-"))
        with contextlib.redirect_stdout(io.StringIO()):
            cls.index = make_cases.build(cls.tmp / "cases")
        cls.by_id = {c["case_id"]: c for c in cls.index["cases"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fake_run(self, name, case_id, decision, *, attempts=None, rule_exit=0, baseline_value=None, seconds=(30.0,),
                 length=(False,)):
        run = self.tmp / name
        case = run / case_id
        (case / "rule").mkdir(parents=True)
        spec = self.by_id[case_id]
        expected = spec["expected"]
        p95 = spec["expected"]["reference_p95_decimal"]
        value = float(p95) if baseline_value is None else baseline_value
        layout.write_json(run / "run.json", {"mode": "gpu", "status": "completed", "selected_cases": [case_id],
                                             "cases_index": str(self.tmp / "cases/index.json"),
                                             "variant": {"label": "번들 그대로"}})
        layout.write_json(case / "case_result.json", {
            "steps": {"validate_feature": {"exit": 0}, "rule": {"exit": rule_exit}, "replay": {"exit": 0}},
            "input_sha256": {"files_match_index": True},
            "cpu_checks": {"baseline_state": expected["baseline_state"], "bundle_p95_matches_independent": True,
                           "expected_states_accepted": True},
            "attempts": [{"seconds": s, "length_limit_hit": h} for s, h in zip(seconds, length)]})
        attempts = attempts or [{"passed": True, "errors": []}]
        layout.write_json(case / "rule/rule_bundle.json", {
            "status": "rule_verified" if attempts[-1]["passed"] else "rule_failed",
            "prediction": {"decisions": [decision]}, "attempts": attempts,
            "input": {"baseline": {"state": expected["baseline_state"], "value": value if expected["baseline_state"] == "verified" else None}}})
        return judge.judge_run(run)

    def decision(self, state, condition=None, needed=(), reason="Current values are compared with the reference p95."):
        return {"state": state, "condition": condition, "needed": list(needed), "reason": reason}

    def test_proposed_pass_and_service_conversion(self):
        p95 = float(self.by_id["syn-002"]["expected"]["reference_p95_decimal"])
        summary = self.fake_run("r1", "syn-002", self.decision("proposed", {"op": "gt", "value": p95}))
        self.assertEqual((summary["passed"], summary["total"]), (1, 1), summary)
        j = layout.read_json(self.tmp / "r1/syn-002/judgment.json")
        self.assertEqual(j["service_conversion"]["item"]["operator"], "gt")
        self.assertEqual(j["service_conversion"]["item"]["threshold"], self.by_id["syn-002"]["expected"]["reference_p95_decimal"])
        md = (self.tmp / "r1/summary.md").read_text(encoding="utf-8")
        self.assertIn("**통과 1/1**", md)
        self.assertIn("| " + " | ".join(judge.COLUMNS) + " |", md)

    def test_wrong_threshold_fails(self):
        summary = self.fake_run("r2", "syn-001", self.decision("proposed", {"op": "gt", "value": 1.5}))
        reasons = summary["cases"][0]["reasons"]
        self.assertIn("p95_mismatch", reasons)
        self.assertIn("service_conversion", reasons)

    def test_state_needed_seconds_length_and_repair(self):
        attempts = [{"passed": False, "errors": [{"code": "baseline_grounding"}]}, {"passed": True, "errors": []}]
        summary = self.fake_run("r3", "syn-004", self.decision("request_evidence", None, ["need_reference"]),
                                attempts=attempts, seconds=(250.0, 20.0), length=(True, False))
        reasons = summary["cases"][0]["reasons"]
        self.assertEqual(set(reasons), {"needed_unexpected", "seconds_over_limit_or_missing", "length_limit_hit_or_missing"})
        self.assertEqual(summary["cases"][0]["row"]["수리 전/후"], "1차 실패→수리 통과")

    def test_constant_accepts_no_applicable_and_unreachable_rejects_proposed(self):
        summary = self.fake_run("r4", "syn-008", self.decision("no_applicable"))
        self.assertEqual(summary["passed"], 1, summary)
        summary = self.fake_run("r5", "syn-009", self.decision("proposed", {"op": "gt", "value": 1}))
        self.assertIn("state_unexpected", summary["cases"][0]["reasons"])

    def test_reason_too_long_fails(self):
        summary = self.fake_run("r6", "syn-007", self.decision("deferred", {"op": "gt", "value": float(
            self.by_id["syn-007"]["expected"]["reference_p95_decimal"])}, ["need_valid_observations"], reason="x" * 321))
        self.assertEqual(summary["cases"][0]["reasons"], ["reason_format"])

    def test_real_case_summary_has_no_values(self):
        out = self.tmp / "real"
        with contextlib.redirect_stdout(io.StringIO()):
            index = export.build_export(out, observations(), source_tz=None, timezone_basis="assumed_Asia/Seoul",
                                        snapshot_id="s", log=lambda _: None)
        case = index["cases"][4]  # elapsed_ms
        facts = layout.facts_from_case(out / case["path"], case["field"])
        p95 = float(facts["p95"])
        run = self.tmp / "real-run"
        (run / case["case_id"] / "rule").mkdir(parents=True)
        layout.write_json(run / "run.json", {"mode": "gpu", "status": "completed", "selected_cases": [case["case_id"]],
                                             "cases_index": str(out / "index.json"), "variant": {"label": "x"}})
        layout.write_json(run / case["case_id"] / "case_result.json", {
            "steps": {"validate_feature": {"exit": 0}, "rule": {"exit": 0}, "replay": {"exit": 0}},
            "input_sha256": {"files_match_index": True},
            "cpu_checks": {"baseline_state": "verified", "bundle_p95_matches_independent": True, "expected_states_accepted": True},
            "attempts": [{"seconds": 10.0, "length_limit_hit": False}]})
        layout.write_json(run / case["case_id"] / "rule/rule_bundle.json", {
            "status": "rule_verified", "attempts": [{"passed": True, "errors": []}],
            "prediction": {"decisions": [self.decision("proposed", {"op": "gt", "value": p95})]},
            "input": {"baseline": {"state": "verified", "value": p95}}})
        summary = judge.judge_run(run)
        self.assertEqual(summary["passed"], 1, summary)
        text = (run / "summary.md").read_text(encoding="utf-8") + (run / "summary.json").read_text(encoding="utf-8") + \
            (run / case["case_id"] / "judgment.json").read_text(encoding="utf-8")
        self.assertNotIn(layout.decimal_text(facts["p95"]), text)
        self.assertNotIn(repr(p95), text)
        self.assertIn("withheld_real_data", text)


if __name__ == "__main__":
    unittest.main()
