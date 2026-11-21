"""Latest frozen alignment result, ordinary bars and actual threshold only."""
import argparse
import numpy as np
import matplotlib.pyplot as plt
from ..common import read_json
from ..benchmark import load_split
from ..report import TASKS
from .run import OUT,OLD
from .model import HORIZONS

METRICS=('accuracy','precision','recall','f1');LABELS=('Accuracy','Precision','Recall','F1')
COLORS=('#446a91','#32958e','#de9450','#9b78af')


def save(fig,name):
    (OUT/'figures').mkdir(exist_ok=True);fig.tight_layout();fig.savefig(OUT/'figures'/name,dpi=150,bbox_inches='tight');plt.close(fig)


def main():
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':11,
                         'axes.spines.top':False,'axes.spines.right':False})
    r=read_json(OUT/'results.json')['results'];old=read_json(OLD/'results.json')['results']['1']
    fig,ax=plt.subplots(figsize=(12,4.8));x=np.arange(5)
    for j,(m,label,color) in enumerate(zip(METRICS,LABELS,COLORS)):
        v=[100*r[str(h)]['median'][m] for h in HORIZONS];b=ax.bar(x+(j-1.5)*.19,v,width=.18,label=label,color=color)
        ax.bar_label(b,labels=[f'{s:.1f}' for s in v],padding=4,fontsize=9)
    ax.set_xticks(x,[f'{h}시간' for h in HORIZONS]);ax.set_ylim(0,112)
    ax.set_ylabel('6개 시나리오별 지표의 중앙값 (%)');ax.set_title('현재 저장 모델의 평가 결과')
    ax.legend(ncol=4,frameon=False,loc='upper left');save(fig,'metrics_median.png')
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),gridspec_kw={'width_ratios':[4,1]})
    for j,(label,data,color) in enumerate((('이전',old,COLORS[0]),('현재',r['1'],COLORS[1]))):
        v=[100*data['median'][m] for m in METRICS];b=axes[0].bar(np.arange(4)+(j-.5)*.36,v,width=.34,label=label,color=color)
        axes[0].bar_label(b,labels=[f'{s:.2f}' for s in v],padding=4,fontsize=9)
    axes[0].set_xticks(np.arange(4),LABELS);axes[0].set_ylim(0,105);axes[0].set_ylabel('시나리오별 중앙값 (%)')
    axes[0].legend(frameon=False);axes[0].set_title('동일한 1시간 평가 자료의 개선 폭')
    v=[100*d['maximum_scenario_fpr'] for d in (old,r['1'])];b=axes[1].bar(['이전','현재'],v,color=COLORS[:2])
    axes[1].bar_label(b,labels=[f'{s:.2f}%' for s in v],padding=4);axes[1].set_ylim(0,max(v)*1.3)
    axes[1].set_title('최대 오탐률');axes[1].set_ylabel('정상 시간 중 잘못 경보한 비율 (%)');save(fig,'one_hour_change.png')
    for metric in ('recall','f1'):
        fig,axes=plt.subplots(2,3,figsize=(14,7.5),sharey=True)
        for ax,t in zip(axes.flat,TASKS):
            v=[100*r[str(h)]['scenarios'][t][metric] for h in HORIZONS]
            b=ax.bar([f'{h}h' for h in HORIZONS],v,color=COLORS[2 if metric=='recall' else 3],width=.65)
            ax.bar_label(b,labels=[f'{s:.1f}' for s in v],padding=4,fontsize=10)
            ax.set_ylim(0,112);ax.set_title(TASKS[t]);ax.set_ylabel(f'{metric.upper()} (%)')
        save(fig,f'scenario_{metric}.png')
    d=load_split('test',True);scores=[]
    for folder in (OLD,OUT):
        with np.load(folder/'scores_1h.npz') as z:scores.append(z['score']/float(z['threshold']))
    left=min(-1.,*(float(np.quantile(s[np.isfinite(s)],.001)) for s in scores))
    right=max(3.,*(min(10.,float(np.quantile(s[np.isfinite(s)],.99))) for s in scores))
    edges=np.linspace(left,right,29);width=edges[1]-edges[0];fig,axes=plt.subplots(1,2,figsize=(14,4.8),sharex=True,sharey=True)
    for ax,s,title in zip(axes,scores,('이전 1시간 모델','현재 1시간 모델')):
        valid=np.isfinite(s)
        for offset,truth,label,color in ((-.21,False,'정상 정답',COLORS[0]),(.21,True,'이상 정답',COLORS[2])):
            v=s[valid&(d['y']==truth)];clipped=np.clip(v,np.nextafter(left,right),np.nextafter(right,left))
            counts,_=np.histogram(clipped,bins=edges);ax.bar(edges[:-1]+width/2+offset*width,100*counts/len(v),width=width*.4,label=label,color=color)
        ax.axvline(1,color='#ad3c46',ls='--',label='실제 경보 기준 = 1');ax.set_title(title)
        ax.set_xlabel('점수 / 실제 threshold');ax.set_ylabel('집단 내 비중 (%)');ax.set_xlim(left,right)
    axes[0].legend(frameon=False,fontsize=9);fig.suptitle('동일한 축으로 비교 · 범위 밖 값은 양끝 막대에 포함',fontsize=11,y=1.03)
    save(fig,'score_distribution.png');print('Five alignment figures generated',flush=True)


if __name__=='__main__':argparse.ArgumentParser(description=__doc__).parse_args();main()
