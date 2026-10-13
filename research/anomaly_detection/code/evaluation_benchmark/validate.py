"""Independent, streamed validation of exported CSVs with the actual pipeline rules."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "code"))
from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.data import PROCESS_COLUMNS, PROCESS_REQUIRED, TRANSACTION_COLUMNS, prepare
from process_anomaly_pipeline.quantiles import QuantileBook
from process_anomaly_pipeline.rules import RULE_NAMES, evaluate_rules
from generate import SCENARIOS
from prepare import sha256


def quantile_book(policy):
    thresholds,support = {},{}
    for row in policy["thresholds"]:
        group = row["group"]
        thresholds.setdefault(group,{})[(row["metric"],row["quantile"])] = row["threshold"]
        support.setdefault(group,{})[row["metric"]] = row["reference_rows"]
    return QuantileBook(thresholds,support,tuple(policy["config"]["quantiles"]),tuple(policy["config"]["quantile_columns"]))


def complete_batches(path,chunk=20_000):
    carry = None
    for frame in pd.read_csv(path,dtype="string",encoding="utf-8-sig",chunksize=chunk):
        if carry is not None:
            frame = pd.concat([carry,frame],ignore_index=True)
        last = frame.transactionId.iloc[-1]
        keep = frame.transactionId.ne(last)
        carry = frame.loc[~keep].copy()
        if keep.any():
            yield frame.loc[keep].reset_index(drop=True)
    if carry is not None and len(carry):
        yield carry.reset_index(drop=True)


def label_types(series):
    # A missing-input event has no abnormal observed rows. An all-empty CSV
    # label column is inferred as float by pandas, not as text.
    return set(series.astype("string").dropna().str.split(";").explode().dropna())


def verify_rules(directory,policy):
    config,book = PipelineConfig.from_dict(policy["config"]),quantile_book(policy)
    report = {}
    for filename,context_name in (("processes.csv","transactions_context.csv"),("normal_processes.csv","normal_transactions_context.csv")):
        parents = pd.read_csv(directory/context_name,dtype="string",encoding="utf-8-sig")
        assert list(parents.columns) == list(TRANSACTION_COLUMNS)
        assert parents.status.eq("S").all()
        assert not parents.duplicated(["transactionId","processHubId","retryCount"]).any()
        assert parents[["transactionId","processHubId","retryCount"]].notna().all().all()
        assert parents.processHubId.str.len().le(20).all()
        assert parents.interfaceId.isna().all() or parents.interfaceId.dropna().str.len().le(30).all()
        parents = parents.set_index("transactionId",drop=False)
        hits,unknown,exceed,rows,pk = Counter(),Counter(),Counter(),0,set()
        for raw in complete_batches(directory/filename):
            assert list(raw.columns) == list(PROCESS_COLUMNS)
            p = prepare(raw)
            t = parents.loc[parents.index.isin(raw.transactionId.unique())].reset_index(drop=True)
            flags,context = evaluate_rules(p,config,transactions=t,complete_context=True)
            flags["statistical_tail"],quantiles = book.evaluate(p)
            hits.update({col:int(flags[col].fillna(False).sum()) for col in RULE_NAMES})
            unknown.update({col:int(flags[col].isna().sum()) for col in RULE_NAMES})
            for col in quantiles:
                if col.endswith("_exceed"):
                    assert not quantiles[col].isna().any(),f"Unverifiable threshold: {col}"
                    exceed[col] += int(quantiles[col].sum())
            assert context.context_state.eq("complete_context_declared").all()
            assert p.status.eq("S").all() and p[list(PROCESS_REQUIRED)].notna().all().all()
            assert p[["totalCount","successCount","errorCount","retryCount"]].notna().all().all()
            assert p.duration_ms.ge(0).all()
            for col,length in {"transactionId":80,"processId":36,"dependProcessId":36,"processHubId":30,"adapterType":30,"channelId":20,"responseCode":10}.items():
                assert p[col].dropna().str.len().le(length).all(),f"SQL length: {col}"
            for tx,pid,retry in p[["transactionId","processId","retryCount"]].itertuples(index=False,name=None):
                key = (tx,pid,int(retry))
                assert key not in pk,"Duplicate exported Process PK"
                pk.add(key)
            # Dependencies are checked against the full execution, never partial chunks.
            ends = {(r.transactionId,r.processId):r.endTime for r in p.itertuples()}
            for r in p.itertuples():
                if pd.notna(r.dependProcessId):
                    key = (r.transactionId,r.dependProcessId)
                    assert key in ends,"Missing predecessor"
                    assert r.startTime >= ends[key],"Predecessor not completed"
            rows += len(p)
        assert not any(hits.values()),dict(hits)
        assert not any(unknown.values()),dict(unknown)
        assert not any(exceed.values()),dict(exceed)
        report[filename] = {"rows":rows,"unique_pk":len(pk),"rule_hits":dict(hits),"unknown_rule_flags":dict(unknown),"quantile_exceedances":dict(exceed)}
    return report


def validate(directory,only=None):
    root = json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    summary = pd.read_csv(directory/"scenario_summary.csv",encoding="utf-8-sig")
    assert set(summary.dataset) == set(root["datasets"])
    assert root["datasets"] == [*SCENARIOS,"mixed_hard"],"Release must contain all seven datasets"
    history = pd.read_csv(directory/"reference_history_windows.csv",encoding="utf-8-sig")
    assert pd.to_datetime(history.window_end).max() <= pd.Timestamp("2026-10-12")
    if only is not None and only not in root["datasets"]:
        raise ValueError("Unknown validation subset")
    report = {"passed":False,"coverage":"subset" if only else "all_seven_datasets","datasets":{},"untouched_source_and_model_hashes":True}
    for name in ([only] if only else root["datasets"]):
        folder = directory/name
        metadata = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
        expected = root["hard_cases"] if name=="mixed_hard" else root["cases_per_scenario"]
        cases = pd.read_csv(folder/"cases.csv",encoding="utf-8-sig")
        events = pd.read_csv(folder/"events.csv",encoding="utf-8-sig")
        assert len(cases)==expected and cases.case_id.nunique()==expected and cases.normal_case_id.nunique()==expected
        assert cases.unique_content_fingerprint.nunique()==expected,"ID-only diversity"
        assert cases.event_count.eq(6 if name=="mixed_hard" else 1).all()
        grouped = events.groupby("case_id").scenario.agg(set)
        assert set(grouped.index)==set(cases.case_id)
        expected_types = set(SCENARIOS) if name=="mixed_hard" else {name}
        assert all(v==expected_types for v in grouped)
        for event in events.itertuples():
            evidence = json.loads(event.evidence_json)
            assert pd.Timestamp(event.endTime)>pd.Timestamp(event.startTime)
            if event.scenario=="input_stop":
                assert event.removed_executions>0 and evidence["expected_executions"]==event.removed_executions
            elif event.scenario=="time_burst":
                assert event.added_executions>0 and evidence["observed_focus_rows"]>evidence["normal_focus_rows"]
            elif event.scenario=="gradual_degradation":
                v = evidence["increments"]
                assert max(x["increment_ms"] for x in v)>0
                ordered = sorted(v,key=lambda x:x["progress"])
                increments = [x["increment_ms"] for x in ordered]
                assert all(b>=a for a,b in zip(increments,increments[1:]))
            elif event.scenario in ("individual_delay","group_delay"):
                assert evidence["target_duration_ms"]>evidence["reference_profile"]["p95_ms"]
                assert event.affected_process_rows>0
            else:
                assert evidence["low_count_new_duration_ms"]>evidence["low_profile"]["p95_ms"]
        assert cases.observed_rows.sum()==metadata["files"]["processes.csv"]["rows"]
        assert cases.normal_rows.sum()==metadata["files"]["normal_processes.csv"]["rows"]
        # Full label inventory proves process-to-case mapping and source provenance are complete.
        labels = pd.read_csv(folder/"process_labels.csv",encoding="utf-8-sig")
        assert len(labels)==int(cases.observed_rows.sum())
        assert not labels.duplicated(["transactionId","processId","retryCount"]).any()
        assert labels.groupby("case_id").size().to_dict()==cases.set_index("case_id").observed_rows.to_dict()
        assert set(labels.case_id)==set(cases.case_id)
        assert label_types(labels.scenario_types).issubset(expected_types)
        delay = labels.scenario_types.astype('string').str.contains('individual_delay|group_delay',regex=True,na=False)
        assert labels.loc[delay,'observed_duration_ms'].gt(labels.loc[delay,'normal_duration_ms']).all(), 'A delay must increase its paired normal duration'
        label_case = labels.set_index(["transactionId","processId","retryCount"]).case_id.to_dict()
        for chunk in pd.read_csv(folder/"processes.csv",encoding="utf-8-sig",chunksize=100_000):
            assert all((tx,pid,int(retry)) in label_case for tx,pid,retry in chunk[["transactionId","processId","retryCount"]].itertuples(index=False,name=None))
        del labels,label_case
        windows = pd.read_csv(folder/"windows.csv",encoding="utf-8-sig")
        window_labels = pd.read_csv(folder/"window_labels.csv",encoding="utf-8-sig")
        keys = ["case_id","scope","window_start","window_end"]
        assert not windows.duplicated(keys).any()
        assert windows[keys].equals(window_labels[keys])
        assert windows.execution_arrivals.equals(window_labels.observed_execution_arrivals)
        assert set(windows.case_id)==set(cases.case_id)
        window_groups = window_labels.groupby(["case_id","scope"],sort=False)
        for event in events.loc[events.scenario.eq("input_stop")].itertuples():
            target = window_groups.get_group((event.case_id,"TARGET"))
            target = target.loc[target.event_ids.fillna("").str.contains(event.event_id,regex=False)]
            assert len(target)>0 and target.observed_execution_arrivals.eq(0).all()
            assert target.normal_execution_arrivals.sum()>0
        del windows,window_labels,window_groups
        actual = verify_rules(folder,root["frozen_policy"])
        report["datasets"][name] = {"anomaly_cases":len(cases),"normal_cases":len(cases),"events":len(events),"actual_rule_validation":actual}
        for filename,info in metadata["files"].items():
            assert sha256(folder/filename)==info["sha256"],f"CSV hash mismatch: {name}/{filename}"
        print(f"PASS {name}: {len(cases):,} cases; rule hits 0, unknown flags 0, thresholds passed",flush=True)
    for filename,info in root["files"].items():
        assert sha256(directory/filename)==info["sha256"]
    for entry in root["source_audit"]:
        assert sha256(ROOT/entry["process_file"])==entry["process_sha256"]
        assert sha256(ROOT/entry["transaction_file"])==entry["transaction_sha256"]
    for filename,expected in root["untouched_hashes"].items():
        assert sha256(ROOT/filename)==expected,f"Out-of-scope file changed: {filename}"
    report["anomaly_cases"] = sum(d["anomaly_cases"] for d in report["datasets"].values())
    report["normal_cases"] = report["anomaly_cases"]
    report["passed"] = True
    destination = directory/only/"validation.json" if only else directory/"validation.json"
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory",type=Path)
    parser.add_argument("--only",choices=(*SCENARIOS,"mixed_hard"))
    args = parser.parse_args()
    warnings.filterwarnings("ignore",category=FutureWarning,module="process_anomaly_pipeline.rules")
    result = validate(args.directory,args.only)
    print(f"{result['coverage']} PASSED: {result['anomaly_cases']:,} anomaly cases + {result['normal_cases']:,} normal controls",flush=True)
