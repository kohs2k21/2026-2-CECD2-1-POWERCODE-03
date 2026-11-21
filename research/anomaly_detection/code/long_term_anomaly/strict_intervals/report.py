"""Read-only interpretation of frozen current results; ordinary bars, no target lines."""
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from ..common import read_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..report import TASKS
from .model import HORIZONS,load_bank
from .run import OUT

METRICS=('accuracy','precision','recall','f1')
LABELS=('Accuracy','Precision','Recall','F1')
COLORS=('#446a91','#32958e','#de9450','#9b78af')


def context():
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':11,
        'axes.spines.top':False,'axes.spines.right':False})
    r=read_json(OUT/'results.json');assert sha256(OUT/'models.joblib')==r['model_sha256']
    return r


def table(mode='median'):
    r=context()
    return pd.DataFrame([{'분석 구간':f'{h}시간',**{label:100*r['results'][str(h)][mode][k] for k,label in zip(METRICS,LABELS)},
        '전체 오탐률':100*r['results'][str(h)]['pooled']['false_positive_rate'],
        '시나리오 최대 오탐률':100*r['results'][str(h)]['maximum_scenario_fpr']} for h in HORIZONS]).round(2)


def scenario_table(h):
    r=context()['results'][str(h)]
    return pd.DataFrame([{'시나리오':TASKS[t],**{label:100*s[k] for k,label in zip(METRICS,LABELS)},
                          '오탐률':100*s['false_positive_rate']} for t,s in r['scenarios'].items()]).round(2)


def save(fig,name):
    (OUT/'figures').mkdir(exist_ok=True);fig.tight_layout()
    fig.savefig(OUT/'figures'/name,dpi=150,bbox_inches='tight');return fig


def metric_plot(mode='median'):
    r=context();fig,ax=plt.subplots(figsize=(12,4.8));x=np.arange(5)
    for i,(m,label,color) in enumerate(zip(METRICS,LABELS,COLORS)):
        v=[100*r['results'][str(h)][mode][m] for h in HORIZONS]
        bars=ax.bar(x+(i-1.5)*.19,v,width=.18,label=label,color=color)
        ax.bar_label(bars,labels=[f'{n:.1f}' for n in v],padding=4,fontsize=9)
    ax.set_xticks(x,[f'{h}시간' for h in HORIZONS]);ax.set_ylim(0,112)
    ax.set_xlabel('선택한 감시 구간 길이 · 이전 배치 상태 사용 없음')
    ax.set_ylabel('시나리오별 지표 중앙값 (%)' if mode=='median' else '전체 시간 통합 지표 (%)')
    ax.set_title('선택한 구간만 사용한 실제 평가 성능');ax.legend(ncol=4,frameon=False,loc='upper left')
    return save(fig,f'metrics_{mode}.png')


def fpr_plot():
    r=context();fig,ax=plt.subplots(figsize=(10,4));x=np.arange(5)
    maximum=0.
    for offset,key,label,color in ((-.18,'pooled','전체 오탐률',COLORS[0]),(.18,'max','시나리오 최대 오탐률',COLORS[2])):
        v=[100*(r['results'][str(h)]['pooled']['false_positive_rate'] if key=='pooled'
                  else r['results'][str(h)]['maximum_scenario_fpr']) for h in HORIZONS]
        bars=ax.bar(x+offset,v,width=.34,label=label,color=color)
        ax.bar_label(bars,labels=[f'{n:.2f}%' for n in v],padding=4);maximum=max(maximum,max(v))
    ax.set_xticks(x,[f'{h}시간' for h in HORIZONS]);ax.set_ylim(0,maximum*1.35)
    ax.set_ylabel('정상 시간 중 잘못 경보한 비중 (%)');ax.set_title('경보 기준 보정 후 실제 오탐률')
    ax.legend(frameon=False,loc='upper left');return save(fig,'false_positive_rates.png')


