"""Synthetic checks for immutable rules, abstention, retry/hub joins and cold start."""
import unittest
import numpy as np
import pandas as pd

from log_analysis_common import add_time_features
from rule_strategy_common import (
    resolve_config, evaluate_rules, hub_aware_summary, schema_audit,
    group_keys, freeze_quantiles, score_frozen_quantiles, grouping_profile,
)


def tx(rows=1):
    start = pd.date_range("2026-01-01", periods=rows, freq="D")
    return add_time_features(pd.DataFrame({
        "source_period": ["old"] * rows, "transactionId": [f"t{i}" for i in range(rows)],
        "processHubId": ["HUB"] * rows, "retryCount": [0.] * rows,
        "interfaceId": ["old"] * rows, "processCount": [1.] * rows,
        "status": ["S"] * rows, "responseCode": ["0000"] * rows,
        "startTime": start, "endTime": start + pd.Timedelta(seconds=1),
        "interfaceType": ["NORMAL"] * rows, "startChannelId": ["a"] * rows,
        "endChannelId": ["b"] * rows, "category_top": ["cat"] * rows,
    }))


def processes():
    p = tx().drop(columns=["interfaceId", "processCount"])
    p["processId"] = "p"
    p["adapterType"] = "SERVICE.EXECUTOR"
    p["channelId"] = "a"
    p["totalCount"], p["successCount"], p["errorCount"] = 1., 1., 0.
    return p


