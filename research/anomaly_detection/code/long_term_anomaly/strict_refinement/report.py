"""Latest measured refinement results, readable bars, no minimum-target lines."""
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from ..common import REPORT,read_json,sha256
from ..benchmark import load_split
from ..experiment import metadata_for_split
from ..report import TASKS
from .experiment import OUT
from .model import HORIZONS

METRICS=('accuracy','precision','recall','f1');LABELS=('Accuracy','Precision','Recall','F1')
COLORS=('#446a91','#32958e','#de9450','#9b78af')


def context():
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':11,
                         'axes.spines.top':False,'axes.spines.right':False})
    r=read_json(OUT/'results.json');assert sha256(OUT/'models.joblib')==r['model_sha256'];return r


def table(mode='median'):
    r=context()
    return pd.DataFrame([{'분석 구간':f'{h}시간',**{label:100*r['results'][str(h)][mode][m] for m,label in zip(METRICS,LABELS)},
        '전체 오탐률':100*r['results'][str(h)]['pooled']['false_positive_rate'],
        '시나리오 최대 오탐률':100*r['results'][str(h)]['maximum_scenario_fpr']} for h in HORIZONS]).round(2)


def scenario_table(h=1):
    r=context()['results'][str(h)]
    return pd.DataFrame([{'시나리오':TASKS[t],**{label:100*v[m] for m,label in zip(METRICS,LABELS)},
                          '오탐률':100*v['false_positive_rate']} for t,v in r['scenarios'].items()]).round(2)


def development_study_table():
    """Keep exploratory development numbers separate from frozen test results."""
    calibration=read_json(OUT/'calibration.json')['development']['1']
    rows=[{'개발셋 비교': '현재 저장 모델', **calibration['median'],
           'max_fpr':calibration['maximum_scenario_fpr'], '최종 모델 반영':True}]
    for label,name in (
        ('정상 변동 폭 공동 추정','scale_shrinkage_search.json'),
        ('시간대·추세·자기상관 보정','preprocessing_search.json'),
        ('집단별 경보 점수 보정','score_calibration_search.json'),
        ('정상 관측 분포 직접 학습','empirical_background_search.json'),
        ('실행 횟수·소요시간 공동 가설','joint_evidence_search.json'),
        ('작은 이동·변동 가설 세분화','flexible_hypothesis_search.json'),
        ('정상 분위수에 맞춘 분포 보정','quantile_warp_search.json'),
        ('정상 과거 1·2·4·8시점 관계 학습','ar_sieve_search.json'),
        ('정상 집단별 경보 기준 회귀 보정','conditional_calibration_search.json')):
        r=read_json(OUT/name)
        assert r['normal_fit_only'] is True and r['test_used'] is False
        best=r['best']
        rows.append({'개발셋 비교':label, **{m:best[m] for m in METRICS},
                     'max_fpr':best['max_fpr'],'최종 모델 반영':False})
    contrastive=read_json(OUT/'ar_contrastive_search.json')
    assert not contrastive['test_used'] and not contrastive['benchmark_anomaly_labels_used_for_fit']
    best=max(contrastive['candidates'],key=lambda r:(min(r[m] for m in METRICS),r['f1']))
    rows.append({'개발셋 비교':'정상 AR 피처·인공 잡음 구분 학습',**{m:best[m] for m in METRICS},
                 'max_fpr':best['max_fpr'],'최종 모델 반영':False})
    df=pd.DataFrame(rows)
    for m in (*METRICS,'max_fpr'):df[m]*=100
    return df.rename(columns=dict(zip(METRICS,LABELS))|{'max_fpr':'시나리오 최대 오탐률(%)'}).round(2)


def save(fig,name):
    (OUT/'figures').mkdir(exist_ok=True);fig.tight_layout()
    fig.savefig(OUT/'figures'/name,dpi=150,bbox_inches='tight');return fig


def metric_plot(mode='median'):
    r=context();fig,ax=plt.subplots(figsize=(12,4.8));x=np.arange(5)
    for j,(metric,label,color) in enumerate(zip(METRICS,LABELS,COLORS)):
        v=[100*r['results'][str(h)][mode][metric] for h in HORIZONS]
        bars=ax.bar(x+(j-1.5)*.19,v,width=.18,label=label,color=color)
        ax.bar_label(bars,labels=[f'{n:.1f}' for n in v],padding=4,fontsize=9)
    ax.set_xticks(x,[f'{h}시간' for h in HORIZONS]);ax.set_ylim(0,112)
    ax.set_xlabel('선택한 감시 구간 길이 · 감시 배치 사이 상태 전달 없음')
    ax.set_ylabel('시나리오별 지표 중앙값 (%)' if mode=='median' else '모든 관측 시간 통합 지표 (%)')
    ax.set_title('현재 모델의 벤치마크 평가 성능');ax.legend(ncol=4,frameon=False,loc='upper left')
    return save(fig,f'metrics_{mode}.png')


