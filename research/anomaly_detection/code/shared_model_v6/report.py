"""Executed notebook: whole-benchmark gains, real roles, visible regressions."""
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
from model_research_v13.data import read
from shared_model_v6.protocol import OUT,BASE,TASKS,ALGORITHMS

LOG=OUT/'release';LABELS=['개별 지연','집단 지연','시간대 폭주','입력 중단','점진적 악화'];NAMES=['IF','K-means','LOF'];COLORS=['#2563eb','#d97706','#059669']

def draw():
    dest=LOG/'figures';dest.mkdir(exist_ok=True);new=read(LOG/'summary.json');old=read(BASE/'summary.json');sel=read(LOG/'selection.json')
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,'font.size':11})
    fig,axes=plt.subplots(1,3,figsize=(15,4.5));x=np.arange(3)
    for ax,key in zip(axes,('precision','recall','f1')):
        for i,(data,label,color)in enumerate(((old,'이전 v5','#94a3b8'),(new,'현재 v6','#2563eb'))):
            bars=ax.bar(x+(i-.5)*.34,[r[key]*100 for r in data['pooled']],width=.31,label=label,color=color);ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
        if key=='recall':ax.axhline(60,ls='--',color='#dc2626',label='전체 Recall 목표 60%')
        ax.set(xticks=x,xticklabels=NAMES,ylabel='%',ylim=(0,108),title=key.title());ax.legend(fontsize=8)
    fig.suptitle('동일 5개 task 전체 · 데이터/정답 변경 없음');fig.tight_layout();fig.savefig(dest/'pooled_comparison.png',dpi=160);plt.close(fig)
    for key,title,file in [('f1','task별 F1 · IF 개별 지연 하락도 함께 확인','task_f1.png'),('recall','task별 Recall · 모든 task가 60%인 것은 아님','task_recall.png'),('normal_false_positive_rate','task별 정상 오탐률 · 모두 5% 미만','task_fpr.png')]:
        fig,ax=plt.subplots(figsize=(12,5));x=np.arange(5)
        for i in range(3):
            v=[d['metrics'][i][key]*100 for d in new['datasets']];bars=ax.bar(x+(i-1)*.26,v,width=.24,label=NAMES[i],color=COLORS[i]);ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=8)
        if key=='recall':ax.axhline(60,ls='--',color='#dc2626',label='참고선 60%')
        if key=='normal_false_positive_rate':ax.axhline(5,ls='--',color='#dc2626',label='오탐 상한 5%')
        ax.set(xticks=x,xticklabels=LABELS,ylabel='%',title=title,ylim=(0,6.4 if key=='normal_false_positive_rate'else 108));ax.legend(ncol=4,fontsize=9)
        fig.tight_layout();fig.savefig(dest/file,dpi=160);plt.close(fig)
    bank=[np.load(LOG/(t+'_scores.npz'))for t in TASKS];y=np.concatenate([z['y']for z in bank]);s=np.concatenate([z['scores']for z in bank])
    for i,a in enumerate(ALGORITHMS):
        th=sel['models'][a]['threshold'];v=s[:,i];lo=min(0.,float(np.quantile(v,.001)));hi=max(float(np.quantile(v,.995)),th*1.4,lo+1.);edges=np.linspace(lo,hi,43);centers=(edges[:-1]+edges[1:])/2
        fig,ax=plt.subplots(figsize=(10,4.2));outside=[]
        for j,(truth,label,color)in enumerate(((False,'정상','#94a3b8'),(True,'이상','#dc2626'))):
            values=v[y==truth];counts,_=np.histogram(values,edges);ax.bar(centers+(j-.5)*np.diff(edges)*.44,counts/len(values)*100,width=np.diff(edges)*.42,label=label,color=color)
            outside.append(label+f' {np.mean((values<lo)|(values>hi))*100:.2f}%')
        ax.axvline(th,ls='--',color='black',label=f'경보선 {th:.6f}');ax.set(title=NAMES[i]+' · 표시 밖: '+', '.join(outside),xlabel='보정 이상 점수 (장애 확률 아님)',ylabel='각 집단 전체 중 비중 (%)');ax.legend(fontsize=9)
        fig.tight_layout();fig.savefig(dest/(a+'_threshold.png'),dpi=160);plt.close(fig)

