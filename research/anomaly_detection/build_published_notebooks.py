"""Build compact latest-only notebooks from aggregate, publicly copied results."""
from pathlib import Path
import argparse
import sys
import base64
import nbformat as nbf
from nbclient import NotebookClient
from jupyter_client import KernelManager

ROOT = Path(__file__).resolve().parent


def markdown(s):
    return nbf.v4.new_markdown_cell(s)


def code(s):
    return nbf.v4.new_code_cell(s.strip())


SETUP = """
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display
ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p/'latest_inventory.json').exists())
read = lambda p: json.loads((ROOT/p).read_text(encoding='utf-8'))
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
colors = ['#2563eb','#d97706','#059669']
"""


def short_notebook():
    return nbf.v4.new_notebook(cells=[
        markdown("# 단기 이상 탐지 — 최신 결과\n\n단일 IF·K-means·LOF의 최신 전체 파이프라인 결과다. 정상 참고·시계열 피처·경보 보정을 포함한다. 개발 벤치마크를 반복 사용했으므로 외부 블라인드 성능이 아니다. 아래 셀은 집계 JSON만 읽으며 원본·벤치마크·모델 로드가 필요 없다."),
        code(SETUP + "\ns = read('reports/shared_model_v7/release/summary.json')\nalg = ['if','kmeans','lof']\nnames = {'if':'IF','kmeans':'K-means','lof':'LOF'}\nmetrics = ['accuracy','precision','recall','f1']\noverall = pd.DataFrame(s['pooled']).set_index('method').loc[alg]\ndisplay((overall[metrics+['normal_false_positive_rate']]*100).round(3))"),
        markdown("## 1. 전체 성능\n\n전체 분 판정의 혼동행렬을 합산했다. Accuracy는 전체 정답률, Precision은 경보 중 실제 이상 비율, Recall은 실제 이상 중 탐지 비율, F1은 Precision·Recall의 조화평균이다. FPR은 정상 중 잘못 경보한 비율이다."),
        code("""
fig, axes = plt.subplots(1,4,figsize=(15,3.6),sharey=True)
for ax, metric in zip(axes,metrics):
    bars = ax.bar([names[a] for a in alg],overall[metric]*100,color=colors,width=.65)
    ax.bar_label(bars,fmt='%.1f',padding=3)
    ax.set(title=metric.capitalize(),ylim=(0,105),ylabel='Percent' if ax is axes[0] else '')
fig.tight_layout(); display(fig); plt.close(fig)
"""),
        markdown("## 2. 시나리오별 차이\n\n각 시나리오에 정상·이상 구간이 함께 포함된다. 모델마다 특화된 비교 기준과 통계 보정까지 포함한 관측 결과이며 알고리즘 자체의 보편적 순위를 뜻하지 않는다."),
        code("""
rows = [{'scenario':d['dataset'],**r} for d in s['datasets'] for r in d['metrics']]
task = pd.DataFrame(rows)
display(task[['scenario','method',*metrics,'normal_false_positive_rate']].assign(**{k:task[k]*100 for k in metrics+['normal_false_positive_rate']}).round(3))
order = [d['dataset'] for d in s['datasets']]
labels = {'individual_delay':'Individual delay','group_delay':'Group delay','time_burst':'Traffic burst','input_stop':'Input stop','gradual_degradation':'Gradual change'}
fig, axes = plt.subplots(1,2,figsize=(15,4))
for ax, metric in zip(axes,['f1','recall']):
    x=np.arange(len(order))
    for j,a in enumerate(alg):
        values=task[task.method.eq(a)].set_index('scenario').loc[order,metric].to_numpy()*100
        bars=ax.bar(x+(j-1)*.24,values,width=.22,color=colors[j],label=names[a])
        ax.bar_label(bars,fmt='%.0f',padding=2,fontsize=8)
    ax.set(title=metric.upper(),ylim=(0,110),xticks=x,xticklabels=[labels[t] for t in order],ylabel='Percent')
    ax.tick_params(axis='x',rotation=15); ax.legend(frameon=False)
fig.tight_layout(); display(fig); plt.close(fig)
"""),
        markdown("## 3. 점수 분포와 실제 경보선\n\n점수 **1 초과**가 경보다. 확률이 아니며 0~1 범위로 제한되지 않는다. 정상/이상은 각 집단 내부 백분율로 표시한다. 가독성을 위해 −1 미만·3 초과는 양 끝 막대에 합산했으며 표에 해당 건수를 표시한다. 행을 버리거나 정답을 확장하지 않았다."),
        code("""
h=read('reports/shared_model_v7/release/score_histograms.json')
edges=np.array(h['edges']); centers=(edges[:-1]+edges[1:])/2; width=np.diff(edges)[0]
fig,axes=plt.subplots(3,1,figsize=(12,10),sharex=True)
tails=[]
for ax,a in zip(axes,alg):
    for label,color,shift in [('normal','#2563eb',-.21),('anomaly','#dc2626',.21)]:
        records=[r[label] for r in h['models'][a]]
        counts=np.sum([r['counts'] for r in records],axis=0); total=sum(r['n'] for r in records)
        ax.bar(centers+shift*width,100*counts/total,width=.42*width,color=color,label=label)
        tails.append({'model':names[a],'label':label,'rows':total,'below_-1':sum(r['below_plot_range'] for r in records),'above_3':sum(r['above_plot_range'] for r in records)})
    ax.axvline(1,color='black',linestyle='--',label='Alarm: score > 1')
    ax.set(title=names[a],ylabel='Within-class percent'); ax.legend(frameon=False)
axes[-1].set_xlabel('Pipeline anomaly score (edge bars include tails)')
fig.tight_layout(); display(fig); plt.close(fig)
display(pd.DataFrame(tails))
"""),
        markdown("## 핵심 해석\n\n- IF는 개별 지연, K-means는 집단 지연, LOF는 점진적 악화에서 상대적인 F1 강점이 관측됐다.\n- 낮은 FPR과 높은 Precision만으로 이상 누락이 적다고 말할 수 없다. Recall도 함께 확인한다.\n- 입력 중단·폭주는 Process 한 행만으로 판단하지 않고 수집 확인과 연속 실행 건수 이력이 필요하다.\n- 새 데이터에 재학습한 baseline은 별도 모델이며 이 성능표를 승계하지 않는다.\n- 피처 중요도 상세는 `code/shared_model_v7/feature_importance.ipynb`, 입력·재학습은 상위 INTEGRATION/REPRODUCING 문서에 있다."),
    ])


