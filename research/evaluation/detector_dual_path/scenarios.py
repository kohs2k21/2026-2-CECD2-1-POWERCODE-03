"""Independent, second-resolution synthetic pilot and paired normal episodes.

These fixtures exercise causal replay and known interventions. They are not the
customer benchmark, nor evidence that the real ESB meets any latency target.
"""
from __future__ import annotations

from dataclasses import dataclass
import random

from .replay import Observation, iso, seconds

SCENARIOS = ("individual_delay", "group_delay", "column_relation", "time_burst",
             "input_stop", "gradual_degradation", "mixed_hard")
SPLITS = ("train", "calibration", "threshold", "validation", "test")


@dataclass(frozen=True)
class Episode:
    case_id: str
    split: str
    scenario: str
    control: bool
    source_template_id: str
    events: tuple[Observation, ...]
    start: float
    end: float
    anomaly_start: float | None
    anomaly_end: float | None
    source: str = "synthetic"
    timestamp_resolution_seconds: int = 1
    fixture_version: str = "synthetic-pilot-v1"

    def row_label(self, observation):
        """Known injection label, evaluated outside the feature preparation path."""
        if self.control or self.scenario in ("input_stop", "time_burst"):
            return False
        if not self.anomaly_start <= observation.event_at < self.anomaly_end:
            return False
        if self.scenario == "individual_delay":
            # Injection construction targets every tenth source execution.
            index = int(round((observation.event_at - self.start) / 7))
            return index % 10 == 0
        return True


def _rows(rng, start, case_id, scenario, control, duration=1500):
    events = []
    for index, offset in enumerate(range(0, duration, 7)):
        active = 900 <= offset < 1200 and not control
        count = rng.randint(1, 8)
        latency = rng.uniform(.04, .5)
        if active and scenario == "input_stop":
            continue
        if active and scenario == "individual_delay" and index % 10 == 0:
            latency += 12
        elif active and scenario == "group_delay":
            latency += 8
        elif active and scenario == "gradual_degradation":
            latency += (offset - 900) / 20
        elif active and scenario == "mixed_hard":
            latency += 3 + (offset - 900) / 30
        repeats = 7 if active and scenario in ("time_burst", "mixed_hard") else 1
        for extra in range(repeats):
            at = start + offset + extra * .3
            execution = f"{case_id}-e{index}-{extra}"
            total = count * (6 if active and scenario == "time_burst" else 1)
            success = total - 1 if active and scenario == "column_relation" else total
            fields = {"processHubId": "SYNTHETIC_HUB", "adapterType": "HTTP",
                      "channelId": "SYNTHETIC_CHANNEL", "dependProcessId": None,
                      "status": "P", "startTime": iso(at), "endTime": None,
                      "totalCount": total, "successCount": None, "errorCount": None,
                      "responseCode": None}
            events.append(Observation("synthetic", case_id, execution, "SYNTHETIC_P00", 0, 0,
                                      at, at + .02, fields))
            finished = at + latency
            terminal = {**fields, "endTime": iso(finished), "status": "S",
                        "successCount": success, "errorCount": 0, "responseCode": "200"}
            events.append(Observation("synthetic", case_id, execution, "SYNTHETIC_P00", 0, 1,
                                      at, finished + .02, terminal))
    return tuple(sorted(events, key=lambda event: (event.observed_at, event.execution_id, event.revision)))


def build_suite(seed=45, cases_per_scenario=1):
    if isinstance(cases_per_scenario, bool) or cases_per_scenario < 1:
        raise ValueError("Positive cases_per_scenario required")
    episodes = []
    base = seconds("2026-01-01T00:00:00+00:00")
    for split_index, split in enumerate(SPLITS):
        for scenario_index, scenario in enumerate(SCENARIOS):
            for index in range(cases_per_scenario):
                template = f"{split}-{scenario}-{index}"
                start = base + split_index * 30 * 86400 + (scenario_index * cases_per_scenario + index) * 1800
                case_seed = seed + split_index * 100_000 + scenario_index * 1000 + index
                for control in (False, True):
                    case_id = template + ("-normal" if control else "-injected")
                    events = _rows(random.Random(case_seed), start, case_id, scenario, control)
                    episodes.append(Episode(case_id, split, scenario, control, template,
                                            events, start, start + 1500,
                                            None if control else start + 900,
                                            None if control else start + 1200))
    return episodes


def normal_history_observations(seed=45, duration_seconds=3600):
    """Dedicated prior-only normal history, independent of all five partitions."""
    start = seconds("2025-12-01T00:00:00+00:00")
    return _rows(random.Random(seed + 991_337), start, "normal-history", "normal", True,
                 duration=duration_seconds)