def fpr_plot():
    r=context();fig,ax=plt.subplots(figsize=(10,4));x=np.arange(5);maximum=0.
    for offset,key,label,color in ((-.18,'pooled','전체 오탐률',COLORS[0]),(.18,'maximum','시나리오 최대 오탐률',COLORS[2])):
        v=[100*(r['results'][str(h)]['pooled']['false_positive_rate'] if key=='pooled'
                else r['results'][str(h)]['maximum_scenario_fpr']) for h in HORIZONS]
        b=ax.bar(x+offset,v,width=.34,label=label,color=color)
        ax.bar_label(b,labels=[f'{n:.2f}%' for n in v],padding=4);maximum=max(maximum,max(v))
    ax.set_xticks(x,[f'{h}시간' for h in HORIZONS]);ax.set_ylim(0,maximum*1.35)
    ax.set_ylabel('정상 시간 중 잘못 경보한 비중 (%)');ax.set_title('경보 기준 고정 후 실제 오탐률')
    ax.legend(frameon=False,loc='upper left');return save(fig,'false_positive_rates.png')


def scenario_plot(metric='recall'):
    r=context();fig,axes=plt.subplots(2,3,figsize=(14,7.5),sharey=True)
    for ax,t in zip(axes.flat,TASKS):
        v=[100*r['results'][str(h)]['scenarios'][t][metric] for h in HORIZONS]
        b=ax.bar([f'{h}h' for h in HORIZONS],v,width=.65,color=COLORS[2 if metric=='recall' else 3])
        ax.bar_label(b,labels=[f'{n:.1f}' for n in v],padding=4,fontsize=10)
        ax.set_ylim(0,112);ax.set_title(TASKS[t]);ax.set_ylabel(f'{metric.upper()} (%)')
    return save(fig,f'scenario_{metric}.png')


def point_distributions():
    context();d=load_split('test',True);fig,axes=plt.subplots(1,2,figsize=(14,4.8),sharex=True,sharey=True)
    saved=[]
    for folder in (REPORT/'strict_intervals',OUT):
        with np.load(folder/'scores_1h.npz') as z:saved.append(z['score']/float(z['threshold']))
    left=min(-1.,*(float(np.quantile(s[np.isfinite(s)],.001)) for s in saved))
    right=max(3.,*(min(10.,float(np.quantile(s[np.isfinite(s)],.99))) for s in saved))
    edges=np.linspace(left,right,29);width=edges[1]-edges[0];maximum=0.
    for ax,folder,title in zip(axes,(REPORT/'strict_intervals',OUT),('이전 1시간 모델','현재 1시간 모델')):
        with np.load(folder/'scores_1h.npz') as z:s=z['score']/float(z['threshold'])
        valid=np.isfinite(s)
        for offset,truth,label,color in ((-.21,False,'정상 정답',COLORS[0]),(.21,True,'이상 정답',COLORS[2])):
            values=s[valid&(d['y']==truth)]
            clipped=np.clip(values,np.nextafter(left,right),np.nextafter(right,left))
            counts,_=np.histogram(clipped,bins=edges)
            heights=100*counts/len(values);maximum=max(maximum,float(heights.max()))
            ax.bar(edges[:-1]+width/2+offset*width,heights,width=width*.4,label=label,color=color)
        ax.axvline(1,color='#ad3c46',ls='--',label='실제 경보 기준 = 1')
        ax.set_title(title);ax.set_xlabel('점수 / 실제 threshold');ax.set_ylabel('집단 내 비중 (%)')
        ax.set_xlim(left,right)
    axes[0].set_ylim(0,maximum*1.18)
    axes[0].legend(frameon=False,loc='upper right',fontsize=9)
    fig.suptitle('동일한 축으로 비교 · 범위 밖 값은 양끝 막대에 모두 포함',fontsize=11,y=1.03)
    return save(fig,'one_hour_score_distribution.png')