def scenario_plot(metric='recall'):
    r=context();fig,axes=plt.subplots(2,3,figsize=(14,7.5),sharey=True)
    for ax,t in zip(axes.flat,TASKS):
        v=[100*r['results'][str(h)]['scenarios'][t][metric] for h in HORIZONS]
        b=ax.bar([f'{h}h' for h in HORIZONS],v,width=.65,color=COLORS[2 if metric=='recall' else 3])
        ax.bar_label(b,labels=[f'{n:.1f}' for n in v],padding=4,fontsize=10)
        ax.set_ylim(0,112);ax.set_title(TASKS[t]);ax.set_ylabel(f'{metric.upper()} (%)')
    return save(fig,f'scenario_{metric}.png')


def score_distributions():
    context();d=load_split('test',True);fig,axes=plt.subplots(5,1,figsize=(12,14))
    for ax,h in zip(axes,HORIZONS):
        with np.load(OUT/f'scores_{h}h.npz') as f:s=f['score']/float(f['threshold'])
        valid=np.isfinite(s);limit=max(3.,min(10.,float(np.quantile(s[valid],.99))))
        bins=np.linspace(0,limit,25);width=np.diff(bins)[0]
        for offset,truth,label,color in ((-.21,False,'정상 정답',COLORS[0]),(.21,True,'이상 정답',COLORS[2])):
            v=s[valid&(d['y']==truth)];clipped=np.minimum(v,np.nextafter(limit,0))
            counts,_=np.histogram(clipped,bins=bins);heights=100*counts/len(v)
            ax.bar(bins[:-1]+width/2+offset*width,heights,width=width*.4,label=label,color=color,alpha=.9)
        ax.axvline(1,color='#ad3c46',linestyle='--',label='실제 경보 기준 = 1')
        ax.set_ylabel(f'{h}시간\n집단 내 비중 (%)');ax.set_xlim(0,limit)
        ax.set_title(f'마지막 막대는 {bins[-2]:.2f} 이상 전체 포함',fontsize=10,loc='right')
    axes[0].legend(ncol=3,frameon=False);axes[-1].set_xlabel('탐지 점수 / 실제 threshold · 정상·이상 집단을 각각 100%로 표시')
    return save(fig,'score_distributions.png')


def timeline(task='gradual_degradation'):
    context();d=load_split('test',True);meta=metadata_for_split('test',d['case_id'])
    i=int(np.flatnonzero(meta.scenario.eq(task)&meta.injected)[0]);fig,axes=plt.subplots(5,1,figsize=(12,9),sharex=True)
    t=np.arange(336)/24
    for ax,h in zip(axes,HORIZONS):
        with np.load(OUT/f'scores_{h}h.npz') as f:s=f['score'][i]/float(f['threshold'])
        ax.step(t,s,where='post',color=COLORS[0],lw=1)
        ax.axhline(1,color='#ad3c46',ls='--',label='실제 경보 기준 = 1')
        ax.fill_between(t,0,1,where=d['y'][i],transform=ax.get_xaxis_transform(),color=COLORS[2],alpha=.18)
        ax.set_ylabel(f'{h}시간\n점수/기준')
    axes[0].set_title(f'{TASKS[task]} · 점수로 고르지 않은 첫 주입 사례 {d["case_id"][i]}')
    axes[0].legend(frameon=False);axes[-1].set_xlabel('감시 시작 후 경과일 · 주황 배경은 원래 이상 정답, 점수는 각 배치 종료 후 제공')
    return save(fig,f'timeline_{task}.png')


def reasons_table():
    context();d=load_split('test',True);meta=metadata_for_split('test',d['case_id']);rows=[]
    for h in HORIZONS:
        with np.load(OUT/f'scores_{h}h.npz') as f:s,t,reasons=f['score'],float(f['threshold']),f['reason']
        for task in sorted(meta.scenario.unique()):
            group=meta.scenario.eq(task).to_numpy()[:,None]
            for positive,label in ((True,'정답 이상 탐지'),(False,'정상 오탐')):
                keep=group&(d['y']==positive)&np.isfinite(s)&(s>t)
                values,counts=np.unique(reasons[keep],return_counts=True)
                rows.extend(dict(analysis_hours=h,scenario=task,result=label,reason=str(reason),hour_count=int(n)) for reason,n in zip(values,counts))
    frame=pd.DataFrame(rows);frame.to_csv(OUT/'detection_reasons.csv',index=False,encoding='utf-8-sig');return frame


