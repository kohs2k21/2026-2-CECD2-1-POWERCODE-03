"""Retrain on caller-supplied actual normal Process CSVs, no benchmark required.

This normal-only threshold baseline does NOT inherit the published, labelled-
development-selected fusion cutoffs or benchmark performance.
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .calibration import CalibratedEventDetector
from .data import sample_ids,write_json
from .features import normalize
from .models import Detector,Settings
from .row_context import ExactReference,RowContext


def session_rows(records):
    f=normalize(records)
    if 'status' not in f or not f.status.eq('S').all():
        raise ValueError('Supply curated, completed STATUS=S normal candidates; do not silently treat N/P/F as normal')
    for c in ('successCount','errorCount','retryCount'):
        if c not in f or f[c].isna().any() or (f[c]<0).any() or (f[c]%1!=0).any():
            raise ValueError('Invalid normal counts; apply quality/hard rules before fitting')
    if not f.errorCount.eq(0).all() or not f.totalCount.eq(f.successCount+f.errorCount).all():
        raise ValueError('Normal input violates count consistency')
    if f.duplicated(['source_period','transactionId','processId','retryCount']).any():
        raise ValueError('Duplicate original Process execution keys; inspect source data before fitting')
    dates=f.availableTime.dt.tz_convert('Asia/Seoul').dt.normalize().astype(str)
    f['case_id']=f.source_period.astype(str)+':'+dates
    return f


def fit_normal(train_records,calibration_records,quantile=.98,cap=60000,predictive_traffic=False):
    if not .94<=quantile<1:
        raise ValueError('normal threshold quantile must be in [.94,1)')
    train,cal=session_rows(train_records),session_rows(calibration_records)
    train_days=train.availableTime.dt.tz_convert('Asia/Seoul').dt.normalize()
    cal_days=cal.availableTime.dt.tz_convert('Asia/Seoul').dt.normalize()
    if train_days.max()>=cal_days.min():
        raise ValueError('Calibration must use strictly later observed dates than training')
    txkeys=['source_period','transactionId']
    if len(train[txkeys].merge(cal[txkeys].drop_duplicates(),on=txkeys)):
        raise ValueError('A source transaction overlaps training and calibration')
    reference=ExactReference.fit(train)
    if predictive_traffic:
        from .predictive import PredictiveRowContext
        processor=PredictiveRowContext
    else:
        processor=RowContext
    train_features=processor(reference).transform(train)
    train_features=train_features.loc[sample_ids(train_features,cap)]
    cal_features=processor(reference).transform(cal)
    settings={'if':Settings('if','row_context',samples=256),
              'kmeans':Settings('kmeans','row_compact',clusters=16),
              'lof':Settings('lof','row_compact',neighbors=60)}
    models={}
    for a,s in settings.items():
        base=Detector(s).fit(train_features)
        model=CalibratedEventDetector(base,.25).fit_calibration(cal_features)
        groups,score=model.components(cal_features)
        fallback=max(float(np.quantile(score,quantile)),1e-6)
        for g in range(8):
            values=score[groups==g]
            model.cutoffs[g]=max(float(np.quantile(values,quantile)),1e-6) if len(values)>=30 else fallback
        if predictive_traffic:
            from .predictive_experiment import FrozenCorePredictiveDetector
            model=FrozenCorePredictiveDetector(model,.25).fit_calibration(cal_features)
            groups,score=model.components(cal_features)
            fallback=max(float(np.quantile(score,quantile)),1e-6)
            for g in range(4):
                values=score[groups==g]
                model.cutoffs[g]=max(float(np.quantile(values,quantile)),1e-6) if len(values)>=30 else fallback
        models[a]=model
    return dict(reference=reference,models=models,processor='predictive_row_context' if predictive_traffic else 'row_context_v2',
                training_mode='new_original_normal_only_baseline',published_metrics_inherited=False,
                normal_quantile=quantile,fit_rows=len(train_features),calibration_rows=len(cal_features))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--train-normal-csv',type=Path,required=True)
    parser.add_argument('--normal-calibration-csv',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--quantile',type=float,default=.98)
    parser.add_argument('--predictive-traffic',action='store_true')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Existing model preserved; choose a new output path')
    package=fit_normal(pd.read_csv(args.train_normal_csv),pd.read_csv(args.normal_calibration_csv),args.quantile,
                       predictive_traffic=args.predictive_traffic)
    args.output.parent.mkdir(parents=True,exist_ok=True);joblib.dump(package,args.output,compress=3)
    write_json(args.output.with_suffix('.json'),{k:v for k,v in package.items() if k not in ('reference','models')})
    print(f'Saved three new original-row models: {args.output}',flush=True)


if __name__=='__main__':main()
