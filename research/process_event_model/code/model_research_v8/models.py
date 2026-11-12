"""Cadence-gated traffic and conditional-duration channels, normal calibration.

No label or scenario selector routes a decision. The maximum of channel/model
scores is calibrated on separate normal data; it is NOT a binary OR shortcut.
"""
from dataclasses import dataclass, asdict
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from threadpoolctl import threadpool_limits
from process_anomaly_pipeline.features import ScoreNormalizer
from model_research_v5.models import Settings, Encoder, RawModel, Detector, Family, normalize, Suite, METHODS
from model_research_v8.features import TRAFFIC, LATENCY, FEATURES


@dataclass(frozen=True)
class ResearchSettings(Settings):
    covariance: str = "radius"

    def __post_init__(self):
        super().__post_init__()
        if self.covariance not in ("radius", "diagonal", "full"): raise ValueError("Unknown cluster covariance")


class ClusterDistance:
    """K-means prototypes; nearest shape-normalized center, calibrated radius.

    Full covariance is a shrunk Mahalanobis-distance candidate, not a GMM.
    Models are still MiniBatch K-means, with no class labels in fit.
    """
    def __init__(self, settings): self.settings = settings

    def fit(self, values, calibration):
        self.model = MiniBatchKMeans(n_clusters=min(self.settings.k, len(np.unique(values, axis=0))),
                                    random_state=self.settings.seed, n_init=5, batch_size=2048,
                                    max_iter=100, reassignment_ratio=.01)
        with threadpool_limits(limits=2): self.model.fit(values); labels = self.model.predict(calibration)
        residual = calibration-self.model.cluster_centers_[labels]
        pooled = np.atleast_2d(np.cov(residual.T))
        self.inverse = []
        for j in range(self.model.n_clusters):
            r = residual[labels == j]
            cov = np.atleast_2d(np.cov(r.T)) if len(r) >= max(30, values.shape[1]*3) else pooled
            diag = np.diag(np.diag(cov))
            if self.settings.covariance == "diagonal": cov = diag
            elif self.settings.covariance == "full": cov = .5*cov+.5*diag
            else: cov = np.eye(values.shape[1])
            self.inverse.append(np.linalg.inv(cov+np.eye(values.shape[1])*.05))
        distance = self.distances(calibration)
        nearest = distance.argmin(axis=1); minimum = distance.min(axis=1)
        fallback = max(float(np.quantile(minimum, .95)), .1)
        self.radii = np.array([max(float(np.quantile(minimum[nearest == j], .95)), .1)
                               if np.sum(nearest == j) >= 30 else fallback for j in range(self.model.n_clusters)])
        return self

    def distances(self, values):
        return np.column_stack([np.sqrt(np.maximum(np.einsum('ij,jk,ik->i', values-c, inverse, values-c), 0.))
                                for c, inverse in zip(self.model.cluster_centers_, self.inverse)])

    def score(self, values):
        d = self.distances(values)
        nearest = d.argmin(axis=1)
        return d[np.arange(len(d)), nearest]/self.radii[nearest]


class ChannelDetector(Detector):
    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        result = cls(None, None, None, None, calibration.phase.to_numpy(), threshold.phase.to_numpy())
        result.channels = []
        for cols, eligible in ((TRAFFIC, "traffic_assessed"), (LATENCY, "latency_assessed")):
            tr, ca = train.loc[train[eligible]], calibration.loc[calibration[eligible]]
            if len(tr) < 80 or len(ca) < 20: raise ValueError("Insufficient assessed normal channel observations")
            encoder = Encoder.fit(tr, cols)
            raw = RawModel(settings) if settings.algorithm == "if" else ClusterDistance(settings)
            raw.fit(encoder.transform(tr), encoder.transform(ca))
            norm = ScoreNormalizer.fit(raw.score(encoder.transform(ca)))
            result.channels.append((encoder, raw, norm, eligible))
        result.calibration_channels = result.explain(calibration)
        result.threshold_channels = result.explain(threshold)
        result.latency_weight = 1.
        result.refresh()
        return result

    def explain(self, frame):
        out = np.zeros((len(frame), 2))
        for i, (encoder, model, norm, eligible) in enumerate(self.channels):
            mask = frame[eligible].to_numpy(bool)
            if mask.any():
                # Zero means no positive evidence, not an assertion of health.
                out[mask, i] = np.maximum(norm.transform(model.score(encoder.transform(frame.loc[mask]))), 0.)
        return out

    def combine(self, channels): return np.maximum(channels[:, 0], self.latency_weight*channels[:, 1])

    def refresh(self):
        self.calibration_raw = self.combine(self.calibration_channels)
        self.threshold_raw = self.combine(self.threshold_channels)

    def raw(self, frame): return self.combine(self.explain(frame))


