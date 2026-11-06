"""Five-task current report: ordinary bars, readable explanations, executed cells."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from model_research_v13.data import read,write
from shared_model_v5.protocol import TASKS,ALGORITHMS,OUT

LOG=OUT/'release'
LABELS={'individual_delay':'개별 지연','group_delay':'집단 지연','time_burst':'시간대 폭주','input_stop':'입력 중단','gradual_degradation':'점진적 악화'}
NAMES=['IF','K-means','LOF'];COLORS=['#2563eb','#d97706','#059669']

def draw():
    dest=LOG/'figures';dest.mkdir(exist_ok=True);s=read(LOG/'summary.json');sel=read(LOG/'selection.json')
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,'font.size':11})
    fig,ax=plt.subplots(figsize=(12,5));x=np.arange(5)
    for i in range(3):
        bars=ax.bar(x+(i-1)*.26,[d['metrics'][i]['f1']*100 for d in s['datasets']],width=.24,color=COLORS[i],label=NAMES[i]);ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=9)
    ax.axhline(30,ls='--',color='#dc2626',label='task별 최소 30%');ax.set(xticks=x,xticklabels=[LABELS[t]for t in TASKS],ylabel='F1 (%)',ylim=(0,105),title='현재 5개 task F1 · 모두 같은 데이터/정답');ax.legend(ncol=4,fontsize=9)
    fig.tight_layout();fig.savefig(dest/'task_f1.png',dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,4.7))
    for i in range(3):
        v=[d['metrics'][i]['normal_false_positive_rate']*100 for d in s['datasets']]
        bars=ax.bar(x+(i-1)*.26,v,width=.24,color=COLORS[i],label=NAMES[i]);ax.bar_label(bars,labels=[f'{n:.4f}'for n in v],padding=3,fontsize=8)
    ax.axhline(5,color='#dc2626',ls='--',label='5% 미만 필수');ax.set(xticks=x,xticklabels=[LABELS[t]for t in TASKS],ylabel='FP / 정상 전체 (%)',ylim=(0,6.3),title='task별 정상 오탐 · IF의 4.9971%는 경계에 매우 가까움');ax.legend(ncol=4,fontsize=9)
    fig.tight_layout();fig.savefig(dest/'task_fpr.png',dpi=160);plt.close(fig)
    bank=[np.load(LOG/(t+'_scores.npz'))for t in TASKS];y=np.concatenate([z['y']for z in bank]);scores=np.concatenate([z['scores']for z in bank])
    for i,a in enumerate(ALGORITHMS):
        fig,ax=plt.subplots(figsize=(11,4.9));ax.axis('off');rows=[]
        for d in s['datasets']:
            r=d['metrics'][i];rows.append([LABELS[d['dataset']],*[f'{r[k]*100:.2f}%'for k in ('precision','recall','f1')],f"{r['normal_false_positive_rate']*100:.4f}%"])
        r=s['pooled'][i];rows.append(['전체 분 합산',*[f'{r[k]*100:.2f}%'for k in ('precision','recall','f1')],f"{r['normal_false_positive_rate']*100:.4f}%"])
        table=ax.table(cellText=rows,colLabels=['task','Precision','Recall','F1','정상 오탐률'],cellLoc='center',loc='center',colWidths=[.25,.18,.18,.18,.21]);table.auto_set_font_size(False);table.set_fontsize(12);table.scale(1,2.1)
        for(c,r),cell in table.get_celld().items():
            if c==0:cell.set_facecolor(COLORS[i]);cell.set_text_props(color='white',weight='bold')
            elif c%2==0:cell.set_facecolor('#f1f5f9')
        ax.set_title(NAMES[i]+' · 현재 5개 task',fontweight='bold',pad=18)
        g=s['acceptance'][a];fig.text(.5,.07,f"최저 F1 {g['minimum_task_F1']*100:.2f}% · 중앙값 {g['median_task_F1']*100:.2f}% · 최대 오탐 {g['maximum_task_normal_FPR']*100:.4f}%",ha='center')
        fig.tight_layout(rect=(0,.1,1,.95));fig.savefig(dest/(a+'_performance.png'),dpi=160);plt.close(fig)
        v=scores[:,i];threshold=sel['models'][a]['threshold'];lo=min(0.,float(np.quantile(v,.001)));hi=max(float(np.quantile(v,.995)),threshold*1.4,lo+1.)
        edges=np.linspace(lo,hi,43);centers=(edges[:-1]+edges[1:])/2;fig,ax=plt.subplots(figsize=(10,4.2));outside=[]
        for j,(truth,label,color)in enumerate(((False,'정상','#94a3b8'),(True,'이상','#dc2626'))):
            values=v[y==truth];counts,_=np.histogram(values,edges)
            ax.bar(centers+(j-.5)*np.diff(edges)*.44,counts/len(values)*100,width=np.diff(edges)*.42,label=label,color=color)
            outside.append(f'{label} {np.mean((values<lo)|(values>hi))*100:.2f}%')
        ax.axvline(threshold,ls='--',color='black',label=f'경보선 {threshold:.6f}');ax.set(title=NAMES[i]+' 점수 분포 · 표시 밖: '+', '.join(outside),xlabel='보정된 이상 점수 (장애 확률 아님)',ylabel='각 집단 전체 중 비중 (%)');ax.legend(fontsize=9)
        fig.tight_layout();fig.savefig(dest/(a+'_threshold.png'),dpi=160);plt.close(fig)

def report():
    draw();md=nbformat.v4.new_markdown_cell;code=nbformat.v4.new_code_cell
    cells=[md('# 현재 5개 task — 한 task의 실패가 전체 점수에 가려지지 않도록\n\n**각 모델에서 task별 F1 최저 30% 이상 · 5개 F1의 중앙값 60% 이상 · 모든 task와 전체 정상 오탐률 5% 미만.**\n\n이 기준은 통과했다. 그러나 **전체 합산 F1이 모두 오른 것은 아니다. IF는 이전 균형 버전 복원, K-means는 유지, LOF는 취약 task 회복과 다른 task 하락의 교환**이다. 아래에서 동일 5개 task끼리 비교한다.'),
        code("from pathlib import Path\nimport json,numpy as np,pandas as pd\nfrom IPython.display import display,Image,Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents]if(p/'reports/shared_model_v5/release/summary.json').exists())\nLOG=ROOT/'reports/shared_model_v5/release'\nread=lambda p:json.loads(p.read_text(encoding='utf8'))\ns=read(LOG/'summary.json');sel=read(LOG/'selection.json');h=read(LOG/'handoff/manifest.json')\nalg=['if','kmeans','lof'];tasks=s['protocol']['active_tasks']\ndef metrics(rows):\n    return (pd.DataFrame(rows).set_index('method')[['accuracy','precision','recall','f1','normal_false_positive_rate']]*100).round(4)"),
        md('## 1. 평가 범위와 판정 단위\n\n컬럼 관계·Hard 혼합은 현재 평가에서 제외했다. 원본은 `reports/retired_tasks_20261018`에서 복구 가능하다. **남은 5개 task의 CSV·정답·난이도는 변경하지 않았다.** 63개 활성/보관 task 파일의 해시를 확인했다. 기존 하드 룰 전수 검증은 동일 파일에 한해 승계했으며 새로 실행했다고 주장하지 않는다.\n\n각 task 이상 사례 5,000개 + 정상 대조군 5,000개. 평가는 **1,927,972개 1분 판정**, 정상 1,596,121 / 이상 331,851이다. 한 사례는 여러 분이므로 사례 수와 판정 수가 다르다. STATUS는 S. ADBench 데이터·모델·결과는 이번에 바꾸지 않았다.'),
        code("display(s['protocol']);display(metrics(s['pooled']))\ndisplay(pd.DataFrame(s['acceptance']).T)\nassert s['all_targets_met']"),
        md('## 2. task별 F1과 오탐\n\n첫 그래프는 탐지 성능, 두 번째는 정상 데이터를 잘못 경보한 비율이다. 오탐은 FP/(TN+FP). **중앙값은 모델 하나의 task F1 5개를 정렬한 가운데 값**이며 전체 F1이나 3개 모델의 중앙값이 아니다. IF 점진적 악화 오탐 **4.9971%**는 수치상 통과이나 운영 여유가 거의 없다.'),
        code("display(Image(filename=str(LOG/'figures/task_f1.png')))\ndisplay(Image(filename=str(LOG/'figures/task_fpr.png')))\nrows=[]\nfor d in s['datasets']:\n    for r in d['metrics']:rows.append({'task':d['dataset'],**r})\ntable=pd.DataFrame(rows)\ndisplay(table[['task','method','tp','fp','fn','tn','precision','recall','f1','normal_false_positive_rate']].round(6))"),
        md('## 3. 같은 5개 task끼리 전후 비교\n\n제외한 task 때문에 합산 점수가 달라지는 효과를 개선으로 포장하지 않는다. 직전 `shared_model_v4_final`에서도 같은 5개만 뽑아 비교한다. **IF와 LOF는 최약 task를 회복했지만 전체 합산 F1·일부 다른 task는 하락했다. K-means는 같은 모델이므로 task별 성능이 그대로다.**'),
        code("old=read(ROOT/'reports/shared_model_v4_final/summary.json');old_by={d['dataset']:d for d in old['datasets']}\ncomparison=[];pooled=[]\nfor i,a in enumerate(alg):\n    before=[old_by[t]['metrics'][i]for t in tasks];now=[d['metrics'][i]for d in s['datasets']]\n    for t,b,r in zip(tasks,before,now):comparison.append({'task':t,'model':a,'before F1 %':b['f1']*100,'now F1 %':r['f1']*100,'change pp':(r['f1']-b['f1'])*100})\n    tp=sum(r['tp']for r in before);fp=sum(r['fp']for r in before);fn=sum(r['fn']for r in before)\n    pooled.append({'model':a,'before pooled F1 %':200*tp/(2*tp+fp+fn),'now pooled F1 %':s['pooled'][i]['f1']*100,'before minimum F1 %':min(r['f1']for r in before)*100,'now minimum F1 %':s['acceptance'][a]['minimum_task_F1']*100})\ndisplay(pd.DataFrame(pooled).round(2));display(pd.DataFrame(comparison).round(2))"),
        md('## 4. Isolation Forest — 개별 지연 누락을 복구\n\n직전 개별 지연 이상 5,093개 분에서 TP=0이었다. 이전 정상 학습 IF를 복원해 **TP 2,657 / F1 64.30%**로 회복했다. 입력 중단 F1 **86.07%**가 현재 세 모델 중 높다. 반면 점진적 악화 F1은 **39.89%**, 해당 task 오탐은 경계에 가까워 약점이다.\n\n**새 재학습 성과가 아니라 이전 균형 버전 복원이다.** 26개 피처, 500 trees, max_samples=32, max_features=0.5. 정상 공분산 변환과 고정 회전, 관측 상태별 정상 점수 보정 사용.'),
        code("display(Image(filename=str(LOG/'figures/if_performance.png')))\ndisplay(Image(filename=str(LOG/'figures/if_threshold.png')))"),
        md('## 5. MiniBatch K-means — 현재 가장 안정적인 범용 후보\n\n기존 모델을 그대로 유지했다. 모든 task F1 **62.63% 이상**, 시간대 폭주 **62.94%**, 최대 task 오탐 **2.8531%**. 개별 지연 Recall은 46.46%라 높은 Precision만 보고 모두 잘 찾는다고 해석하면 안 된다.\n\n19개 피처, 8개 군집. 각 정상 중심까지 거리/정상 P95 반경의 최솟값을 점수로 쓴다. 정상 공분산 whitening과 관측 상태별 CDF 보정을 적용한다. Adapter 전문가 모델은 아니다.'),
        code("display(Image(filename=str(LOG/'figures/kmeans_performance.png')))\ndisplay(Image(filename=str(LOG/'figures/kmeans_threshold.png')))"),
        md('## 6. LOF — 폭주 취약성을 회복하되 중단 탐지는 하락\n\n같은 정상 후보로 이미 학습한 표현 후보를 새 5-task 기준으로 재선정했다. **asinh → linear+whitening, 이웃 800 → 200, 참조점 최대 4096 → 2048**, 정상 점수 CDF 보정과 정상 calibration P99.5를 채택했다. 현재 LOF는 28개 피처를 사용한다.\n\n직전 대비 개별 지연 **40.39 → 70.45%**, 시간대 폭주 **3.16 → 50.37%**. 반면 입력 중단 **83.41 → 53.43%**, 집단 지연도 소폭 하락했다. 점진적 악화는 **73.20%**. LOF가 모든 task에서 더 좋아졌다는 결론은 아니다.\n\nP99.25의 첫 후보는 집단 지연 오탐 5.58%로 탈락했다. 정상 자료에서 계산한 P99.5/P99.75도 비교했으며 탈락 기록은 `adoption_candidates.json`에 남겼다. **반복 사용한 연구 벤치마크의 정답을 채택 판단에 활용했으므로 블라인드 테스트가 아니다.**'),
        code("display(Image(filename=str(LOG/'figures/lof_performance.png')))\ndisplay(Image(filename=str(LOG/'figures/lof_threshold.png')))\ndisplay(pd.DataFrame(read(ROOT/'reports/shared_model_v5/adoption_candidates.json')['candidates']).drop(columns=['tasks']))"),
        md('## 7. 피처·학습·경보선은 무엇인가\n\n- 실행 종류는 **adapterType + processId**. 별도의 업무 의미 컬럼은 아니다. 처리 건수는 **totalCount**이며 분당 요청 수와 다르다.\n- 소요시간은 **endTime−startTime**. 같은 실행 종류·처리 건수의 과거 정상 후보 범위 대비 이탈을 `current_source_upper/lower` 등으로 전달한다. Adapter별 통계 피처는 별도 전문가 모델과 다르다.\n- 최근 요청 수 증가·0건 지속, 완료 관측 대비 지연·추세를 과거 방향으로 계산한다. K-means·LOF의 rank 피처는 초기 완료 관측 최대 6개를 참조한다. 초기 6개가 정상이라는 보장은 없다. IF는 복원한 current 표현을 사용한다.\n- 정상 후보 19,793개 분으로 추정기를 학습했고 별도 정상 17,925개 분으로 점수를 보정했다. LOF는 중복 제거 후 2,048개만 참조점으로 사용한다. S는 처리 성공이지 성능 정상 인증이 아니다.\n- **score > threshold**일 때 경보한다. 점수는 확률이 아니며 모델마다 단위가 다르다. 정상 분위수가 운영 오탐을 보장하지 않는다.'),
        code("for r in h['models']:\n    display(Markdown('### '+r['algorithm']))\n    display(pd.Series({'features':len(r['columns']),'fit rows':r['fit_rows'],'threshold':r['threshold'],'calibration':r['calibration_mode']+' / '+r['calibration_kind'],'estimator':r['actual_estimator_parameters']}).to_frame('실제 설정'))\n    display(pd.Series(r['columns'],name='입력 피처').to_frame())"),
        md('## 8. 검증과 서비스 전달\n\n독립 confusion matrix 재계산, 71개 테스트, 원시 Process 입력 경로·저장/로드 점수 일치·미지 Adapter fallback·미래 로그가 과거 점수를 바꾸지 않는지 확인했다. 실행된 결과는 아래에서 읽는다.\n\n`reports/shared_model_v5/release/handoff/{if,kmeans,lof}_shared.joblib`가 현재 전달용 모델이다. 함께 `code/` 모듈 트리가 필요하다. **worker는 빈 1분 시간창, 이전 이력, 지연 도착을 처리해야 하며 실시간 상태 관리와 운영 배포가 완료된 것은 아니다.** 현재 테스트를 여러 번 확인한 연구 결과이므로 실제 운영 로그로 별도 검증해야 한다.'),
        code("audit=read(LOG/'audit.json');display({k:v for k,v in audit.items()if k!='raw_serving_checks'})\ndisplay(pd.DataFrame([{'model':r['algorithm'],'file':r['file'],'bytes':r['bytes'],'raw Process':r['raw_Process_predict'],'prefix causal':r['prefix_causal']}for r in h['models']]))")]
    nb=nbformat.v4.new_notebook(cells=cells,metadata={'kernelspec':{'display_name':'Python (analysis venv)','language':'python','name':'python3'}})
    km=KernelManager(kernel_name='python3');km.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
    NotebookClient(nb,timeout=600,resources={'metadata':{'path':str(ROOT)}},km=km).execute()
    nbformat.write(nb,ROOT/'code/shared_model_v5/result.ipynb')
    write(OUT/'latest.json',{'current_release':'reports/shared_model_v5/release','notebook':'code/shared_model_v5/result.ipynb','all_targets_met':True,'ADBench_unchanged':True})
    print('EXECUTED',len(cells),'cells; eight figures; five active tasks',flush=True)

if __name__=='__main__':report()
