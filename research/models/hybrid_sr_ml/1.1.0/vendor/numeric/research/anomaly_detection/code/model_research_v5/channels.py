"""Separate activity and latency evidence before calibrating their maximum.

Motivation: joint vectors can let broad duration variability dilute a rare
traffic pattern. Channel scores are normal-calibration empirical tail ranks.
They are NOT guaranteed valid p-values for dependent/shifted replay streams.
"""
from dataclasses import dataclass
import numpy as np

from model_research_v5.features import CONTEXT
from model_research_v5.models import Encoder, RawModel, Settings, Detector, Family

ACTIVITY = ("arrival_dev_1m", "arrival_dev_5m", "arrival_dev_15m", "zero_run")
LATENCY = ("max_delay_change", "p95_delay_change", "duration_slope", "delay_tail_spread", "delay_ewma", "delay_cusum")


@dataclass
class TailReference:
    phases: dict

    @classmethod
    def fit(cls, scores, phases, eligible=None):
        if eligible is None: eligible = np.ones(len(scores), bool)
        whole = np.sort(scores[eligible])
        if len(whole) < 20: raise ValueError("Insufficient normal channel calibration")
        samples = {}
        for phase in ("startup", "steady"):
            sample = scores[eligible & (phases == phase)]
            samples[phase] = np.sort(sample) if len(sample) >= 50 else whole
        return cls(samples)

    def score(self, values, phases):
        output = np.empty(len(values))
        for phase, reference in self.phases.items():
            mask = phases == phase
            rank = np.searchsorted(reference, values[mask], side="left")
            # Inclusive >= ties and add-one smoothing avoid log(0) and treating
            # frequent identical normal vectors as abnormal just from ties.
            tail = (len(reference)-rank+1)/(len(reference)+1)
            output[mask] = -np.log(tail)
        return output


class ChannelRaw:
    def __init__(self, settings): self.settings = settings

    def fit(self, values, calibration, columns, calibration_frame):
        self.columns = tuple(columns)
        self.activity_indices = [columns.index(c) for c in ACTIVITY]
        self.latency_indices = [columns.index(c) for c in LATENCY]
        self.activity = RawModel(self.settings).fit(values[:, self.activity_indices], calibration[:, self.activity_indices])
        self.latency = RawModel(self.settings).fit(values[:, self.latency_indices], calibration[:, self.latency_indices])
        phase = calibration_frame.phase.to_numpy()
        self.activity_reference = TailReference.fit(self.activity.score(calibration[:, self.activity_indices]), phase)
        enough = self.eligible(calibration_frame)
        self.latency_reference = TailReference.fit(self.latency.score(calibration[:, self.latency_indices]), phase, enough)
        return self

    @staticmethod
    def eligible(frame):
        return (frame.history_support.ge(.4) & frame.missing_duration_1m.eq(0)).to_numpy()

    def channel_scores(self, values, frame):
        phase = frame.phase.to_numpy()
        activity = self.activity_reference.score(self.activity.score(values[:, self.activity_indices]), phase)
        latency = self.latency_reference.score(self.latency.score(values[:, self.latency_indices]), phase)
        assessed = self.eligible(frame)
        latency[~assessed] = 0.
        return np.column_stack([activity, latency]), assessed

    def score(self, values, frame):
        return self.channel_scores(values, frame)[0].max(axis=1)


class ChannelDetector(Detector):
    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        encoder = Encoder.fit(train, columns)
        raw = ChannelRaw(settings).fit(encoder.transform(train), encoder.transform(calibration), tuple(columns), calibration)
        return cls(encoder, raw, raw.score(encoder.transform(calibration), calibration),
                   raw.score(encoder.transform(threshold), threshold), calibration.phase.to_numpy(), threshold.phase.to_numpy())

    def raw(self, frame): return self.raw_model.score(self.encoder.transform(frame), frame)

    def explain(self, frame): return self.raw_model.channel_scores(self.encoder.transform(frame), frame)


class ChannelFamily(Family):
    def fit(self, partitions, source_support):
        self.global_model = ChannelDetector.fit(*(partitions[n] for n in ("train", "calibration", "threshold")), self.columns, self.settings)
        adapters = sorted(set().union(*(set(p.adapterType) for p in partitions.values())))
        for adapter in adapters:
            parts = {n: p.loc[p.adapterType.eq(adapter)] for n, p in partitions.items()}
            reasons = [f"{n}<{minimum}" for n, minimum in (("train", 500), ("calibration", 100), ("threshold", 100)) if len(parts[n]) < minimum]
            reasons += [f"{n}_source_workflows<20" for n in parts if source_support.get(adapter, {}).get(n, 0) < 20]
            if not reasons:
                expert = ChannelDetector.fit(*(parts[n] for n in ("train", "calibration", "threshold")), self.columns, self.settings)
                self.experts[adapter] = expert
                self.threshold_raw[adapter] = np.column_stack([self.global_model.raw(parts["threshold"]), expert.threshold_raw])
            self.support.append({"adapter": adapter, **{n: len(p) for n, p in parts.items()},
                                 "source_workflows": source_support.get(adapter, {}), "expert_available": not reasons,
                                 "reason": "; ".join(reasons) or "supported"})
        self.set_operating_point(.99, .5, True)
        return self
