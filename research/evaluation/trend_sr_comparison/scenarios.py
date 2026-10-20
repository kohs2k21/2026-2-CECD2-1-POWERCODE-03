"""Frozen scenario definitions; no detector is imported by the generator."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import pandas as pd

DEV_SEEDS = (3101, 3137, 3173, 3209, 3247)
FINAL_SEEDS = (4101, 4137, 4173, 4209, 4247)
GROUPS = ("always", "business")
STARTS = {"train": "2025-03-03", "cal": "2025-04-14", "selection": "2025-05-12", "final": "2025-06-23"}
DAYS = {"train": 42, "cal": 28, "selection": 42, "final": 42}
MAIN = ("B01", "B02", "B03", "Z01", "Z02", "Z03", "L01", "L02", "L03", "G01", "G02", "G03", "R01", "R02", "R03")
ERROR = ("E01", "E02", "E03")


def registry():
    """Each interval is [offset minutes, duration minutes], excluding pulse gaps."""
    definitions = {
        "B01": ("count", [(0, 5)], {"factor": 5.0}),
        "B02": ("count", [(0, 120)], {"factor": 2.0}),
        "B03": ("count", [(0, 10), (30, 10), (60, 10)], {"factor": 3.0}),
        "Z01": ("count", [(0, 30)], {"factor": 0.0}),
        "Z02": ("count", [(0, 120)], {"factor": 0.2}),
        "Z03": ("count", [(0, 5), (25, 5), (50, 5), (75, 5)], {"factor": 0.0}),
        "L01": ("duration_ms", [(0, 60)], {"factor": 2.0}),
        "L02": ("duration_ms", [(0, 5)], {"factor": 5.0}),
        "L03": ("duration_ms", [(0, 120)], {"tail_fraction": .05, "tail_factor": 10., "body_factor": 10 / 19}),
        "G01": ("count", [(0, 180)], {"shape": "linear", "start_factor": 1., "end_factor": 2.}),
        "G02": ("count", [(0, 180)], {"shape": "exponential", "start_factor": 1., "end_factor": 4.}),
        "G03": ("count", [(0, 180)], {"shape": "steps", "factors": [1.5, 2., 2.5]}),
        "R01": ("duration_ms", [(0, 180)], {"rho_before": .8, "rho_after": 0.}),
        "R02": ("duration_ms", [(0, 180)], {"rho_before": .8, "rho_after": -.8}),
        "R03": ("duration_ms", [(0, 180)], {"conditional_slope_factor": 2.}),
        "E01": ("error_count", [(0, 60)], {"probability": .15}),
        "E02": ("error_count", [(0, 5)], {"probability": .5}),
        "E03": ("error_count", [(0, 5), (25, 5), (50, 5), (75, 5)], {"probability": .15}),
    }
    return {key: {"scenario_id": "SC-" + key,
                  "target_fields": (["count", "total_count", "error_count"] + (["duration_ms"] if key in ("Z01", "Z03") else [])) if field == "count" else [field], "intervals": intervals,
                  "parameters": params, "variant_id": "baseline-v1"}
            for key, (field, intervals, params) in definitions.items()}


def start_of(split):
    if split not in STARTS:
        raise ValueError(f"Unknown split {split}")
    return pd.Timestamp(STARTS[split], tz="Asia/Seoul").tz_convert("UTC")


def seed_index(seed):
    return (DEV_SEEDS.index(seed) if seed in DEV_SEEDS else
            FINAL_SEEDS.index(seed) if seed in FINAL_SEEDS else 0)


def schedule(seed, split, family="main"):
    if split not in ("selection", "final"):
        return []
    if family not in ("main", "error"):
        raise ValueError("schedule family must be main or error")
    subtypes = MAIN if family == "main" else ERROR
    business_days = pd.bdate_range(start_of(split).tz_convert("Asia/Seoul").normalize(), periods=30)
    result = []
    for gi, group in enumerate(GROUPS):
        for slot in range(len(subtypes)):
            si = (4 * slot + 3 * seed_index(seed) + gi) % 15 if family == "main" else (slot + seed_index(seed) + gi) % 3
            subtype = subtypes[si]
            phase = (7 * si + 11 * seed_index(seed) + 13 * gi) % 60
            anchor = (business_days[slot * 2] + pd.Timedelta(hours=10, minutes=phase)).tz_convert("UTC")
            definition = registry()[subtype]
            intervals = [(anchor + pd.Timedelta(minutes=o), anchor + pd.Timedelta(minutes=o + d)) for o, d in definition["intervals"]]
            result.append(dict(subtype=subtype, scenario_id="SC-" + subtype, group=group,
                               case_id=f"{split}-{seed}-{group}-{subtype}", start=anchor, intervals=intervals,
                               variant_id="baseline-v1", slot=slot, phase_minutes=phase,
                               parameters=definition["parameters"], target_fields=definition["target_fields"]))
    return result


@dataclass(frozen=True)
class Component:
    label: str
    subtype: str
    group: str
    start: pd.Timestamp
    end: pd.Timestamp


@dataclass(frozen=True)
class CompoundCase:
    case_id: str
    recipe: str
    seed: int
    split: str
    group: str
    anchor: pd.Timestamp
    components: tuple[Component, ...]
    arms: tuple[str, ...]


def compound_cases(seed, split):
    if split not in ("selection", "final"):
        raise ValueError("Compounds need selection or final split")
    recipes = {
        "MX01": (("B02", 0, 120), ("L01", 0, 60)),
        "MX02": (("L01", 0, 60), ("E01", 0, 60)),
        "MX03": (("B02", 0, 120), ("L01", 30, 90)),
        "MX04": (("B02", 0, 120), ("L01", 90, 150)),
        "MX05": (("B02", 0, 120), ("L01", 180, 240)),
        "MX06": (("B02", 0, 120), ("L01", 0, 60)),
        "MX07": (("Z02", 0, 120), ("L01", 0, 60)),
        "MX08": (("B02", 0, 120), ("L01", 0, 60), ("E01", 0, 60)),
    }
    days = pd.bdate_range(start_of(split).tz_convert("Asia/Seoul"), periods=30)
    result = []
    for gi, group in enumerate(GROUPS):
        for ri, (recipe, definitions) in enumerate(recipes.items()):
            # Two business days between cases; event phases need not be C-aligned.
            anchor = (days[ri * 2] + pd.Timedelta(hours=10, minutes=(7 * ri + 11 * seed_index(seed) + 13 * gi) % 60)).tz_convert("UTC")
            components = tuple(Component("ABC"[i], subtype,
                                         GROUPS[1 - gi] if recipe == "MX06" and i == 1 else group,
                                         anchor + pd.Timedelta(minutes=begin), anchor + pd.Timedelta(minutes=end))
                               for i, (subtype, begin, end) in enumerate(definitions))
            arms = ("0", "A", "B", "AB") if len(components) == 2 else ("0", "A", "B", "C", "AB", "AC", "BC", "ABC")
            result.append(CompoundCase(f"{split}-{seed}-{group}-{recipe}", recipe, seed, split, group, anchor, components, arms))
    return result


def compatibility_table():
    """153 unordered pairs; conservative same-field conflicts are explicit."""
    rows = []
    for first, second in combinations(registry(), 2):
        a, b = registry()[first]["target_fields"][0], registry()[second]["target_fields"][0]
        zero = first in ("Z01", "Z03") or second in ("Z01", "Z03")
        same = a == b
        unsupported = zero and (a == "duration_ms" or b == "duration_ms" or a == "error_count" or b == "error_count")
        rows.append(dict(first=first, second=second, same_group_overlap=not (same or unsupported),
                         different_groups=True, nonoverlapping_same_group=True,
                         first_target_fields=[a], second_target_fields=[b],
                         status="field_conflict" if same else "observable_unsupported" if unsupported else "compatible",
                         transform_order=["count/total", "duration_ms", "error_count"],
                         reason="No implicit last-write-wins" if same else "Zero exposure has no duration/rate" if unsupported else "Disjoint physical fields; cross-effects retained"))
    return rows