class RuleTests(unittest.TestCase):
    def test_defaults_and_deliberate_false_zero(self):
        c = resolve_config({"time_tolerance_ms": 0, "business_alert_statuses": [], "check_response_convention": False})
        self.assertEqual(c["time_tolerance_ms"], 0)
        self.assertEqual(c["business_alert_statuses"], [])
        c["allowed_statuses"].append("X")
        self.assertNotIn("X", resolve_config()["allowed_statuses"])

    def test_invalid_configuration(self):
        for override in ({"typo": 1}, {"time_tolerance_ms": -1}, {"pending_timeout_ms": -1},
                         {"allowed_hours_by_interface": {"old": [24]}},
                         {"pattern_rules": [{"name": "a", "conditions": {}}]}):
            with self.assertRaises(ValueError):
                resolve_config(override)

    def test_nullable_status_is_review_not_invalid_enum(self):
        f = tx(); f.loc[0, "status"] = None
        flags, _ = evaluate_rules(f, "transaction", resolve_config())
        self.assertTrue(flags.loc[0, "missing_status"])
        self.assertFalse(flags.loc[0, "invalid_status"])
        self.assertFalse(flags.loc[0, "quarantine"])

    def test_unknown_enum_quarantined(self):
        f = tx(); f.loc[0, "status"] = "Q"
        flags, _ = evaluate_rules(f, "transaction", resolve_config())
        self.assertTrue(flags.loc[0, "quarantine"])

    def test_failure_alert_is_not_invalid_data(self):
        f = tx(); f.loc[0, ["status", "responseCode"]] = ["F", "4311"]
        flags, _ = evaluate_rules(f, "transaction", resolve_config())
        self.assertTrue(flags.loc[0, "business_alert"])
        self.assertFalse(flags.loc[0, "quarantine"])
        self.assertFalse(flags.loc[0, "reference_eligible"])

    def test_pending_end_missing_not_final_error(self):
        f = tx(); f.loc[0, "status"] = "P"; f.loc[0, "endTime"] = pd.NaT
        flags, eligible = evaluate_rules(f, "transaction", resolve_config())
        self.assertFalse(flags.loc[0, "final_missing_end"])
        self.assertFalse(eligible.loc[0, "pending_timeout"])

    def test_timeout_requires_explicit_cutoff_and_boundary(self):
        f = tx(); f.loc[0, "status"] = "N"; f.loc[0, "endTime"] = pd.NaT
        c = resolve_config({"pending_timeout_ms": 1000})
        flags, eligible = evaluate_rules(f, "transaction", c)
        self.assertFalse(eligible.loc[0, "pending_timeout"])
        flags, _ = evaluate_rules(f, "transaction", c, observed_as_of={"old": "2026-01-01 00:00:01"})
        self.assertFalse(flags.loc[0, "pending_timeout"])
        flags, _ = evaluate_rules(f, "transaction", c, observed_as_of={"old": "2026-01-01 00:00:01.001"})
        self.assertTrue(flags.loc[0, "pending_timeout"])

    def test_fractional_and_negative_counts(self):
        f = tx(2); f.loc[0, "processCount"] = -1; f.loc[1, "retryCount"] = .5
        flags, _ = evaluate_rules(f, "transaction", resolve_config())
        self.assertTrue(flags.quarantine.all())

    def test_count_invariant_needs_completed_outcome(self):
        f = processes(); f.loc[0, "status"] = "P"; f.loc[0, "successCount"] = 0
        flags, eligible = evaluate_rules(f, "process", resolve_config())
        self.assertFalse(eligible.loc[0, "count_conservation"])
        f.loc[0, "status"] = "F"
        flags, _ = evaluate_rules(f, "process", resolve_config())
        self.assertTrue(flags.loc[0, "count_conservation"])
        self.assertFalse(flags.loc[0, "quarantine"])
        flags, _ = evaluate_rules(f, "process", resolve_config({"count_conservation_confirmed": True}))
        self.assertTrue(flags.loc[0, "quarantine"])

    def test_pattern_and_time_policy(self):
        f = tx()
        c = resolve_config({"pattern_rules": [{"name": "combo", "conditions": {"interfaceId": {"regex": "^old$"}, "status": "S"}}],
                            "allowed_hours_by_interface": {"old": [9, 10]}, "business_alert_statuses": []})
        flags, _ = evaluate_rules(f, "transaction", c)
        self.assertTrue(flags.loc[0, "configured_pattern"])
        self.assertTrue(flags.loc[0, "outside_allowed_hours"])

    def test_hub_join_does_not_merge_two_hubs(self):
        t = pd.concat([tx(), tx()], ignore_index=True); t.loc[1, "processHubId"] = "OTHER"
        p = processes(); p.loc[0, "status"] = "F"
        result, _ = hub_aware_summary(t, p)
        self.assertTrue(result.loc[0, "tx_success_all_process_failed"])
        self.assertTrue(result.loc[1, "no_process"])
        self.assertFalse(result.loc[1, "tx_success_all_process_failed"])

    def test_incomplete_process_set_is_not_all_failed(self):
        t = tx(); t.loc[0, "processCount"] = 2
        p = processes(); p.loc[0, "status"] = "F"
        result, _ = hub_aware_summary(t, p)
        self.assertTrue(result.loc[0, "process_count_mismatch"])
        self.assertFalse(result.loc[0, "tx_success_all_process_failed"])

    def test_ambiguous_parent_abstains(self):
        t = pd.concat([tx(), tx()], ignore_index=True)
        result, audit = hub_aware_summary(t, processes())
        self.assertFalse(result.join_unambiguous.any())
        self.assertFalse(result.tx_success_all_process_failed.any())
        self.assertEqual(audit.orphan_processes.sum(), 1)

    def test_retry_attempts_not_mixed(self):
        t = tx(); t.loc[0, "retryCount"] = 1
        result, _ = hub_aware_summary(t, processes())
        self.assertTrue(result.loc[0, "no_process"])

    def test_group_keys_have_no_separator_collision(self):
        f = pd.DataFrame({"a": ["a / b", "a"], "b": ["c", "b / c"]})
        self.assertEqual(group_keys(f, ["a", "b"]).nunique(), 2)

    def test_freeze_uses_reference_only_and_abstains(self):
        f = tx(4); f["duration_ms"] = [10., 20., 30., 999999.]
        c = resolve_config({"quantile_min_support": {"0.99": 3, "0.999": 5}})
        profile = freeze_quantiles(f, pd.Series([True, True, True, False]), [], c)
        self.assertLess(profile.iloc[0]["q0.99"], 31)
        self.assertTrue(pd.isna(profile.iloc[0]["q0.999"]))
        result = score_frozen_quantiles(f, {"all": profile}, [("all", [])], .99)
        self.assertTrue(result.loc[3, "candidate"])
        result = score_frozen_quantiles(f, {"all": profile}, [("all", [])], .999)
        self.assertFalse(result.comparable.any())

    def test_cold_start_group_coverage(self):
        f = tx(5); f.loc[4, "interfaceId"] = "NEW"
        flags, _ = evaluate_rules(f, "transaction", resolve_config())
        split = pd.Series(["train", "train", "validation", "test", "test"])
        c = resolve_config({"min_group_train": 2, "min_group_validation": 1, "min_group_days": 2})
        summary, _ = grouping_profile(f, "transaction", split, flags, c)
        row = summary.loc[summary.grouping.eq("업무")].iloc[0]
        self.assertEqual(row.test_unseen_pct, 50)
        self.assertEqual(row.test_group_ready_pct, 50)

    def test_sql_primary_key_parsing(self):
        sql = "CREATE TABLE T (\n A VARCHAR2(5) NOT NULL,\n B NUMBER,\n PRIMARY KEY (A, B)\n);\nCOMMENT ON COLUMN T.A IS '아이디';"
        result = schema_audit(sql).set_index("column")
        self.assertTrue(result.loc["A", "primary_key"])
        self.assertTrue(result.loc["B", "primary_key"])
        self.assertEqual(result.loc["A", "meaning"], "아이디")


if __name__ == "__main__":
    unittest.main()