def report():
    draw();md=nbformat.v4.new_markdown_cell;code=nbformat.v4.new_code_cell
    cells=[md('# Recall 60% 이상 + 다른 전체 지표 유지\n\n**192만여 개 기존 판정 전체에서 IF·K-means·LOF의 전체 Recall ≥60%, Accuracy·Precision·F1은 v5 이상, 모든 task와 전체 오탐 <5%를 확인했다.**\n\n목표의 Recall은 **각 모델의 전체 합산 기준**으로 검증했다. **모든 task의 Recall이 60% 이상이라는 뜻이 아니다.** task별 수치와 퇴보는 따로 공개한다. 5~10%p 향상은 IF·LOF 일부 지표에서 달성했고, K-means는 기존 모델을 유지했다.'),
        code("from pathlib import Path\nimport json,numpy as np,pandas as pd\nfrom IPython.display import display,Image,Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents]if(p/'reports/shared_model_v6/release/summary.json').exists())\nLOG=ROOT/'reports/shared_model_v6/release'; BASE=ROOT/'reports/shared_model_v5/release'\nread=lambda p:json.loads(p.read_text(encoding='utf8'))\ns=read(LOG/'summary.json');old=read(BASE/'summary.json');sel=read(LOG/'selection.json');handoff=read(LOG/'handoff/manifest.json')\nalg=['if','kmeans','lof'];keys=['accuracy','precision','recall','f1','normal_false_positive_rate']\ndef metrics(rows):return (pd.DataFrame(rows).set_index('method')[keys]*100).round(4)"),
        md('## 1. 전체 성능과 전후 차이\n\n같은 **1,927,972개 1분 판정**: 정상 1,596,121 / 이상 331,851. 컬럼 관계·Hard 혼합은 계속 제외하며 ADBench도 변경하지 않았다. 정상 오탐률 = FP/(TN+FP). 모든 분을 합친 F1은 task F1의 평균이나 중앙값과 다르다.'),
        code("display(metrics(s['pooled']))\nchanges=[]\nfor b,r in zip(old['pooled'],s['pooled']):\n    row={'model':r['method']}\n    for k in keys:row[k+' 전 %']=b[k]*100;row[k+' 후 %']=r[k]*100;row[k+' 차이 %p']=(r[k]-b[k])*100\n    changes.append(row)\ndisplay(pd.DataFrame(changes).round(2));display(Image(filename=str(LOG/'figures/pooled_comparison.png')))\nassert s['all_targets_met']"),
        md('## 2. task별 F1·Recall·오탐을 함께 보기\n\n**IF의 개별 지연 F1은 64.30% → 52.53%, Recall은 52.17% → 35.79%로 하락했다. 입력 중단 F1도 86.07% → 84.69%로 소폭 하락했다.** 전체 지표 개선을 모든 task 개선이라고 표현하면 안 된다. K-means는 task별 수치도 그대로이고, LOF는 5개 task F1이 모두 v5 이상이다.\n\n범위가 다른 ‘모델 전체 Recall 60%’와 ‘모든 task Recall 60%’를 구분한다. 후자는 미달이다.'),
        code("rows=[{'task':d['dataset'],**r}for d in s['datasets']for r in d['metrics']]\ntable=pd.DataFrame(rows);display(table[['task','method','tp','fp','fn','tn',*keys]].round(6))\nfor file in ['task_f1.png','task_recall.png','task_fpr.png']:display(Image(filename=str(LOG/'figures'/file)))"),
        md('## 3. 모델별 장점은 실제 결과로 확인\n\n- **IF:** 집단 지연 F1 **86.90%**, 입력 중단 **84.69%**로 상대적으로 높다. 개별 지연은 약점이다. 집단 지연의 차이는 약 1.25%p로 크지 않다.\n- **K-means:** 시간대 폭주 F1 **62.94%**로 가장 높고 최대 task 오탐은 **2.8531%**. 폭주 Recall은 50.27%라 해결 완료가 아니다.\n- **LOF:** 개별 지연 F1 **75.65%**로 K-means보다 약 13.01%p 높다. 점진적 악화는 LOF **74.98%**, IF **74.83%**로 사실상 비슷해 LOF만의 독보적 강점으로 부르지 않는다.\n\n아래 간격은 정상/이상 대조를 묶은 **사례 단위 bootstrap 300회**로 계산했다. 모델 선택의 낙관 편향을 제거하거나 실제 운영 성능을 보증하는 구간은 아니다.'),
        code("roles=read(LOG/'roles.json');display(pd.DataFrame(roles['best_by_task']))\ndisplay(pd.DataFrame(roles['pairwise']))"),
        md('## 4. 무엇을 바꿨나 — 신호를 묶고 정상 기준을 맞춤\n\n입력은 기존과 같이 Process 로그에서 만든 1분 피처다. 실행 종류는 **adapterType + processId**, 처리 건수는 **totalCount**, 소요시간은 **endTime−startTime**이다.\n\n새 IF·LOF에서는 이미 계산된 수치 15개를 아래 5개 신호군으로 묶었다. **정상 학습 후보의 중앙값·P95 범위**로 크기를 맞춰 중복된 피처나 크기가 큰 신호가 다른 신호를 가리지 않도록 했다.\n\n| 신호군 | 실제 컬럼 예 | 의미 |\n|---|---|---|\n| 한 번의 지연 | current_source_upper, current_source_delay | 실행 종류·처리 건수의 정상 후보 참조 대비 현재 소요시간 이탈 |\n| 지연 지속 | seq_last_shift, seq_delay_cusum, rank_upper_memory | 최근 지연과 과거 기준 초과가 이어지는 정도 |\n| 지연 추세 | rank_duration_cusum, seq_delay_slope, seq_delay_ewma | 소요시간의 누적 변화·증가 경향 |\n| 요청 폭주 | traffic_tail_3m/5m, current_prefix_burst | 과거 시간대·초기 관측 대비 최근 실행 횟수 증가 |\n| 입력 중단 | gap_expected_prefix, traffic_zero_logprob | 평소 들어오던 실행이 0건인 상태의 지속 |\n\n이는 연속적인 숫자 피처이지 STATUS 규칙이나 새로운 장애 확정 하드 룰이 아니다. 과거 방향으로만 계산하며, 시간대 이력은 현재 합성 정상 이력이라 운영에서는 재검증이 필요하다.'),
        md('## 5. IF — 정상 대비 가장 큰 신호를 요약해 학습\n\n15개 원천 수치를 정상 기준으로 맞춘 뒤 신호군별 최대를 구하고, 그중 최대인 **요약 피처 1개**를 IF에 입력한다. IF가 15차원 조합을 그대로 학습했다고 말하면 틀리다. 요약 이후 400개 트리, 트리당 max_samples=64로 학습했다.\n\n또한 관측에서 가장 큰 신호군별로 **정상 점수 참조 통계**를 달리해 점수를 보정했다(alpha=0.5, CDF, 5개 참조군). 신호군은 task 정답이 아니라 현재 수치의 argmax로 결정한다. 별도 전문가 IF 5개를 학습한 것이 아니다.\n\n이 구조가 지속 지연·중단을 더 찾도록 했지만 개별 지연의 성능은 낮아졌다. 전체 Recall **69.58%**, F1 **77.55%**, 오탐 **2.05%**.'),
        code("r=sel['models']['if'];display(pd.Series(r).to_frame('실제 IF 설정'));display(Image(filename=str(LOG/'figures/if_threshold.png')))"),
        md('## 6. K-means — 폭주 강점을 보존하기 위해 유지\n\n새로운 5/11개 요약 피처, 공분산 변환, 군집 수, 신호군별 점수 보정, 기존 19차원에 추가 피처를 붙이는 방식까지 비교했다. 일부 후보는 전체 F1이 약 0.98%p 좋아졌지만 **시간대 폭주 F1이 8.65%p 하락**해 최종 모델로 채택하지 않았다.\n\n현재는 기존 19개 피처·8개 군집·정상 공분산 whitening을 유지한다. 군집 중심까지 거리/정상 P95 반경의 최솟값을 점수로 사용한다. 전체 Recall **66.38%**, F1 **75.98%**, 오탐 **1.73%**. K-means까지 5~10%p 개선했다고 주장하지 않는다.'),
        code("display(pd.Series(sel['models']['kmeans']).to_frame('실제 K-means 설정'));display(Image(filename=str(LOG/'figures/kmeans_threshold.png')))"),
        md('## 7. LOF — 지연·폭주·중단 신호 11개로 국소 밀도 비교\n\n각 신호군의 최대·평균 10개와 지연 신호 간 동시 상승 1개를 입력한다. 정상 후보에서 가까운 이웃과의 밀도를 비교한다. **11차원**, novelty=True, 이웃 **800개**, 참조점 최대 **4096개**를 사용한다.\n\n관측 가능 상태와 가장 큰 신호군의 조합별 정상 점수 robust 보정(alpha=0.5)을 적용했다. 여러 LOF 전문가나 IF·K-means와의 앙상블이 아니다.\n\n전체 Recall **58.76% → 68.49%**, F1 **69.02% → 75.61%**, Precision **83.63% → 84.39%**. 5개 task F1은 모두 유지·개선됐지만 task별 Precision까지 모두 유지된 것은 아니다. 오탐은 **2.39% → 2.63%**, 최대 task 오탐 **4.7221%**로 상한 이하다.'),
        code("display(pd.Series(sel['models']['lof']).to_frame('실제 LOF 설정'));display(Image(filename=str(LOG/'figures/lof_threshold.png')))"),
        md('## 8. 학습·선택·평가 경계와 한계\n\n- 정상 후보 **19,793개 분**으로 추정기를 fit하고 별도 정상 **17,925개 분**으로 점수·경보선을 보정했다. LOF는 중복 제거 후 최대 4096개 참조점만 사용한다. **S는 성능 정상 보장이 아니다.**\n- 개발용 정답으로 후보를 비교했고, 이미 반복 사용한 연구 벤치마크에서 **task별 200개 전체 사례 + 정상 대조군**을 예비 비교에 썼다. 채택 후보를 고정한 뒤 전체 5000개 사례씩 평가했다. **일부 평가 정답이 후보·경보 정책 선택에 쓰였으므로 외부 블라인드 테스트가 아니다.**\n- 경보선 후보는 별도 정상 점수 분위수에서 계산했지만 최종 채택에는 재사용 연구 평가 결과를 확인했다. 데이터·정답·난이도·point adjustment로 점수를 올리지 않았다.\n- 정상 분위수나 bootstrap 구간은 운영 오탐률 보장이 아니다. 외부 운영 로그와 장애로 재검증해야 한다.\n- [COPOD 논문](https://arxiv.org/abs/2009.09463)의 경험적 꼬리 확률 관점도 검토했다. 피처 ECDF 후보를 실험했으나 현재 IF·LOF의 원천 피처 스케일링은 robust 방식이다. 정상 점수 CDF 보정은 사용하지만 COPOD 자체를 구현·채택한 것은 아니다.\n- [scikit-learn 공식 문서](https://scikit-learn.org/stable/modules/outlier_detection.html)에 따라 LOF는 novelty=True로 설정해 학습 참조점과 별도의 관측을 평가한다.'),
        md('## 9. 검증과 백엔드 전달\n\n독립 지표 재계산·원시 Process 입력·저장/로드 점수 일치·미지 Adapter fallback·과거 판정 인과성 및 **79개 테스트**를 확인했다. 새 피처는 미래 로그로 이전 값을 바꾸지 않는다.\n\n현재 전달용 파일은 **reports/shared_model_v6/release/handoff/{if,kmeans,lof}_shared.joblib**와 manifest.json이다. 이전 v5를 덮어쓰지 않았다. 의존 code 모듈도 필요하다. worker의 빈 1분 생성·이전 상태 보관·지연 도착 처리·실제 배포는 별도 작업이다. 이 작업에서는 Git push나 Notion 페이지 수정은 하지 않았다.'),
        code("audit=read(LOG/'audit.json');display({k:v for k,v in audit.items()if k!='raw_serving_checks'})\ndisplay(pd.DataFrame([{'model':r['algorithm'],'file':r['file'],'bytes':r['bytes'],'threshold':r['threshold'],'raw Process':r['raw_Process_predict'],'prefix causal':r['prefix_causal']}for r in handoff['models']]))\nfor r in handoff['models']:\n    display(Markdown('### '+r['algorithm']+' 실제 원천/변환 피처'))\n    display(pd.Series(r['columns'],name='원천 피처').to_frame())\n    if 'transformed_columns'in r:display(pd.Series(r['transformed_columns'],name='추정기에 들어가는 변환 피처').to_frame())")]
    nb=nbformat.v4.new_notebook(cells=cells,metadata={'kernelspec':{'display_name':'Python (analysis venv)','language':'python','name':'python3'}})
    km=KernelManager(kernel_name='python3');km.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
    NotebookClient(nb,timeout=600,resources={'metadata':{'path':str(ROOT)}},km=km).execute();nbformat.write(nb,ROOT/'code/shared_model_v6/result.ipynb')
    print('Executed notebook',len(cells),'cells; readable normal-bar plots; all task regressions visible',flush=True)

if __name__=='__main__':report()