def point_timeline():
    context();d=load_split('test',True);meta=metadata_for_split('test',d['case_id']);task='gradual_degradation'
    i=int(np.flatnonzero(meta.scenario.eq(task)&meta.injected)[0]);t=np.arange(336)/24
    fig,axes=plt.subplots(2,1,figsize=(12,6.5),sharex=True)
    for ax,folder,title in zip(axes,(REPORT/'strict_intervals',OUT),('이전 1시간 모델','현재 1시간 모델')):
        with np.load(folder/'scores_1h.npz') as f:s=f['score'][i]/float(f['threshold'])
        shown=np.clip(s,-1,4)
        ax.step(t,shown,where='post',color=COLORS[0],lw=1)
        high=np.flatnonzero(s>4)
        if len(high):
            ax.scatter(t[high],np.full(len(high),4.),marker='^',color='#ad3c46',s=28,zorder=4)
            i_max=int(high[np.argmax(s[high])])
            ax.annotate(f'실제 {s[i_max]:.1f} · 표시 상한 초과',xy=(t[i_max],4),xytext=(t[i_max]+.5,3.3),
                        arrowprops={'arrowstyle':'->','color':'#ad3c46'},fontsize=10,color='#ad3c46')
        ax.axhline(1,color='#ad3c46',ls='--',label='실제 경보 기준 = 1')
        ax.fill_between(t,0,1,where=d['y'][i],transform=ax.get_xaxis_transform(),color=COLORS[2],alpha=.18)
        ax.set_title(title);ax.set_ylabel('점수 / 기준');ax.set_ylim(-1,4.5)
    axes[0].legend(frameon=False);axes[-1].set_xlabel('경과일 · 주황 배경은 정답 구간 · 그림의 상한 초과값은 삼각형과 실제 값으로 표시')
    return save(fig,'one_hour_gradual_case.png')


def summary():
    r=context();lines=['# 현재 장기 이상 탐지 결과','','입력은 선택한 구간만 사용하며, 정상 참고 28일 외의 감시 상태는 유지하지 않는다.','',
        '| 구간 | Accuracy 중앙값 | Precision 중앙값 | Recall 중앙값 | F1 중앙값 | 최대 시나리오 오탐률 |','|---:|---:|---:|---:|---:|---:|']
    for h in HORIZONS:
        a=r['results'][str(h)];lines.append(f'| {h}시간 | '+' | '.join(f'{100*a["median"][m]:.2f}%' for m in METRICS)+f' | {100*a["maximum_scenario_fpr"]:.2f}% |')
    lines+=['',f'전체 길이 목표 달성: {r["goal_met_all_lengths"]}. 미달 길이: {[h for h in HORIZONS if not r["results"][str(h)]["target_met"]]}.','',
        '## 무엇을 개선했는가','',
        '- 정상 학습 64개 구간의 앞 672시간에서만 시간대·정상 잔차·자기상관을 추정했다.',
        '- 정상 잔차의 새 오차 성분을 모아 재표본하고, 자기상관별 정상 흔들림 분포를 학습했다.',
        '- 현재 한 점이 정상 분포와 일반적인 이동·변동성 증가 가설 중 어디에 더 잘 맞는지 비교한다. 실제 이상 정답은 분포 학습에 사용하지 않았다.',
        '- 가중치·변형 범위·점수 결합 방식은 개발셋 정답으로 선택하고, 경보 기준은 정상 보정 자료와 개발셋 정상 시간으로 정했다.',
        '- 4·8·12·24시간 모델과 예측은 기존 모델 그대로다. 모든 동일 관측 시간에서 재계산·대조한다.','',
        '## 해석할 때','',
        '- 중앙값 기준이다. 모든 개별 시나리오가 65% 이상이라는 뜻은 아니다. 모든 시간을 합친 통합 지표도 별도다.',
        '- 1시간 입력은 여전히 한 점이다. 점진적 악화의 현재 값이 평소 분포에서 벗어나는지 볼 뿐, 1시간 안에서 긴 추세를 직접 관측한 것은 아니다.',
        '- 점수는 확률이 아니다. 최대 기여 가설은 통계적 경보 근거이지 장애 원인을 확정하는 분류가 아니다.',
        '- 기존 반합성 평가셋을 재사용한 연구 결과다. 새 독립 블라인드 검증이나 실제 운영 장애 성능이 아니다.',
        '- 평가셋의 정상 연속성도 AR형 가정으로 생성되어 이 모델에 유리할 수 있다. 독립적인 실제 시계열 검증이 필요하다.',
        '- 현재 구간 밖의 감시 로그·상태는 사용하지 않았고 평가셋·주입 강도·정답을 변경하지 않았다.','']
    (OUT/'summary.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    figs=[metric_plot(),metric_plot('pooled'),fpr_plot(),scenario_plot(),scenario_plot('f1'),point_distributions(),point_timeline()]
    for fig in figs:plt.close(fig)
    summary();print('Latest refinement: 7 figures and interpretation written',flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args();main()
