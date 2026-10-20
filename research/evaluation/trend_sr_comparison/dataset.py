"""Causal 1-minute synthetic sources and isolated, same-source interventions.

Frames contain observables only. Ground truth and generation diagnostics are
separate. The normal source is cached; compound arms are materialized on demand.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import scenarios as sc


def load_protocol():
    return json.loads(Path(__file__).with_name("protocol.json").read_text(encoding="utf-8"))


PROTOCOL = load_protocol()
EVENT_COLUMNS = ["scenario_id", "subtype", "variant_id", "case_id", "incident_id", "event_id", "group", "start", "end", "target_fields", "injected_parameters", "source_seed", "snapshot_hash", "support_expected", "identifiability", "component_id"]


@dataclass
class Dataset:
    frame: pd.DataFrame
    events: pd.DataFrame
    quality: pd.DataFrame
    manifest: dict[str, Any]

    def __getitem__(self, key):
        return self.frame if key == "stream" else getattr(self, key)


def _rng(seed, split, namespace):
    digest = hashlib.sha256(f"{PROTOCOL['version']}:{seed}:{split}:{namespace}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little"))


def frame_hash(frame):
    data = pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()
    return hashlib.sha256(data).hexdigest()


def _ar(rng, n, coefficient=.35):
    values = rng.normal(size=n)
    scale = np.sqrt(1 - coefficient ** 2)
    for i in range(1, n):
        values[i] = coefficient * values[i - 1] + scale * values[i]
    return values


def _calendar(starts, split, group):
    local = starts.tz_convert("Asia/Seoul")
    minutes = local.hour * 60 + local.minute
    day = (local.normalize() - sc.start_of(split).tz_convert("Asia/Seoul")).days.to_numpy()
    active = np.ones(len(starts), dtype=bool)
    if group == "business":
        active = (local.dayofweek < 5) & (minutes >= 9 * 60) & (minutes < 18 * 60)
        active &= ~np.isin(day, PROTOCOL["normal_holiday_offsets"][split])
    diurnal = 1 + .24 * np.sin(2 * np.pi * (minutes - 420) / 1440)
    weekday = np.asarray([1.03, .99, 1.04, 1., 1.08, .80, .72])[local.dayofweek]
    # Announced lunch peak and modest permitted growth are normal calendar effects.
    peak = np.where((minutes >= 12 * 60) & (minutes < 12 * 60 + 30), 1.35, 1.)
    growth = 1 + .05 * np.clip(day, 0, sc.DAYS[split]) / sc.DAYS[split]
    expected = (110 if group == "always" else 80) * diurnal * weekday * peak * growth
    sparse_hour = 3 if group == "always" else 17
    expected = np.where((minutes >= sparse_hour * 60) & (minutes < (sparse_hour + 1) * 60), 2., expected)
    return np.asarray(active), np.asarray(expected)


@lru_cache(maxsize=12)
def _normal(seed, split):
    begin = sc.start_of(split)
    prefix = PROTOCOL["evaluation_prefix_minutes"] if split in ("selection", "final") else 0
    starts = pd.date_range(begin - pd.Timedelta(minutes=prefix), periods=sc.DAYS[split] * 1440 + prefix, freq="min").as_unit("ns")
    frames = []
    for group in sc.GROUPS:
        rng = _rng(seed, split, "normal/" + group)
        active, expected = _calendar(starts, split, group)
        latent = _ar(rng, len(starts))
        noise = _ar(rng, len(starts))
        # Lognormal mixture produces overdispersed counts and retains normal tails.
        intensity = expected * np.exp(.24 * latent - .5 * .24 ** 2)
        count = rng.poisson(intensity).astype(float)
        count[~active] = 0
        x = (count - expected) / np.maximum(.27 * expected, 1)
        duration = np.maximum(20., 230 + 45 * (.8 * x + .6 * noise))
        # Fixed rare lognormal tails are legitimate normal observations, never trimmed.
        tails = rng.random(len(starts)) < .002
        duration[tails] *= rng.lognormal(mean=.7, sigma=.3, size=tails.sum())
        duration[count == 0] = np.nan
        error = rng.binomial(count.astype(int), .01).astype(float)
        frames.append(pd.DataFrame({"bin_start": starts, "group": group, "count": count,
                                    "duration_ms": duration, "error_count": error, "total_count": count.copy(),
                                    "source_complete": True, "activity_expected": active,
                                    "category_supported": True, "available_at": starts + pd.Timedelta(minutes=1)},
                                   index=starts + pd.Timedelta(minutes=1)))
    frame = pd.concat(frames).sort_index(kind="stable")
    frame.index.name = "endpoint"
    return frame


def _gate(seed, split, authorize_final):
    if split not in sc.STARTS:
        raise ValueError("split must be train/cal/selection/final")
    if split == "final":
        if seed not in sc.FINAL_SEEDS or not authorize_final:
            raise ValueError("Final source is sealed until explicit root GO and candidate lock")
    elif seed in sc.FINAL_SEEDS:
        raise ValueError("Final seeds cannot be opened in development splits")


def _mask(frame, group, start, end):
    return (frame.group.eq(group) & frame.bin_start.ge(start) & frame.bin_start.lt(end)).to_numpy()


def _event(case, interval, seed, number=0, component_id=""):
    subtype = case["subtype"]
    return {"scenario_id": case.get("scenario_id", "SC-" + subtype), "subtype": subtype,
            "variant_id": case.get("variant_id", "baseline-v1"), "case_id": case["case_id"],
            "incident_id": case.get("incident_id", case["case_id"]),
            "event_id": f"{case['case_id']}/{component_id or subtype}/{number}", "group": case["group"],
            "start": interval[0], "end": interval[1], "target_fields": case.get("target_fields", []),
            "injected_parameters": case.get("parameters", {}), "source_seed": seed, "snapshot_hash": "",
            "support_expected": "observable", "identifiability": "observable_signal", "component_id": component_id}


def _apply(frame, case, seed, split, namespace):
    definition = sc.registry()[case["subtype"]]
    subtype, params = case["subtype"], definition["parameters"]
    for number, (start, end) in enumerate(case["intervals"]):
        mask = _mask(frame, case["group"], start, end)
        ids = np.flatnonzero(mask)
        rng = _rng(seed, split, f"{namespace}/{case['case_id']}/{subtype}/{number}")
        count = frame.iloc[ids]["count"].to_numpy(copy=True)
        if subtype[0] in "BZG":
            if subtype == "G01":
                factor = np.linspace(1., 2., len(ids))
            elif subtype == "G02":
                factor = np.geomspace(1., 4., len(ids))
            elif subtype == "G03":
                factor = np.repeat([1.5, 2., 2.5], 60)
            else:
                factor = params["factor"]
            new = np.rint(count * factor)
            frame.iloc[ids, frame.columns.get_loc("count")] = new
            frame.iloc[ids, frame.columns.get_loc("total_count")] = new
            # Existing success/error population is scaled consistently before rate interventions.
            previous = frame.iloc[ids]["error_count"].to_numpy()
            error = np.minimum(new, np.rint(previous * np.asarray(factor)))
            frame.iloc[ids, frame.columns.get_loc("error_count")] = error
            frame.iloc[ids[new == 0], frame.columns.get_loc("duration_ms")] = np.nan
        elif subtype[0] == "L":
            values = frame.iloc[ids]["duration_ms"].to_numpy(copy=True)
            if subtype == "L03":
                valid = np.flatnonzero(np.isfinite(values))
                factors = np.full(len(valid), 10 / 19)
                tails = rng.choice(len(valid), size=max(1, round(.05 * len(valid))), replace=False)
                factors[tails] = 10.
                changed = values[valid] * factors
                changed *= values[valid].sum() / changed.sum()
                values[valid] = changed
            else:
                values *= params["factor"]
            frame.iloc[ids, frame.columns.get_loc("duration_ms")] = values
        elif subtype[0] == "R":
            values = frame.iloc[ids]["duration_ms"].to_numpy(copy=True)
            valid = np.isfinite(values) & (count > 0)
            x = count[valid]
            z = (x - x.mean()) / x.std()
            if subtype == "R03":
                active, expected = _calendar(pd.DatetimeIndex(frame.iloc[ids].bin_start), split, case["group"])
                values[valid] += 45 * .8 * (x - expected[valid]) / (.27 * expected[valid])
                values[valid] = np.maximum(1., values[valid])
            else:
                noise = _ar(rng, len(x))
                noise -= (noise @ z) / (z @ z) * z
                noise = (noise - noise.mean()) / noise.std()
                rho = params["rho_after"]
                target = rho * z + np.sqrt(1 - rho * rho) * noise
                mapped = np.empty(len(x))
                mapped[np.argsort(target)] = np.sort(values[valid])
                values[valid] = mapped
            frame.iloc[ids, frame.columns.get_loc("duration_ms")] = values
        elif subtype[0] == "E":
            total = frame.iloc[ids]["total_count"].to_numpy()
            frame.iloc[ids, frame.columns.get_loc("error_count")] = rng.binomial(total.astype(int), params["probability"])


def _finish(frame, events, seed, split, suite, definitions, extra=None):
    source_hash = frame_hash(frame)
    events = pd.DataFrame(events, columns=EVENT_COLUMNS)
    events["snapshot_hash"] = source_hash
    quality = frame[["group", "source_complete", "activity_expected", "category_supported", "available_at"]].copy()
    quality["coverage"] = frame.source_complete.astype(float)
    quality["count_support"] = frame.source_complete & frame.category_supported & frame["count"].notna()
    quality["duration_support"] = quality.count_support & frame.duration_ms.notna() & frame["count"].gt(0)
    quality["error_rate_support"] = quality.count_support & frame.total_count.gt(0)
    quality["reason"] = np.select([~frame.source_complete, ~frame.category_supported, frame["count"].eq(0)], ["source_incomplete", "new_category", "zero_exposure"], default="observed")
    protocol_hash = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = dict(protocol_version=PROTOCOL["version"], protocol_hash=protocol_hash, seed=seed, split=split,
                    suite=suite, fixture=seed not in (*sc.DEV_SEEDS, *sc.FINAL_SEEDS), source_hash=source_hash,
                    normal_source_hash=frame_hash(_normal(seed, split)), evaluation_start=sc.start_of(split).isoformat(),
                    evaluation_end=(sc.start_of(split) + pd.Timedelta(days=sc.DAYS[split])).isoformat(),
                    source_rows=len(frame), groups=list(sc.GROUPS), definitions=definitions,
                    intended_cases=len(definitions), generated_cases=len(definitions), generated_events=len(events),
                    duration_definition=PROTOCOL["duration_definition"], transform_order=PROTOCOL["compound_transform_order"],
                    source_columns=list(frame.columns), final_authorized=split == "final")
    manifest["normal_controls"] = {"N01": "announced daily local12:00-12:30 peak factor1.35",
                                   "N02": "normal load-duration relation and permitted5% source growth",
                                   "N03": "business weekday09-18 plus predeclared holiday; inactive zero is observed",
                                   "N04": "always03h/business17h local expected2/min, overdispersed Poisson-lognormal",
                                   "N05": "weekday factors, AR0.35 latent context, rare duration lognormal tails probability.002"}
    manifest["generator_code_sha256"] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                         for name in ("dataset.py", "scenarios.py", "protocol.json", "generation.py")}
    if extra:
        manifest.update(extra)
    frame.attrs.update(evaluation_start=manifest["evaluation_start"], evaluation_end=manifest["evaluation_end"])
    return Dataset(frame, events, quality, manifest)


def generate(seed, split="selection", suite="main", *, authorize_final=False):
    _gate(seed, split, authorize_final)
    if split in ("train", "cal") and suite != "normal":
        raise ValueError("Train and calibration are normal-only")
    if suite not in ("normal", "main", "error", "quality", "recurrence", "identifiability", "quality_cross"):
        raise ValueError("Unknown suite; composites use materialize_compound")
    frame = _normal(seed, split).copy(deep=True)
    events, definitions = [], []
    if suite in ("main", "error"):
        definitions = sc.schedule(seed, split, suite)
        for case in definitions:
            _apply(frame, case, seed, split, suite)
            events.extend(_event(case, interval, seed, i) for i, interval in enumerate(case["intervals"]))
    elif suite != "normal":
        definitions, events = _auxiliary(frame, seed, split, suite)
    return _finish(frame, events, seed, split, suite, definitions)


def _auxiliary(frame, seed, split, suite):
    definitions, events = [], []
    days = pd.bdate_range(sc.start_of(split).tz_convert("Asia/Seoul"), periods=30)
    for gi, group in enumerate(sc.GROUPS):
        anchor = (days[2] + pd.Timedelta(hours=10, minutes=7 + 13 * gi)).tz_convert("UTC")
        if suite in ("quality", "quality_cross"):
            kinds = ("Q01", "Q02", "Q03", "Q04") if suite == "quality" else ("MQ01",)
            for i, kind in enumerate(kinds):
                start = (days[2 + 2 * i] + pd.Timedelta(hours=10, minutes=7 + 13 * gi)).tz_convert("UTC")
                end = start + pd.Timedelta(minutes=60)
                case = dict(subtype=kind, case_id=f"{split}-{seed}-{group}-{kind}", group=group,
                            start=start, intervals=[(start, end)], target_fields=[], parameters={"quality": kind})
                if kind == "MQ01":
                    for subtype in ("B02", "L01"):
                        service = {**case, "subtype": subtype, "intervals": [(start, start + pd.Timedelta(minutes=120 if subtype == "B02" else 60))],
                                   "target_fields": sc.registry()[subtype]["target_fields"]}
                        _apply(frame, service, seed, split, suite)
                        events.append(_event(service, service["intervals"][0], seed, component_id=subtype))
                    case["intervals"] = [(start, start + pd.Timedelta(minutes=120))]
                ids = np.flatnonzero(_mask(frame, group, start, end))
                if kind in ("Q01", "MQ01"):
                    missing = ids[::3]
                    frame.iloc[missing, frame.columns.get_loc("source_complete")] = False
                    frame.iloc[missing, [frame.columns.get_loc(c) for c in ("count", "duration_ms", "error_count", "total_count")]] = np.nan
                if kind in ("Q02", "MQ01"):
                    frame.iloc[ids, frame.columns.get_loc("available_at")] = frame.iloc[ids].available_at + pd.Timedelta(minutes=15)
                elif kind == "Q03":
                    # Canonical snapshot resolves duplicates/revisions before availability; raw ledger is retained in definition.
                    case["parameters"].update(raw_duplicate_records=60, revision_delay_minutes=10, canonical_policy="deduplicate immutable source key, publish last revision at +10m")
                    frame.iloc[ids, frame.columns.get_loc("available_at")] = frame.iloc[ids].available_at + pd.Timedelta(minutes=10)
                elif kind == "Q04":
                    frame.iloc[ids, frame.columns.get_loc("category_supported")] = False
                definitions.append(case)
        elif suite == "recurrence":
            for i, gap in enumerate((20, 45, 90)):
                start = (days[2 + 2 * i] + pd.Timedelta(hours=10, minutes=7 + 13 * gi)).tz_convert("UTC")
                intervals = [(start, start + pd.Timedelta(minutes=60)), (start + pd.Timedelta(minutes=60 + gap), start + pd.Timedelta(minutes=120 + gap))]
                case = dict(subtype="L01", scenario_id=f"SC-T0{i+1}", case_id=f"{split}-{seed}-{group}-T0{i+1}", group=group, intervals=intervals,
                            target_fields=["duration_ms"], parameters={"gap_minutes": gap}, variant_id=f"recurrence-{gap}")
                _apply(frame, case, seed, split, suite)
                definitions.append(case)
                events.extend(_event(case, interval, seed, j) for j, interval in enumerate(intervals))
            for j, gap in enumerate((2, 5, 10)):
                start = (days[8 + 2 * j] + pd.Timedelta(hours=10, minutes=7 + 13 * gi)).tz_convert("UTC")
                case = dict(subtype="L01", scenario_id="SC-T04", case_id=f"{split}-{seed}-{group}-T04-{gap}", group=group,
                            intervals=[(start, start + pd.Timedelta(minutes=60)), (start + pd.Timedelta(minutes=60 + gap), start + pd.Timedelta(minutes=120 + gap))],
                            target_fields=["duration_ms"], parameters={"normal_looking_gap_minutes": gap, "incident_continues": True}, variant_id=f"ongoing-gap-{gap}")
                _apply(frame, case, seed, split, suite)
                definitions.append(case)
                event = _event(case, (start, start + pd.Timedelta(minutes=120 + gap)), seed)
                event["identifiability"] = "normal_looking_gap_observationally_ambiguous"
                events.append(event)
        elif suite == "identifiability":
            # The identical source has two alternative, incompatible business labels.
            end = anchor + pd.Timedelta(minutes=60)
            case = dict(subtype="B02", scenario_id="SC-T05", case_id=f"{split}-{seed}-{group}-T05", group=group,
                        intervals=[(anchor, end)], target_fields=["count"], parameters={"alternative_truth": ["announced_peak_normal", "incident_abnormal"], "observable_configuration_identical": True})
            _apply(frame, case, seed, split, suite)
            event = _event(case, (anchor, end), seed)
            event["identifiability"] = "impossible_from_observables"
            events.append(event)
            definitions.append(case)
    return definitions, events


build_compound_cases = sc.compound_cases


def materialize_compound(case, arm="AB", *, authorize_final=False):
    _gate(case.seed, case.split, authorize_final)
    if arm not in case.arms:
        raise ValueError("Arm is not a registered component subset")
    frame = _normal(case.seed, case.split).copy(deep=True)
    events, definitions = [], []
    selected = [c for c in case.components if c.label in arm]
    order = {"B": 0, "Z": 0, "L": 1, "E": 2}
    for component in sorted(selected, key=lambda c: order[c.subtype[0]]):
        definition = dict(subtype=component.subtype, case_id=case.case_id, group=component.group,
                          intervals=[(component.start, component.end)], target_fields=sc.registry()[component.subtype]["target_fields"],
                          parameters=sc.registry()[component.subtype]["parameters"], variant_id=case.recipe)
        # Same component RNG across arms: E sampling differs only if total changes.
        _apply(frame, definition, case.seed, case.split, "composite")
        events.append(_event(definition, (component.start, component.end), case.seed, component_id=component.label))
        definitions.append(definition)
    return _finish(frame, events, case.seed, case.split, "composite", definitions,
                   {"case_id": case.case_id, "recipe": case.recipe, "arm": arm, "arms": list(case.arms),
                    "intended_cases": 1, "generated_cases": 1,
                    "paired_source_seed": case.seed, "all_components": [vars(c) for c in case.components],
                    "evaluation_exposure": "full42d+common1442min prefix; lazy materialization, no arm-specific cropping"})


def overlap_segments(item):
    """Partition all evaluated group time, including empty component subsets."""
    begin, end = pd.Timestamp(item.manifest["evaluation_start"]), pd.Timestamp(item.manifest["evaluation_end"])
    rows = []
    for group in sc.GROUPS:
        events = item.events[item.events.group.eq(group)]
        boundaries = sorted({begin, end, *events.start.tolist(), *events.end.tolist()})
        for start, stop in zip(boundaries, boundaries[1:]):
            active = events[(events.start <= start) & (events.end > start)]
            labels = sorted(active.component_id[active.component_id.ne("")].unique())
            rows.append(dict(group=group, start=start, end=stop, active_component_ids=labels,
                             bitmask=sum(1 << "ABC".index(label) for label in labels),
                             minutes=(stop - start).total_seconds() / 60))
    return pd.DataFrame(rows)


def connected_incidents(item):
    """Connected union per group; an onset cannot be credited to two components."""
    rows = []
    for group, events in item.events.groupby("group"):
        merged = []
        for event in events.sort_values("start").itertuples():
            if merged and event.start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], event.end)
                merged[-1][2].append(event.event_id)
            else:
                merged.append([event.start, event.end, [event.event_id]])
        for i, (start, end, components) in enumerate(merged):
            rows.append(dict(incident_id=f"{item.manifest.get('case_id', item.manifest['suite'])}/{group}/union/{i}",
                             group=group, start=start, end=end, component_event_ids=components))
    return pd.DataFrame(rows, columns=["incident_id", "group", "start", "end", "component_event_ids"])


def validate_joint_request(subtypes, *, same_group=True):
    """Reject physically undefined requests instead of inventing observations."""
    if not same_group:
        return
    definitions = sc.registry()
    fields = [definitions[s]["target_fields"][0] for s in subtypes]
    if len(fields) != len(set(fields)):
        raise ValueError("MI03: conflicting operations on the same physical field")
    if set(subtypes) & {"Z01", "Z03"} and set(fields) & {"duration_ms", "error_count"}:
        raise ValueError("MI01/MI02: zero exposure has no current duration or error rate")


def relation_controls(seed, split="selection", *, authorize_final=False):
    """Keep marginal and serial controls explicit; neither proves causal purity."""
    base = generate(seed, split, "normal", authorize_final=authorize_final)
    definitions = [case for case in sc.schedule(seed, split) if case["subtype"] in ("R01", "R02")]
    intervention = base.frame.copy(deep=True)
    events = []
    for case in definitions:
        _apply(intervention, case, seed, split, "main")
        events.extend(_event(case, interval, seed, i) for i, interval in enumerate(case["intervals"]))
    result = {"no_intervention": base,
              "intervention": _finish(intervention, events, seed, split, "relation_intervention", definitions,
                                      {"interpretation": "Only R01/R02 windows differ from the paired normal source"})}
    for kind in ("marginal_only", "serial_only"):
        frame = base.frame.copy(deep=True)
        for case in definitions:
            start, end = case["intervals"][0]
            ids = np.flatnonzero(_mask(frame, case["group"], start, end))
            values = frame.iloc[ids].duration_ms.to_numpy(copy=True)
            if kind == "serial_only":
                values = np.roll(values, 61)
            # marginal_only is the exact unchanged marginal for rank-preserving R01/02.
            frame.iloc[ids, frame.columns.get_loc("duration_ms")] = values
        result[kind] = _finish(frame, [], seed, split, "relation_" + kind, definitions,
                               {"interpretation": "Exact marginal control; serial-only circular shift preserves internal autocorrelation except wrap boundary"})
    return result
