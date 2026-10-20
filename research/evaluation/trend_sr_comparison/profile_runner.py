"""Bounded fixture-only cost profile; never performs model/candidate selection."""
from __future__ import annotations
import argparse
from pathlib import Path
import time
import json
from . import dataset,metrics
from .methods import Configuration,FeatureCache,fit_method,score_method
from .references import fit_normal
from .runner import prepare,Publisher,write_json,save_pickle,_save_raw,CADENCES,_package


def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    data={key:dataset.generate(9501,split,suite) for key,split,suite in [
        ('train','train','normal'),('cal','cal','normal'),('selection','selection','main'),('error','selection','error')]}
    aggs={key:prepare(item,1) for key,item in data.items()}
    normal=fit_normal(aggs['train'],'B1');caches={key:FeatureCache(value,normal) for key,value in aggs.items()}
    publisher=Publisher()
    base=fit_method(aggs['train'],Configuration('R00',60),normal,caches['train'],seed=9501)
    basecal=publisher.publish(score_method(base,aggs['cal'],caches['cal']),CADENCES)
    baseselect={key:publisher.publish(score_method(base,aggs[key],caches[key]),CADENCES) for key in ('selection','error')}
    rows=[]
    for method,window in [('R00',60),('R01',60),('R16',1440),('R14',1440)]:
        cfg=Configuration(method,window);row={'method':method,'window':window};last=time.perf_counter()
        def lap(key):
            nonlocal last
            now=time.perf_counter();row[key]=now-last;last=now
        artifact=fit_method(aggs['train'],cfg,normal,caches['train'],seed=9501);lap('fit_wall')
        rawcal=score_method(artifact,aggs['cal'],caches['cal']);lap('score_cal_wall')
        published=publisher.publish(rawcal,CADENCES);lap('publish_cal_wall')
        suite='error' if method=='R14' else 'selection'
        raw=score_method(artifact,aggs[suite],caches[suite]);lap('score_selection_wall')
        selected=publisher.publish(raw,CADENCES);lap('publish_selection_wall')
        families=list(next(iter(raw.values())).values)
        views=[(None,('count','latency','relation'))] if method=='R00' else [(f,(f,)) for f in families]
        calibrations=[]
        for family,columns in views:
            calibrations.append((family,'branch',metrics.calibrate(published,score_columns=columns)))
            if family:
                package=_package(published,basecal,family)
                cols=('count','latency','relation','error') if family=='error' else ('count','latency','relation')
                calibrations.append((family,'replacement_package',metrics.calibrate(package,score_columns=cols)))
        lap('all_calibration_wall')
        count=0
        for family,view,calibration in calibrations:
            if not calibration['passed']:continue
            source=selected if view=='branch' else _package(selected,baseselect[suite],family)
            for cadence,frame in source.items():
                prediction=metrics.apply_thresholds(frame,calibration['thresholds'])
                evaluated=metrics.evaluate(prediction,data[suite].events);count+=1
        lap('all_evaluation_wall')
        _save_raw(raw,output/f'{method}-selection-raw.npz')
        _save_raw(rawcal,output/f'{method}-cal-raw.npz')
        save_pickle(output/f'{method}-reference.pkl.gz',artifact)
        lap('artifact_io_wall')
        row['evaluations']=count;rows.append(row)
        print(json.dumps(row),flush=True);write_json(output/'profile.json',{'fixture_seed':9501,'rows':rows})
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    run(parser.parse_args().output)