class ChannelFamily(Family):
    def fit(self, partitions, source_support):
        self.global_model = ChannelDetector.fit(*(partitions[n] for n in ("train", "calibration", "threshold")), FEATURES, self.settings)
        self.threshold_channels = {}
        adapters = sorted(set().union(*(set(p.adapterType) for p in partitions.values())))
        for adapter in adapters:
            parts = {n:p.loc[p.adapterType.eq(adapter)] for n,p in partitions.items()}
            reasons = [f"{n}<{minimum}" for n,minimum in (("train",500),("calibration",100),("threshold",100)) if len(parts[n])<minimum]
            reasons += [f"{n}_source_workflows<20" for n in parts if source_support.get(adapter,{}).get(n,0)<20]
            reasons += [f"{n}_{channel}_observations<{minimum}" for n in parts for channel in ("traffic_assessed","latency_assessed")
                        for minimum in [80 if n=='train' else 20] if int(parts[n][channel].sum())<minimum]
            if not reasons:
                expert = ChannelDetector.fit(*(parts[n] for n in ("train","calibration","threshold")), FEATURES, self.settings)
                self.experts[adapter] = expert
                self.threshold_channels[adapter] = np.column_stack([self.global_model.explain(parts["threshold"]), expert.threshold_channels])
            self.support.append({"adapter":adapter, **{n:len(p) for n,p in parts.items()}, "source_workflows":source_support.get(adapter,{}),
                                 "expert_available":not reasons, "reason":"; ".join(reasons) or "supported"})
        self.set_operating_point(.99,.5,True,1.,"mean")
        return self

    def set_operating_point(self,q,weight,phase_aware,latency_weight=1.,combination="mean"):
        if latency_weight <= 0 or combination not in ("mean","max"): raise ValueError("Invalid channel combination")
        self.latency_weight,self.combination = latency_weight,combination
        for d in (self.global_model,*self.experts.values()): d.latency_weight=latency_weight; d.refresh()
        self.threshold_raw={a:np.column_stack([self.global_model.combine(raw[:,:2]),self.experts[a].combine(raw[:,2:])])
                            for a,raw in self.threshold_channels.items()}
        super().set_operating_point(q,weight,phase_aware)
        if combination == "max":
            for adapter,expert in self.experts.items():
                phase=expert.threshold_phase; raw=self.threshold_raw[adapter]
                global_score=normalize(raw[:,0],phase,self.norms["GLOBAL"])
                local=normalize(raw[:,1],phase,self.norms[adapter])
                combined=np.maximum(weight*global_score,(1-weight)*local)
                for p in ("startup","steady"):
                    mask=phase==p if phase_aware else np.ones(len(phase),bool)
                    if mask.sum()<50:mask=np.ones(len(phase),bool)
                    self.thresholds[adapter][p][2]=np.quantile(combined[mask],q)

    def components(self,frame):
        glob=self.global_model.explain(frame); raw=np.column_stack([glob,glob])
        covered=frame.adapterType.isin(self.experts).to_numpy()
        for adapter,expert in self.experts.items():
            mask=frame.adapterType.eq(adapter).to_numpy()
            if mask.any():raw[mask,2:]=expert.explain(frame.loc[mask])
        return raw,covered

    def from_components(self,raw,adapters,phases):
        pair=np.column_stack([self.global_model.combine(raw[:,:2]),self.global_model.combine(raw[:,2:])])
        margin=super().from_components(pair,adapters,phases)
        if self.combination=="max":
            g=normalize(pair[:,0],phases,self.norms["GLOBAL"])
            for adapter in self.experts:
                mask=adapters==adapter
                if not mask.any():continue
                local=normalize(pair[mask,1],phases[mask],self.norms[adapter])
                threshold=np.array([self.thresholds[adapter][p][2] for p in phases[mask]])
                margin[mask,2]=np.maximum(self.global_weight*g[mask],(1-self.global_weight)*local)-threshold
        return margin

    def records(self):
        return [{**r,"latency_weight":self.latency_weight,"combination":self.combination} for r in super().records()]