def summary():
    r=context();lines=['# 선택한 구간만 사용하는 장기 모델 개선 결과','','입력 집계는 1시간, 정상 참고 구간은 28일이다. 이전·다음 감시 배치나 경보 상태는 사용하지 않았다.','',
        '| 구간 | Accuracy 중앙값 | Precision 중앙값 | Recall 중앙값 | F1 중앙값 | 최대 시나리오 오탐률 |','|---:|---:|---:|---:|---:|---:|']
    for h in HORIZONS:
        a=r['results'][str(h)];lines.append(f'| {h}시간 | '+' | '.join(f'{100*a["median"][k]:.2f}%' for k in METRICS)+f' | {100*a["maximum_scenario_fpr"]:.2f}% |')
    passing=[h for h in HORIZONS if r['results'][str(h)]['target_met']]
    failing=[h for h in HORIZONS if not r['results'][str(h)]['target_met']]
    lines+=['',f'네 지표 중앙값 각각 65% 이상·전체 및 시나리오별 오탐률 6% 이하를 모두 충족한 길이: {passing}.',
        f'미충족 길이: {failing}. 전체 목표 달성: {r["goal_met_all_lengths"]}.','',
        '중앙값 통과가 모든 시나리오의 성공은 아니다. '
        f'4시간 지속 입력 감소 Recall은 {100*r["results"]["4"]["scenarios"]["sustained_input_drop"]["recall"]:.2f}%, '
        f'24시간 일일 패턴 변화 Recall은 {100*r["results"]["24"]["scenarios"]["daily_pattern_change"]["recall"]:.2f}%로 낮다. '
        '개별 시나리오의 큰 약점은 별도 개선 대상으로 남는다.','',
        '## 개선 내용','',
        '- 비대칭 정상 분포의 상·하위 편차를 분리하고 시간대별 정상 중앙값 사용',
        '- 같은 길이의 정상 구간과 현재 구간의 평균·중앙값·변동성 비교',
        '- 정상 구간 통계의 희소도와 요청 수·소요시간별 가중치를 개발셋에서 선택',
        '- 실제 모델은 시간대 정상분포 + 구간 통계·밀도 모델. 최종 코드에는 감시 상태 누적이나 PELT 변화점 분할 없음','',
        '## 해석과 한계','',
        '- 배치당 하나의 점수를 관측된 시간들에 제공한다. 장애 시작 시각을 정확히 찾아내는 모델이라는 뜻은 아니다.',
        '- 모든 원래 관측 시간을 평가했다. 배치 정답 확장·point adjustment·시나리오 제외 없음.',
        '- 1시간 모드는 한 점이므로 지속성·주기 변화·변동성 증가를 직접 볼 수 없다. 이번 미달이 모든 가능한 모델의 수학적 불가능성을 증명하지는 않는다.',
        '- 순서 정보 부족을 숨기려고 이전 감시 자료를 사용하거나 평가셋 난도를 바꾸지 않았다.',
        '- 이전에 보고한 반합성 평가셋을 재사용한 연구 비교다. 새 독립 블라인드 평가나 실제 장애 성능이 아니다.',
        '- 정상 자료 가상 변형 기반 자기지도 후보와 결합 밀도 후보도 개발셋에서 비교했으나 더 좋지 않아 최종 모델에서 제외했다.',
        '- 처리 건수 컬럼은 입력 호환용이며 최종 점수에 사용하지 않는다. 실제 정상 대량 처리에 대한 추가 검증 필요.','',
        '실행된 결과와 분포·탐지 근거는 code/long_term_anomaly/strict_intervals/result.ipynb에서 확인할 수 있다.','']
    (OUT/'summary.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    figures=[metric_plot(),metric_plot('pooled'),fpr_plot(),scenario_plot(),scenario_plot('f1'),score_distributions(),timeline()]
    for f in figures:plt.close(f)
    reasons_table();summary();print('Current strict results: 7 figures, tables, interpretation written',flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();main()
