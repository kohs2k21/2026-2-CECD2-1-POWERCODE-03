"""Readable research views, with protocol changes explicit."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from model_research_v5 import report as previous

ROOT=Path(__file__).resolve().parents[2]
VERSIONS={'v4':'model_improvement_v4','v7':'model_research_v7','v8':'model_research_v8',
          'v9':'model_research_v9','v10':'model_research_v10','v11':'model_research_v11','v12':'model_research_v12'}
LABELS=previous.LABELS;SCENARIOS=previous.SCENARIOS
previous.VERSIONS.update(VERSIONS)


def read(v='v12',name='summary'):
    return json.loads((ROOT/'reports'/VERSIONS[v]/f'{name}.json').read_text(encoding='utf-8'))


def setup():previous.setup()


def baseline():return [*read('v7')['pooled'][:3],*read('v4')['pooled'][3:]]


def comparison():
    rows=[]
    for i,(old,new) in enumerate(zip(baseline(),read()['pooled'])):
        r={'모델':LABELS[i],'기준 버전':'v7' if i<3 else 'v4'}
        for label,key in (('Accuracy','accuracy'),('Precision','precision'),('Recall','recall'),('F1','f1'),('정상 오탐','normal_false_positive_rate')):
            r[f'기존 {label} (%)']=old[key]*100;r[f'새 {label} (%)']=new[key]*100;r[f'{label} 변화 (%p)']=(new[key]-old[key])*100
        rows.append(r)
    return pd.DataFrame(rows).round(2)


def progress():
    rows=[]
    for v in VERSIONS:
        for i,r in enumerate(read(v)['pooled']):rows.append({'버전':v,'모델':LABELS[i],'F1 (%)':r['f1']*100,'Recall (%)':r['recall']*100,'정상 오탐 (%)':r['normal_false_positive_rate']*100})
    return pd.DataFrame(rows).round(2)


def bars():
    old,new=baseline(),read()['pooled'];fig,axes=plt.subplots(1,2,figsize=(14,5));x=np.arange(6)
    for ax,key,title in zip(axes,('f1','normal_false_positive_rate'),('기존/새 F1','기존/새 정상 오탐')):
        for offset,records,label,color in ((-.18,old,'기준: IF v7 / K-means v4','#A2ADB7'),(.18,new,'새 v11','#2674AA')):
            label=label.replace('v11','v12')
            bars=ax.bar(x+offset,[r[key]*100 for r in records],.36,label=label,color=color);ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=9)
        ax.set_xticks(x,LABELS,rotation=25,ha='right');ax.set_title(title);ax.set_ylabel('%');ax.set_ylim(0,max(r[key]*100 for r in [*old,*new])*1.2+1)
        ax.legend();ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.tight_layout();return fig


def scenarios():
    fig,axes=plt.subplots(2,1,figsize=(14,10));new=read()['datasets'];x=np.arange(len(new));w=.19
    old_if={d['dataset']:d for d in read('v7')['datasets']};old_k={d['dataset']:d for d in read('v4')['datasets']}
    for j,(source,mi,label,color) in enumerate(((old_if,2,'기존 IF 앙상블','#B0BAC2'),({d['dataset']:d for d in new},2,'새 IF 앙상블','#2674AA'),
                                              (old_k,5,'기존 K-means 앙상블','#D6C0A5'),({d['dataset']:d for d in new},5,'새 K-means 앙상블','#4E947D'))):
        for ax,key in zip(axes,('f1','normal_false_positive_rate')):ax.bar(x+(j-1.5)*w,[source[d['dataset']]['metrics'][mi][key]*100 for d in new],w,label=label,color=color)
    for ax,title in zip(axes,('시나리오별 F1','시나리오별 정상 오탐')):
        ax.set_xticks(x,[SCENARIOS[d['dataset']] for d in new]);ax.set_title(title);ax.set_ylabel('%');ax.set_ylim(bottom=0);ax.legend(ncol=2);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.tight_layout();return fig


def metrics(records):return previous.metrics(records)
def distributions():return previous.margin_histograms('v12')


def confirmation():
    rows=[]
    for name,splits in read(name='validation_results').items():
        for i,r in enumerate(splits['confirmation']['pooled']):rows.append({'설정':name,'모델':LABELS[i],**{label:r[k]*100 for label,k in
            (('Accuracy (%)','accuracy'),('Precision (%)','precision'),('Recall (%)','recall'),('F1 (%)','f1'),('정상 오탐 (%)','normal_false_positive_rate'))}})
    return pd.DataFrame(rows).round(2)


def ablations():
    rows=[]
    for algorithm,modes in read('v11',name='ablations')['results'].items():
        for mode,summary in modes.items():
            for i,r in enumerate(summary['pooled']):rows.append({'알고리즘':algorithm,'제거/설정':mode,'경로':('전체','Adapter','앙상블')[i],
                'F1 (%)':r['f1']*100,'Recall (%)':r['recall']*100,'정상 오탐 (%)':r['normal_false_positive_rate']*100})
    return pd.DataFrame(rows).round(2)
