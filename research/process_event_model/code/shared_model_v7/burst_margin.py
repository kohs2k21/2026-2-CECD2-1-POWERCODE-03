"""Small declared operating buffer for observed VOLUME evidence only.

No new detector, label routing, point adjustment or event extension is added.
Score >1 remains the public alarm threshold. For known Adapter rows whose
observed dominant evidence is volume, a fixed margin is subtracted before
that threshold. Unknown Adapter fallback alarms remain EXACTLY unchanged.
"""
from dataclasses import dataclass
import numpy as np


@dataclass
class Reference:
    previous:object
    margin:float=.025
    mode:str='fixed_conservative_margin_on_observed_volume_evidence'
    kind:str='normalized_margin_not_fault_probability'
    @property
    def parent(self):return self.previous.parent
    @property
    def encoder(self):return self.previous.encoder
    @property
    def codec(self):return self.previous.codec
    @property
    def alpha(self):return self.previous.alpha
    @property
    def tables(self):return self.previous.tables
    @property
    def original_score(self):return self.previous.original_score
    @property
    def gap_core_retained(self):return getattr(self.previous,'gap_core_retained',False)
    def apply(self,f,raw):
        value=self.previous.apply(f,raw)
        observed=np.argmax(self.parent.encoder.transform(f,'peaks'),axis=1)
        known=getattr(self.codec,'adapters',getattr(self.codec,'known',()))
        mask=(observed==3)&f.adapterType.astype(str).isin(known).to_numpy()
        value[mask]-=self.margin
        return value