def long_notebook():
    return nbf.v4.new_notebook(cells=[
        markdown("# 장기 추세 모델 — 최신 확인 결과\n\n원래 Process 컬럼을 받고 선택한 1·4·8·12·24시간 내부를 5분 단위로 집계한다. 미래 예측이 아니라 해당 구간의 회고적 이상 판정이다. 별도 검토한 정상 참고 28일과 수집 확인이 필요하다. 아래는 모델 고정 후 생성한 반합성 확인용 576개 사례의 집계 결과다. 실제 운영 장애 검증이나 공식 ADBench 성능이 아니다."),
        code(SETUP + "\nr=read('reports/long_term_anomaly/five_minute_localized/confirmation_results.json')['results']\nhours=[1,4,8,12,24]\nmetrics=['accuracy','precision','recall','f1']\nrows=[{'hours':h,**r[str(h)]['median'],'max_scenario_fpr':r[str(h)]['maximum_scenario_fpr']} for h in hours]\ntable=pd.DataFrame(rows).set_index('hours')\ndisplay((table*100).round(3))"),
        markdown("## 1. 구간 길이에 따른 성능\n\n네 지표는 **시나리오별 지표의 중앙값**이다. 모든 개별 시나리오의 최솟값이나 전체 시간 합산 수치가 아니다. FPR 열은 가장 높은 시나리오의 값이다. 4시간 Recall 중앙값은 69.97%로 70%보다 조금 낮다."),
        code("""
fig,axes=plt.subplots(1,4,figsize=(15,3.8),sharey=True)
for ax,k in zip(axes,metrics):
    bars=ax.bar([str(h)+'h' for h in hours],table[k]*100,color='#2563eb',width=.65)
    ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=9)
    ax.set(title=k.capitalize(),ylim=(0,110),ylabel='Scenario median (%)' if ax is axes[0] else '')
fig.tight_layout(); display(fig); plt.close(fig)
"""),
        markdown("## 2. 시나리오별 실제 수치\n\n중앙값에 가려지는 약점을 확인한다. 점진적 악화 초기와 일주기 변화는 여전히 어려우며, 집계 해상도·정상 참고 품질에 영향을 받는다. 정답 구간 확장이나 point adjustment는 사용하지 않았다."),
        code("""
details=pd.DataFrame([{'hours':h,'scenario':name,**v} for h in hours for name,v in r[str(h)]['scenarios'].items()])
display(details[['hours','scenario',*metrics,'false_positive_rate']].assign(**{k:details[k]*100 for k in metrics+['false_positive_rate']}).round(3))
pivot=details.pivot(index='scenario',columns='hours',values='f1')*100
display(pivot.round(2))
fig,ax=plt.subplots(figsize=(8,4))
bars=ax.bar([str(h)+'h' for h in hours],table.max_scenario_fpr*100,color='#d97706',width=.65)
ax.bar_label(bars,fmt='%.2f',padding=3)
ax.set(ylabel='Maximum scenario FPR (%)',xlabel='Selected analysis interval',ylim=(0,max(table.max_scenario_fpr*100)*1.25))
fig.tight_layout(); display(fig); plt.close(fig)
"""),
        markdown("## 적용 시 주의\n\n- 선택 구간 밖 감시 로그·경보 상태는 사용하지 않지만 미리 고정한 정상 참고는 별도로 사용한다.\n- 로그가 없다는 이유로 수집 상태를 정상으로 가정하지 않는다. 수집 불명은 판단 불가다.\n- 같은 Adapter+Process의 정상 참고를 등록해야 하며, 미등록 집단을 임의의 정상 통계로 대체하지 않는다.\n- 원본 데이터에는 실제 검토된 연속 28일 정상 참고가 확보되지 않았다. 이번 결과는 반합성 연구 검증이다.\n- 원래 시간당 1점 평가와 다른 자료이므로 새 수치를 예전 평가 대비 향상 폭으로 주장하지 않는다."),
    ])


def build(execute=False):
    folder=ROOT/'notebooks'; folder.mkdir(exist_ok=True)
    for name,make in [('latest_short_results',short_notebook),('latest_long_results',long_notebook)]:
        nb=make(); nb.metadata.kernelspec={'display_name':'Python 3','language':'python','name':'python3'}
        if execute:
            manager=KernelManager(kernel_name='python3')
            manager.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
            NotebookClient(nb,timeout=180,resources={'metadata':{'path':str(ROOT)}},km=manager).execute()
            previews=ROOT/'outputs/notebook_previews'; previews.mkdir(parents=True,exist_ok=True)
            for cell_index,cell in enumerate(nb.cells):
                for output_index,output in enumerate(cell.get('outputs',[])):
                    data=output.get('data',{}).get('image/png')
                    if data:
                        (previews/f'{name}-{cell_index}-{output_index}.png').write_bytes(base64.b64decode(data))
        nbf.validate(nb); nbf.write(nb,folder/(name+'.ipynb'))
        print('EXECUTED' if execute else 'BUILT',name,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--execute',action='store_true')
    build(parser.parse_args().execute)
