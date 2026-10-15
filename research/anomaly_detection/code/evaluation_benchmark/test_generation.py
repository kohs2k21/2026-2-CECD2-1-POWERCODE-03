"""Boundary and reproducibility tests; no output dataset is modified."""
import json
from pathlib import Path
import sys
import unittest

import pandas as pd

from generate import Catalog, Case, SCENARIOS, make_history, generate_case
from validate import quantile_book, label_types
from prepare import ROOT
from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.data import prepare
from process_anomaly_pipeline.rules import evaluate_rules


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = json.loads((ROOT/"reports/evaluation_v2_work/inputs.json").read_text(encoding="utf-8"))
        cls.catalog = Catalog(cls.bundle)
        _,cls.history = make_history(20261010)
        cls.config = PipelineConfig.from_dict(cls.bundle["policy"]["config"])
        cls.book = quantile_book(cls.bundle["policy"])

    def check_tables(self,tables):
        for f,t in (("processes.csv","transactions_context.csv"),("normal_processes.csv","normal_transactions_context.csv")):
            p = prepare(pd.DataFrame(tables[f]))
            flags,context = evaluate_rules(p,self.config,transactions=pd.DataFrame(tables[t]),complete_context=True)
            flags["statistical_tail"],_ = self.book.evaluate(p)
            self.assertFalse(flags.fillna(True).any().any())
            self.assertTrue(context.context_state.eq("complete_context_declared").all())
        self.assertTrue(all(r["status"]=="S" for r in tables["processes.csv"]))

    def test_scenarios_and_hard(self):
        for scenario in (*SCENARIOS,"mixed_hard"):
            for i in range(9):
                with self.subTest(scenario=scenario,index=i):
                    tables = generate_case(self.catalog,scenario,i,20261010,self.history).export()
                    self.check_tables(tables)
                    self.assertEqual({e["scenario"] for e in tables["events.csv"]},set(SCENARIOS) if scenario=="mixed_hard" else {scenario})

    def test_reproducibility(self):
        a = Case(self.catalog,"mixed_hard",42,20261010,self.history).build().export()
        b = Case(self.catalog,"mixed_hard",42,20261010,self.history).build().export()
        self.assertEqual(a,b)

    def test_empty_row_labels_for_missing_input(self):
        self.assertEqual(label_types(pd.Series([float("nan")]*3)),set())
        self.assertEqual(label_types(pd.Series(["time_burst;input_stop",None])),{"time_burst","input_stop"})

    def test_strict_positive_delay(self):
        for scenario in ('individual_delay','group_delay'):
            for index in range(90):
                tables=generate_case(self.catalog,scenario,index,20261010,self.history).export()
                for row in tables['process_labels.csv']:
                    if row['row_is_anomaly']:
                        self.assertGreater(row['observed_duration_ms'],row['normal_duration_ms'])

    def test_negative_rule_controls(self):
        tables = Case(self.catalog,"individual_delay",1,20261010,self.history).build().export()
        p = pd.DataFrame(tables["processes.csv"])
        p.loc[0,"status"]="F"
        flags,_ = evaluate_rules(prepare(p),self.config)
        self.assertTrue(flags.loc[0,"status_failed"])
        p.loc[0,"status"]="S"
        p["totalCount"]=p.totalCount.astype(float)
        p.loc[0,"totalCount"]=-.5
        flags,_ = evaluate_rules(prepare(p),self.config)
        self.assertTrue(flags.loc[0,"invalid_count_domain"])
        t = pd.DataFrame(tables["transactions_context.csv"])
        t.loc[0,"processCount"] += 1
        flags,_ = evaluate_rules(prepare(pd.DataFrame(tables["processes.csv"])),self.config,transactions=t,complete_context=True)
        self.assertTrue(flags.process_count_mismatch.fillna(False).any())

    def test_strict_frozen_boundary_and_fallback(self):
        row = Case(self.catalog,"individual_delay",0,20261010,self.history).build().export()["normal_processes.csv"][0]
        row["adapterType"]="NEW.ADAPTER"
        cap = self.catalog.cap("NEW.ADAPTER","duration_ms")
        p = prepare([row])
        p.loc[0,"duration_ms"]=cap
        tail,details = self.book.evaluate(p)
        self.assertFalse(tail.iloc[0])
        self.assertEqual(details.p99_duration_ms_basis.iloc[0],"global")
        p.loc[0,"duration_ms"]=cap+.001
        tail,_ = self.book.evaluate(p)
        self.assertTrue(tail.iloc[0])


if __name__=="__main__":
    unittest.main()
