"""Independent, paired ESB benchmark cases; no model-score-based selection.

Writes bounded NDJSON intermediates. author_csv.mjs authors the final CSVs.
Case IDs and oracle fields never enter the 14-column serving inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

import numpy as np

SCENARIOS = ("individual_delay", "group_delay", "column_relation", "time_burst", "input_stop", "gradual_degradation")


class InfeasibleCase(ValueError):
    """Resample a case rather than weaken its anomaly definition or rule gate."""
NAMES = ("개별 지연", "업무 집단 내 지연", "컬럼 관계 이상", "시간대 폭주", "입력 중단", "점진적 악화")
PROCESS = ["transactionId","processId","dependProcessId","processHubId","adapterType","channelId","status","startTime","endTime","successCount","errorCount","responseCode","totalCount","retryCount"]
TRANSACTION = ["transactionId","processHubId","retryCount","interfaceId","status","processCount","startTime","endTime"]
TABLES = {
    "processes.csv": PROCESS,
    "normal_processes.csv": PROCESS,
    "transactions_context.csv": TRANSACTION,
    "normal_transactions_context.csv": TRANSACTION,
    "cases.csv": ["case_id","normal_case_id","case_seed","scenario_types","severity","episode_start","episode_end","target_adapter","target_process_id","observed_rows","normal_rows","observed_executions","normal_executions","event_count","unique_content_fingerprint"],
    "process_labels.csv": ["case_id","transactionId","processId","retryCount","source_template_id","source_period","source_file","source_row","source_transactionId","source_duration_ms","normal_duration_ms","observed_duration_ms","row_is_anomaly","collective_context_is_anomaly","scenario_types","event_ids","changed_fields"],
    "events.csv": ["case_id","event_id","scenario","severity","startTime","endTime","target_adapter","target_process_id","target_selector_json","label_unit","affected_process_rows","added_executions","removed_executions","evidence_json"],
    "windows.csv": ["case_id","scope","adapterType","processId","window_start","window_end","execution_arrivals","process_rows_started","completed_rows","duration_p50_ms","duration_p95_ms","history_median_arrivals","history_p95_arrivals","history_support_windows"],
    "window_labels.csv": ["case_id","scope","window_start","window_end","window_is_anomaly","scenario_types","event_ids","normal_execution_arrivals","observed_execution_arrivals"],
}


def stable_seed(seed, *parts):
    return int.from_bytes(hashlib.sha256((str(seed)+"|"+"|".join(map(str,parts))).encode()).digest()[:8], "big")


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec="milliseconds")


def stamp(value):
    # All timestamps are KST-naive calendar values; never use the host timezone.
    return int((datetime.fromisoformat(value)-datetime(1970,1,1)).total_seconds()*1000)


def time_text(ms):
    return (datetime(1970,1,1)+timedelta(milliseconds=int(ms))).isoformat(timespec="milliseconds")


def percentile_sorted(values, quantile):
    position = (len(values)-1)*quantile
    low = int(position)
    high = min(low+1,len(values)-1)
    return float(values[low]+(values[high]-values[low])*(position-low))


class StreamTables:
    def __init__(self, directory, schemas, chunk=5000):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / "tables.ndjson").open("w", encoding="utf-8", newline="\n")
        self.schemas, self.chunk = schemas, chunk
        self.buffer, self.counts = defaultdict(list), Counter()

    def add(self, name, records):
        columns = self.schemas[name]
        for record in records:
            row = [record.get(c) for c in columns]
            self.buffer[name].append(row)
            self.counts[name] += 1
            if len(self.buffer[name]) >= self.chunk:
                self.flush(name)

    def flush(self, name):
        if self.buffer[name]:
            self.file.write(json.dumps({"filename":name,"columns":self.schemas[name],"rows":self.buffer[name]}, ensure_ascii=False, allow_nan=False, separators=(",",":"))+"\n")
            self.buffer[name].clear()

    def close(self, metadata):
        for name in self.schemas:
            self.flush(name)
            if name not in self.counts:
                self.file.write(json.dumps({"filename":name,"columns":self.schemas[name],"rows":[]})+"\n")
        self.file.close()
        metadata["table_rows"] = dict(self.counts)
        (self.directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


class Catalog:
    def __init__(self, bundle):
        self.templates, self.policy = bundle["templates"], bundle["policy"]
        self.values = {(r["group"],r["metric"],r["quantile"]):r["threshold"] for r in self.policy["thresholds"]}
        self.groups, coarse, adapters = defaultdict(list), defaultdict(list), defaultdict(list)
        self.adapter_templates = defaultdict(set)
        for ti, template in enumerate(self.templates):
            for ri, row in enumerate(template["rows"]):
                key = (row["adapterType"],row["processId"],row["totalCount"])
                self.groups[key].append((ti,ri))
                coarse[(key[0],key[2])].append(row["duration_ms"])
                adapters[key[0]].append(row["duration_ms"])
                self.adapter_templates[key[0]].add(ti)
        self.adapter_templates = {a:sorted(v) for a,v in sorted(self.adapter_templates.items())}
        self.adapter_names = sorted(self.adapter_templates)
        self.profiles, self.central, self.delay_keys = {}, {}, []
        for key, refs in sorted(self.groups.items()):
            durations = [self.templates[ti]["rows"][ri]["duration_ms"] for ti,ri in refs]
            basis = "adapter_process_count"
            reference = durations
            if len(reference) < 30:
                reference, basis = coarse[(key[0],key[2])], "adapter_count"
            if len(reference) < 30:
                reference, basis = adapters[key[0]], "adapter"
            if len(reference) < 30:
                continue
            q = np.quantile(reference,[.05,.25,.5,.75,.95])
            p = {"adapterType":key[0],"processId":key[1],"totalCount":key[2],"basis":basis,"reference_rows":len(reference),
                 "p05_ms":float(q[0]),"p25_ms":float(q[1]),"p50_ms":float(q[2]),"p75_ms":float(q[3]),"p95_ms":float(q[4]),
                 "frozen_duration_cap_ms":self.cap(key[0],"duration_ms")}
            self.profiles[key] = p
            central = [(ti,ri) for ti,ri in refs if q[0] <= self.templates[ti]["rows"][ri]["duration_ms"] <= q[3]]
            self.central[key] = central or refs
            if p["frozen_duration_cap_ms"] - p["p95_ms"] >= 10 and p["p50_ms"] >= 5:
                self.delay_keys.append(key)
        self.relations = []
        by_process = defaultdict(list)
        for k,p in self.profiles.items():
            if p["basis"] == "adapter_process_count" and len(self.groups[k]) >= 30:
                # A Process ID often fixes its count regime. Compare supported
                # count regimes within an Adapter instead of inventing unseen
                # counts for that Process ID. Record both workflow targets.
                by_process[k[0]].append(k)
        for _, keys in sorted(by_process.items()):
            for low in keys:
                for high in keys:
                    lp,hp = self.profiles[low],self.profiles[high]
                    if low[2] < high[2] and hp["p50_ms"] > lp["p95_ms"] + 10 and lp["p50_ms"] > 0:
                        self.relations.append((low,high))
        self.relations.sort()
        if not self.delay_keys or not self.relations:
            raise ValueError("Insufficient supported conditional-delay or count-duration relation groups")
        # Adapter-balanced group selection, not row-frequency-weighted sampling.
        self.delay_by_adapter = defaultdict(list)
        self.relation_by_adapter = defaultdict(list)
        for key in self.delay_keys:
            self.delay_by_adapter[key[0]].append(key)
        for pair in self.relations:
            if pair[0] in self.delay_keys:
                self.relation_by_adapter[pair[0][0]].append(pair)
        if not self.relation_by_adapter:
            raise ValueError("No Hard case can support all six types under the frozen rules")

    def cap(self, adapter, column):
        values = []
        for q in sorted({r["quantile"] for r in self.policy["thresholds"]}):
            value = self.values.get((adapter,column,q), self.values.get(("GLOBAL",column,q)))
            if value is None:
                raise ValueError(f"Missing frozen bound for {adapter}/{column}/{q}")
            values.append(value)
        return float(min(values))

    def choose_key(self, i, rng, relation=False):
        options = self.relation_by_adapter if relation else self.delay_by_adapter
        adapter = sorted(options)[i % len(options)]
        return options[adapter][int(rng.integers(len(options[adapter])))]

    def pick(self, key, rng):
        refs = self.central.get(key, self.groups[key])
        return refs[int(rng.integers(len(refs)))]


class Case:
    def __init__(self, catalog, scenario, index, seed, history, attempt=0):
        self.catalog, self.scenario, self.index = catalog, scenario, index
        self.seed = stable_seed(seed,scenario,index) if attempt==0 else stable_seed(seed,scenario,index,"retry",attempt)
        self.rng = np.random.default_rng(self.seed)
        self.case_id = f"C{stable_seed(seed,scenario,index,'id'):016x}"
        self.hard = scenario == "mixed_hard"
        self.severity = "hard" if self.hard else ("weak","medium","strong")[index % 3]
        self.level = index % 3
        self.factor = (.2,.45,.75)[self.level]
        relation = scenario in ("column_relation","mixed_hard")
        chosen = catalog.choose_key(index,self.rng,relation)
        self.pair = chosen if relation else None
        self.key = chosen[0] if relation else chosen
        if scenario in ("time_burst","input_stop"):
            # Sparse Adapters are valid replay targets, but do not get invented conditional quantiles.
            adapter = catalog.adapter_names[index % len(catalog.adapter_names)]
            ti = catalog.adapter_templates[adapter][int(self.rng.integers(len(catalog.adapter_templates[adapter])))]
            ri = next(j for j,r in enumerate(catalog.templates[ti]["rows"]) if r["adapterType"] == adapter)
            r = catalog.templates[ti]["rows"][ri]
            self.key, self.fixed_ref = (adapter,r["processId"],r["totalCount"]),(ti,ri)
        else:
            self.fixed_ref = None
        day = index % 14
        hour = int(self.rng.integers(0,5) if scenario == "time_burst" or self.hard else self.rng.integers(0,23))
        minute = int(self.rng.integers(0,20))
        self.start = stamp((datetime(2026,10,12)+timedelta(days=day,hours=hour,minutes=minute)).isoformat())
        self.history = history
        if not hasattr(catalog,"history_stats"):
            catalog.history_stats = {k:(float(np.median(v)),float(np.quantile(v,.95)),len(v)) for k,v in history.items()}
        self.history_stats = catalog.history_stats
        self.normal, self.observed, self.normal_meta, self.meta = {}, {}, {}, {}
        self.events, self.labels = [], defaultdict(list)
        self.serial = 0

    def clone(self, key, minute, normal=True, observed=True, ref=None):
        ti, ri = ref or (self.fixed_ref if key == self.key and self.fixed_ref else self.catalog.pick(key,self.rng))
        t = self.catalog.templates[ti]
        self.serial += 1
        tx = f"E{stable_seed(self.seed,self.serial,'execution'):016x}"
        rows = []
        ended = {}
        gap = int(self.rng.integers(2,26))
        for src in t["rows"]:
            row = {c:src.get(c) for c in PROCESS}
            duration = max(0,int(math.floor(src["duration_ms"])))
            begin = ended[src["dependProcessId"]] + gap if src["dependProcessId"] is not None else 0
            end = begin + duration
            ended[src["processId"]] = end
            row.update(transactionId=tx,status="S",startTime=begin,endTime=end)
            rows.append(row)
        anchor = self.start + int(minute*60_000) + int(self.rng.integers(25000,45000))
        shift = anchor - rows[ri]["startTime"]
        for row in rows:
            row["startTime"] += shift
            row["endTime"] += shift
        metadata = {"template":t,"ti":ti,"focus":ri,"gap":gap,"original_start":{r["processId"]:r["startTime"] for r in rows}}
        if normal:
            self.normal[tx],self.normal_meta[tx] = deepcopy(rows),metadata
        if observed:
            self.observed[tx],self.meta[tx] = deepcopy(rows),metadata
        return tx, rows[ri]["processId"]

    def background(self, minutes):
        for slot, minute in enumerate(minutes):
            adapter = self.catalog.adapter_names[(self.index+slot) % len(self.catalog.adapter_names)]
            candidates = self.catalog.adapter_templates[adapter]
            start = int(self.rng.integers(len(candidates)))
            for ti in candidates[start:]+candidates[:start]:
                t = self.catalog.templates[ti]
                if any((r["adapterType"],r["processId"]) == self.key[:2] for r in t["rows"]):
                    continue
                ri = next(j for j,r in enumerate(t["rows"]) if r["adapterType"] == adapter)
                row = t["rows"][ri]
                self.clone((adapter,row["processId"],row["totalCount"]),minute,ref=(ti,ri))
                break

    def baseline(self, minutes):
        for minute in range(minutes):
            dt = datetime(1970,1,1)+timedelta(milliseconds=self.start+minute*60_000)
            # Draw only from a prior synthetic normal history with the same hour/day type.
            candidates = self.history[(dt.weekday()>=5,dt.hour)]
            n = int(candidates[int(self.rng.integers(len(candidates)))])
            for _ in range(n):
                self.clone(self.key,minute)
        self.background(range(4,minutes,10))

    def focus_rows(self, begin, end):
        return [(tx,r["processId"]) for tx,rows in self.observed.items() for r in rows
                if (r["adapterType"],r["processId"]) == self.key[:2] and begin <= r["startTime"] < end]

    def set_duration(self, tx, pid, duration):
        rows = self.observed[tx]
        duration = max(1,int(math.floor(duration)))
        changed, end = {}, {}
        gap = self.meta[tx]["gap"]
        for r in rows:
            old_start, old_duration = r["startTime"],r["endTime"]-r["startTime"]
            if r["dependProcessId"] is not None:
                r["startTime"] = max(r["startTime"],end[r["dependProcessId"]]+gap)
            r["endTime"] = r["startTime"] + (duration if r["processId"] == pid else old_duration)
            end[r["processId"]] = r["endTime"]
            if old_start != r["startTime"]:
                changed[r["processId"]] = True
        return changed

    def event(self, scenario, begin, end, affected, evidence, added=0, removed=0):
        eid = f"{self.case_id}-E{len(self.events)+1}"
        point = scenario in ("individual_delay","group_delay","column_relation")
        if point and affected:
            # Keep completion-time detection inside the annotated event, too.
            end = max(end,max(r['endTime'] for tx,pid in affected for r in self.observed[tx] if r['processId']==pid)+1)
        for tx,pid in affected:
            self.labels[(tx,pid)].append((scenario,eid,point))
        process_ids = sorted({pid for tx,pid in affected}) or [self.key[1]]
        self.events.append({"case_id":self.case_id,"event_id":eid,"scenario":scenario,"severity":self.severity,
                            "startTime":time_text(begin),"endTime":time_text(end),"target_adapter":self.key[0],
                            "target_process_id":self.key[1],"target_selector_json":json.dumps({"adapterType":self.key[0],"processIds":process_ids}),
                            "label_unit":"process" if point else "window",
                            "affected_process_rows":len(affected),"added_executions":added,"removed_executions":removed,
                            "evidence_json":json.dumps(evidence,ensure_ascii=False,separators=(",",":"))})

    def delay(self, scenario, begin_minute, end_minute):
        begin,end = self.start+begin_minute*60_000,self.start+end_minute*60_000
        affected = self.focus_rows(begin,end)
        if not affected:
            affected = [self.clone(self.key,begin_minute+.3)]
        if scenario == "individual_delay":
            affected = [affected[int(self.rng.integers(len(affected)))]]
        p = self.catalog.profiles[self.key]
        fraction = float(self.rng.uniform(.02,.12)) if self.hard else float(self.rng.uniform(max(.05,self.factor-.08),self.factor+.08))
        value = p["p95_ms"] + fraction*(p["frozen_duration_cap_ms"]-p["p95_ms"]-1)
        baselines = [next(r for r in self.observed[tx] if r['processId']==pid)['endTime']-
                     next(r for r in self.observed[tx] if r['processId']==pid)['startTime'] for tx,pid in affected]
        if any(int(value)<=baseline for baseline in baselines):
            raise InfeasibleCase("Delay must strictly increase every targeted normal duration")
        for tx,pid in affected:
            self.set_duration(tx,pid,value)
        if not all(next(r for r in self.observed[tx] if r["processId"]==pid)["endTime"] - next(r for r in self.observed[tx] if r["processId"]==pid)["startTime"] > p["p95_ms"] for tx,pid in affected):
            raise ValueError("Rounded delay does not exceed conditional P95")
        self.event(scenario,begin,end,affected,{"reference_profile":p,"target_duration_ms":int(value),"headroom_fraction":fraction})

    def relation(self, minute):
        low,high = self.pair
        targets = [self.clone(low,minute+.2),self.clone(high,minute+.6)]
        lp,hp = self.catalog.profiles[low],self.catalog.profiles[high]
        # Counts, status and arithmetic remain untouched; change only their dependency with time.
        high_duration = hp["p50_ms"] if not self.hard else lp["p95_ms"]+float(self.rng.uniform(.08,.25))*(hp["p50_ms"]-lp["p95_ms"])
        high_duration = min(high_duration,self.catalog.cap(low[0],"duration_ms")-1)
        low_duration = lp["p50_ms"]
        self.set_duration(*targets[0],high_duration)
        self.set_duration(*targets[1],low_duration)
        if int(high_duration) <= lp["p95_ms"]:
            raise ValueError("Relation injection has no conditional deviation")
        self.event("column_relation",self.start+minute*60_000,self.start+(minute+2)*60_000,targets,
                   {"low_count":low[2],"high_count":high[2],"low_profile":lp,"high_profile":hp,
                    "low_count_new_duration_ms":int(high_duration),"high_count_new_duration_ms":int(low_duration),
                    "reference_scope":"supported count regimes within Adapter, possibly different Process IDs",
                    "low_process_id":low[1],"high_process_id":high[1],
                    "mechanism":"break count-duration dependence; preserve total=success+error"})

    def burst(self, minute, length):
        begin,end = self.start+minute*60_000,self.start+(minute+length)*60_000
        original = self.focus_rows(begin,end)
        if not original:
            original = [self.clone(self.key,minute+.2)]
        factor = float(self.rng.uniform(1.2,2.0)) if self.hard else float(self.rng.uniform(2,5))
        extra = max(1,int(math.ceil(len(original)*(factor-1))))
        shape = ("pulse","sustained","two_pulses")[self.index % 3]
        added = []
        for j in range(extra):
            if shape == "pulse":
                at = minute + self.rng.uniform(0,min(length,2))
            elif shape == "two_pulses":
                at = minute + (0 if j%2==0 else max(0,length-2)) + self.rng.uniform(0,min(2,length))
            else:
                at = minute+self.rng.uniform(0,length)
            # clone adds up to 45 s jitter: do not spill extra arrivals beyond
            # the labelled burst interval and leave them falsely unlabelled.
            at = min(float(at),minute+length-.8)
            added.append(self.clone(self.key,at,normal=False))
        actual = self.focus_rows(begin,end)
        self.event("time_burst",begin,end,actual,{"normal_focus_rows":len(original),"observed_focus_rows":len(actual),
                   "requested_factor":factor,"actual_factor":len(actual)/len(original),"shape":shape},added=extra)

    def stop(self, minute, length):
        begin,end = self.start+minute*60_000,self.start+(minute+length)*60_000
        targets = self.focus_rows(begin,end)
        if not targets:
            targets = [self.clone(self.key,minute+.25)]
        removed = sorted({tx for tx,pid in targets})
        missing_rows = sum(len(self.observed[tx]) for tx in removed)
        for tx in removed:
            del self.observed[tx]
            del self.meta[tx]
        self.event("input_stop",begin,end,[],{"expected_executions":len(removed),"missing_process_rows":missing_rows,
                   "missing_transaction_ids":removed,"collector_healthy_assumption":True,
                   "mechanism":"remove whole workflow and observed parent, not intermediate stages"},removed=len(removed))

    def trend(self, minute, length):
        begin,end = self.start+minute*60_000,self.start+(minute+length)*60_000
        targets = self.focus_rows(begin,end)
        if len(targets) < 3:
            targets += [self.clone(self.key,minute+k+.2) for k in (0,length//2,length-1)]
        p = self.catalog.profiles[self.key]
        actual_max = max(r['endTime']-r['startTime'] for tx,pid in targets for r in self.observed[tx] if r['processId']==pid)
        increment = min(p["p50_ms"]*((.05,.1,.2)[self.level] if self.hard else (.3,.6,1.0)[self.level]),
                        p["frozen_duration_cap_ms"]-p["p75_ms"]-2,
                        p["frozen_duration_cap_ms"]-actual_max-2)
        if increment < 1:
            raise InfeasibleCase("No feasible actual trend headroom")
        shape = ("linear","stairs","smooth")[self.index % 3]
        values = []
        for tx,pid in targets:
            row = next(r for r in self.observed[tx] if r["processId"] == pid)
            progress = max(0,min(1,(row["startTime"]-begin)/max(1,length*60_000-1)))
            if shape == "stairs":
                progress = math.floor(progress*5)/4
                progress = min(1,progress)
            elif shape == "smooth":
                progress = progress*progress*(3-2*progress)
            baseline = row["endTime"]-row["startTime"]
            value = baseline+progress*increment
            self.set_duration(tx,pid,value)
            values.append({"transactionId":tx,"normal_duration_ms":baseline,"increment_ms":int(value)-baseline,"progress":progress})
        if max(v["increment_ms"] for v in values) <= 0:
            raise ValueError("Trend disappeared after integer rounding")
        self.event("gradual_degradation",begin,end,targets,{"reference_profile":p,"max_increment_ms":increment,
                   "shape":shape,"increments":values,"monotonic_latent_change":True})

    def build(self):
        if self.hard:
            self.baseline(90)
            self.delay("individual_delay",10,12)
            self.delay("group_delay",18,25)
            self.relation(30)
            self.burst(38,7)
            self.stop(50,int(self.rng.integers(1,4)))
            self.trend(58,20)
        elif self.scenario == "individual_delay":
            self.clone(self.key,2)
            self.background((0,4))
            self.delay(self.scenario,2,3)
        elif self.scenario == "column_relation":
            self.background((0,4))
            self.relation(2)
        elif self.scenario == "group_delay":
            self.baseline(36)
            self.delay(self.scenario,12,23)
        elif self.scenario == "time_burst":
            self.baseline(40)
            self.burst(12,(5,8,12)[self.level])
        elif self.scenario == "input_stop":
            self.baseline(50)
            self.stop(14,(5,10,20)[self.level])
        elif self.scenario == "gradual_degradation":
            self.baseline(60)
            self.trend(12,(20,30,40)[self.level])
        assert {e["scenario"] for e in self.events} == (set(SCENARIOS) if self.hard else {self.scenario})
        return self

    def export(self):
        out = {name:[] for name in TABLES}
        all_rows = [r for flows in (self.normal,self.observed) for rows in flows.values() for r in rows]
        first = min(r["startTime"] for r in all_rows)//60_000*60_000
        last = (max(r["endTime"] for r in all_rows)//60_000+2)*60_000
        for flows,metadata,filename,parent_filename in (
            (self.normal,self.normal_meta,"normal_processes.csv","normal_transactions_context.csv"),
            (self.observed,self.meta,"processes.csv","transactions_context.csv")):
            for tx,rows in flows.items():
                template = metadata[tx]["template"]
                for row in rows:
                    item = dict(row)
                    item["startTime"],item["endTime"] = time_text(row["startTime"]),time_text(row["endTime"])
                    out[filename].append(item)
                for original_parent in template["parents"]:
                    children = [r for r in rows if r["processHubId"] == original_parent["processHubId"]]
                    if not children:
                        continue
                    parent = {c:original_parent.get(c) for c in TRANSACTION}
                    parent.update(transactionId=tx,status="S",retryCount=0,processCount=int(original_parent["processCount"]),
                                  startTime=time_text(min(r["startTime"] for r in rows)),
                                  endTime=time_text(max(r["endTime"] for r in rows)))
                    assert parent["processCount"] == len({r["processId"] for r in children})
                    out[parent_filename].append(parent)
                if filename == "processes.csv":
                    for ri,row in enumerate(rows):
                        source = template["rows"][ri]
                        marks = self.labels[(tx,row["processId"])]
                        normal = next((r for r in self.normal.get(tx,[]) if r["processId"] == row["processId"]),None)
                        changed = []
                        if normal is None:
                            changed.append("added_execution")
                        else:
                            changed = [c for c in ("startTime","endTime") if row[c] != normal[c]]
                        out["process_labels.csv"].append({"case_id":self.case_id,"transactionId":tx,"processId":row["processId"],"retryCount":0,
                            "source_template_id":template["template_id"],"source_period":template["source_period"],"source_file":template["source_file"],
                            "source_row":source["source_row"],"source_transactionId":template["source_transactionId"],"source_duration_ms":source["duration_ms"],
                            "normal_duration_ms":normal["endTime"]-normal["startTime"] if normal else None,
                            "observed_duration_ms":row["endTime"]-row["startTime"],"row_is_anomaly":int(any(m[2] for m in marks)),
                            "collective_context_is_anomaly":int(any(not m[2] for m in marks)),"scenario_types":";".join(sorted({m[0] for m in marks})),
                            "event_ids":";".join(sorted({m[1] for m in marks})),"changed_fields":";".join(changed)})
        out["events.csv"] = self.events
        # Window inputs use only observed, completed rows and prior history. Counterfactuals are oracle-only.
        for scope in ("TARGET","ALL"):
            def selected(flows):
                return [r for rows in flows.values() for r in rows if scope == "ALL" or (r["adapterType"],r["processId"]) == self.key[:2]]
            observed,normal = selected(self.observed),selected(self.normal)
            started_bins,normal_bins,done_bins = defaultdict(list),defaultdict(list),defaultdict(list)
            for r in observed:
                started_bins[r['startTime']//60_000*60_000].append(r)
                done_bins[r['endTime']//60_000*60_000].append(r['endTime']-r['startTime'])
            for r in normal:
                normal_bins[r['startTime']//60_000*60_000].append(r)
            arrivals,normal_arrivals = Counter(),Counter()
            for records,target in ((observed,arrivals),(normal,normal_arrivals)):
                earliest = {}
                for r in records:
                    tx = r['transactionId']
                    earliest[tx] = min(earliest.get(tx,r['startTime']),r['startTime'])
                target.update(v//60_000*60_000 for v in earliest.values())
            for begin in range(first,last,60_000):
                end = begin+60_000
                started,done = started_bins[begin],done_bins[begin]
                done.sort()
                dt = datetime(1970,1,1)+timedelta(milliseconds=begin)
                past_median,past_p95,past_support = self.history_stats[(dt.weekday()>=5,dt.hour)]
                events = [e for e in self.events if stamp(e["startTime"]) < end and stamp(e["endTime"]) > begin]
                out["windows.csv"].append({"case_id":self.case_id,"scope":scope,"adapterType":self.key[0] if scope=="TARGET" else None,
                    "processId":self.key[1] if scope=="TARGET" else None,"window_start":time_text(begin),"window_end":time_text(end),
                    "execution_arrivals":arrivals[begin],"process_rows_started":len(started),"completed_rows":len(done),
                    "duration_p50_ms":percentile_sorted(done,.5) if done else None,"duration_p95_ms":percentile_sorted(done,.95) if done else None,
                    "history_median_arrivals":past_median if scope=="TARGET" else None,
                    "history_p95_arrivals":past_p95 if scope=="TARGET" else None,
                    "history_support_windows":past_support if scope=="TARGET" else 0})
                out["window_labels.csv"].append({"case_id":self.case_id,"scope":scope,"window_start":time_text(begin),"window_end":time_text(end),
                    "window_is_anomaly":int(bool(events)),"scenario_types":";".join(sorted({e["scenario"] for e in events})),
                    "event_ids":";".join(e["event_id"] for e in events),"normal_execution_arrivals":normal_arrivals[begin],
                    "observed_execution_arrivals":arrivals[begin]})
        fingerprint_records = []
        for row in out["processes.csv"]:
            fingerprint_records.append([row["adapterType"],row["processId"],row["totalCount"],
                                        stamp(row["startTime"])-first,stamp(row["endTime"])-stamp(row["startTime"])])
        fingerprint = hashlib.sha256(json.dumps(sorted(fingerprint_records),separators=(",",":")).encode()).hexdigest()
        out["cases.csv"] = [{"case_id":self.case_id,"normal_case_id":self.case_id+"-NORMAL","case_seed":str(self.seed),
            "scenario_types":";".join(sorted({e["scenario"] for e in self.events})),"severity":self.severity,
            "episode_start":time_text(first),"episode_end":time_text(last),"target_adapter":self.key[0],"target_process_id":self.key[1],
            "observed_rows":len(out["processes.csv"]),"normal_rows":len(out["normal_processes.csv"]),"observed_executions":len(self.observed),
            "normal_executions":len(self.normal),"event_count":len(self.events),"unique_content_fingerprint":fingerprint}]
        return out


def generate_case(catalog,dataset,index,seed,history):
    for attempt in range(128):
        try:
            case = Case(catalog,dataset,index,seed,history,attempt).build()
            for rows in case.observed.values():
                for row in rows:
                    if any(value>catalog.cap(row['adapterType'],metric) for metric,value in
                           (('duration_ms',row['endTime']-row['startTime']),('totalCount',row['totalCount']),('retryCount',row['retryCount']))):
                        raise InfeasibleCase("Generated row exceeds a frozen rule bound")
            return case
        except InfeasibleCase:
            continue
    raise ValueError(f"No valid anomaly case after 128 deterministic attempts: {dataset}/{index}")


def make_history(seed):
    rng = np.random.default_rng(stable_seed(seed,"normal_history"))
    records,lookup = [],defaultdict(list)
    for day in range(14):
        for minute in range(1440):
            dt = datetime(2026,9,28)+timedelta(days=day,minutes=minute)
            rate = 1.1 if dt.hour < 6 else (1.8 if 9 <= dt.hour < 18 else 1.4)
            if dt.weekday() >= 5:
                rate *= .8
            count = int(min(4,rng.poisson(rate)))
            records.append({"window_start":dt.isoformat(timespec="milliseconds"),"window_end":(dt+timedelta(minutes=1)).isoformat(timespec="milliseconds"),
                            "day_type":"weekend" if dt.weekday()>=5 else "weekday","hour":dt.hour,"execution_arrivals":count,
                            "origin":"declared_synthetic_normal_history"})
            lookup[(dt.weekday()>=5,dt.hour)].append(count)
    return records,lookup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--cases-per-scenario",type=int,default=5000)
    parser.add_argument("--hard-cases",type=int,default=1000)
    parser.add_argument("--seed",type=int,default=20261010)
    parser.add_argument("--only",choices=(*SCENARIOS,"mixed_hard"))
    args = parser.parse_args()
    if args.cases_per_scenario < 1 or args.hard_cases < 1:
        parser.error("Case counts must be positive; counts are never silently reduced")
    bundle = json.loads(args.inputs.read_text(encoding="utf-8"))
    catalog = Catalog(bundle)
    history,lookup = make_history(args.seed)
    root_schemas = {
        "reference_history_windows.csv":list(history[0]),
        "reference_profiles.csv":list(next(iter(catalog.profiles.values()))),
        "adapter_source_coverage.csv":["period","adapterType","prototype_rows"],
        "scenario_summary.csv":["dataset","anomaly_cases","normal_cases","process_rows","normal_process_rows","event_count","target_adapters","unique_content_cases"],
    }
    root = StreamTables(args.output,root_schemas)
    root.add("reference_history_windows.csv",history)
    root.add("reference_profiles.csv",list(catalog.profiles.values()))
    root.add("adapter_source_coverage.csv",bundle["eligible_adapter_coverage"])
    datasets = (args.only,) if args.only else (*SCENARIOS,"mixed_hard")
    for dataset in datasets:
        n = args.hard_cases if dataset=="mixed_hard" else args.cases_per_scenario
        writer = StreamTables(args.output / dataset,TABLES)
        coverage,periods,fingerprints,event_counts = Counter(),Counter(),set(),Counter()
        for i in range(n):
            case = generate_case(catalog,dataset,i,args.seed,lookup)
            tables = case.export()
            fp = tables["cases.csv"][0]["unique_content_fingerprint"]
            if fp in fingerprints:
                raise ValueError(f"ID-only duplicate content in {dataset} case {i}")
            fingerprints.add(fp)
            for filename,rows in tables.items():
                writer.add(filename,rows)
            coverage[case.key[0]] += 1
            for rows in case.observed.values():
                for row in rows:
                    event_counts["adapter:"+row["adapterType"]] += 1
            periods.update(r["source_period"] for r in tables["process_labels.csv"])
            event_counts.update(e["scenario"] for e in case.events)
            if (i+1)%250 == 0 or i+1==n:
                print(f"{dataset}: {i+1:,}/{n:,} cases; {writer.counts['processes.csv']:,} Process rows",flush=True)
        meta = {"benchmark_version":"esb-process-synthetic-v2","dataset":dataset,"anomaly_cases":n,"normal_cases":n,
                "seed":args.seed,"timezone":"Asia/Seoul","target_adapter_case_counts":dict(coverage),"source_period_process_rows":dict(periods),
                "event_and_adapter_counts":dict(event_counts),"unique_content_cases":len(fingerprints),"all_statuses":["S"],
                "rule_policy_sha256":bundle["policy"]["artifact_sha256"],"hard_selection":"conditional/contextual, never IF scores"}
        writer.close(meta)
        root.add("scenario_summary.csv",[{"dataset":dataset,"anomaly_cases":n,"normal_cases":n,
            "process_rows":writer.counts["processes.csv"],"normal_process_rows":writer.counts["normal_processes.csv"],
            "event_count":writer.counts["events.csv"],"target_adapters":len(coverage),"unique_content_cases":len(fingerprints)}])
    root.close({"benchmark_version":"esb-process-synthetic-v2","datasets":list(datasets),"seed":args.seed,
                "cases_per_scenario":args.cases_per_scenario,"hard_cases":args.hard_cases,
                "source_audit":bundle["source_audit"],"frozen_policy":bundle["policy"],"untouched_hashes":bundle["untouched_hashes"],
                "excluded_workflow_reasons":bundle["excluded_workflow_reasons"],"source_sampling":bundle["sampling"],
                "eligible_adapters":catalog.adapter_names,"delay_supported_adapters":sorted(catalog.delay_by_adapter),
                "relation_supported_adapters":sorted(catalog.relation_by_adapter),
                "history_assumption":"14 prior days of bounded, synthetic normal arrivals, not original collection gaps or claimed production traffic"})


if __name__ == "__main__":
    main()
