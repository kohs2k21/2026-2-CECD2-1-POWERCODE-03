"""Preserve the SAME core model's established gap-channel operating point.

Observed missing-input evidence selects this score path, never a scenario
label or event boundary. This is a calibration policy, NOT a hard business
rule and NOT a second estimator. It avoids re-optimizing a strong unchanged
gap channel while researching point/volume changes.
"""
from dataclasses import dataclass
import argparse
from pathlib import Path
import joblib
import numpy as np
from shared_model_v7.protocol import OUT,atomic_dump


@dataclass
class Reference:
    previous:object
    original_threshold:float
    mode:str='retain_same_core_original_operating_point_for_observed_gap_evidence'
    kind:str='exact_margin_not_fault_probability'
    gap_core_retained:bool=True
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
    def apply(self,f,raw):
        value=self.previous.apply(f,raw)
        gap=np.argmax(self.parent.encoder.transform(f,'peaks'),axis=1)==4
        value[gap]=1+(raw[gap]-self.original_threshold)/max(abs(self.original_threshold),1e-5)
        return value


def run(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if not output.is_relative_to(OUT.resolve()):raise ValueError('Research output only')
    m=joblib.load(source)
    if not np.isfinite(m.base.threshold):raise ValueError('Frozen core threshold required')
    m.reference=Reference(m.reference,float(m.base.threshold));atomic_dump(m,output)
    print('RESEARCH GAP SCORE RETENTION',m.algorithm,output,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();run(args.model,args.output)
