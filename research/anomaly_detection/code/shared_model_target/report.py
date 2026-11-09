"""Build a readable, executed report from frozen full-benchmark predictions."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from model_research_v13.data import read,write
from shared_model_improvement.run import ALGORITHMS

OUT=ROOT/'reports/shared_model_target_lof'
BEFORE=ROOT/'reports/shared_model_improvement_refined'
SCENARIOS={'individual_delay':'개별 지연','group_delay':'집단 지연','column_relation':'컬럼 관계',
 'time_burst':'시간대 폭주','input_stop':'입력 중단','gradual_degradation':'점진적 악화','mixed_hard':'Hard'}
NAMES={'if':'IF','kmeans':'K-means','lof':'LOF'}
METRICS={'accuracy':'Accuracy','precision':'Precision','recall':'Recall','f1':'F1','normal_false_positive_rate':'정상 오탐률'}
COLORS=('#2563eb','#f59e0b','#16a34a')


def metric_table(rows):
    return pd.DataFrame([{ '모델':NAMES[r['method']],**{v:round(r[k]*100,2)for k,v in METRICS.items()}}for r in rows]).set_index('모델')


def markdown_table(frame):
    return '\n'.join(['| '+str(frame.index.name or '구분')+' | '+' | '.join(frame.columns)+' |',
        '| --- | '+' | '.join(['---']*len(frame.columns))+' |',
        *['| '+str(i)+' | '+' | '.join(map(str,row))+' |'for i,row in frame.iterrows()]])


def build():
    summary,old,selected=read(OUT/'summary.json'),read(BEFORE/'summary.json'),read(OUT/'selection.json')
    fresh=read(OUT/'fresh_confirmation_v2.json')
    plots=OUT/'figures';plots.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'Malgun Gothic','font.size':11,'axes.unicode_minus':False,
        'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(13,4.8))
    for j,(source,label,color)in enumerate(((old,'직전 단일 모델','#94a3b8'),(summary,'이번 개선','#2563eb'))):
        for ax,key in zip(axes,('f1','normal_false_positive_rate')):
            bars=ax.bar(np.arange(3)+(j-.5)*.34,[r[key]*100 for r in source['pooled']],width=.31,color=color,label=label)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=10)
            ax.set(xticks=range(3),xticklabels=['IF','K-means','LOF'],ylabel='%',ylim=(0,70 if key=='f1'else 18))
    axes[0].set_title('F1 · 동일 전체 벤치마크');axes[1].set_title('정상 오탐률 · 낮을수록 좋음')
    axes[1].axhline(10,color='#dc2626',ls='--',label='목표 10% 미만');axes[0].legend();axes[1].legend()
    fig.tight_layout();fig.savefig(plots/'comparison.png',dpi=150);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,4.8))
    for j,a in enumerate(ALGORITHMS):
        row=summary['pooled'][j];bars=ax.bar(np.arange(4)+(j-1)*.25,[row[k]*100 for k in list(METRICS)[:4]],width=.23,label=NAMES[a],color=COLORS[j])
        ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=9)
    ax.set(xticks=range(4),xticklabels=list(METRICS.values())[:4],ylim=(0,105),ylabel='%',title='전수 지표 · 세 알고리즘은 각각 독립된 단일 모델')
    ax.legend();fig.tight_layout();fig.savefig(plots/'metrics.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(14,7.2),sharey=True)
    for j,a in enumerate(ALGORITHMS):
        for ax,key in zip(axes,('f1','normal_false_positive_rate')):
            values=[next(r[key]*100 for r in d['metrics']if r['method']==a)for d in summary['datasets']]
            bars=ax.barh(np.arange(7)+(j-1)*.25,values,height=.23,color=COLORS[j],label=NAMES[a])
            ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=8)
    axes[0].set(yticks=range(7),yticklabels=[SCENARIOS[d['dataset']]for d in summary['datasets']],xlim=(0,105),xlabel='F1 (%)',title='시나리오별 F1')
    axes[1].set(xlim=(0,max(15,max(r['normal_false_positive_rate']*100 for d in summary['datasets']for r in d['metrics'])+3)),xlabel='정상 오탐률 (%)',title='시나리오별 정상 오탐률')
    axes[0].invert_yaxis();axes[0].legend();axes[1].axvline(10,color='#dc2626',ls='--')
    fig.tight_layout();fig.savefig(plots/'scenarios.png',dpi=150);plt.close(fig)

    ys=[];ss=[]
    for d in summary['datasets']:
        p=np.load(OUT/(d['dataset']+'_scores.npz'));ys.append(p['y']);ss.append(p['scores'])
    y,scores=np.concatenate(ys),np.concatenate(ss)
    plot_info=[]
    for j,a in enumerate(ALGORITHMS):
        s=scores[:,j];cutoff=selected['selected'][a]['threshold']
        low=min(0.,float(np.quantile(s,.001)));high=max(cutoff*3,float(np.quantile(s[~y],.995)),low+1)
        bins=np.linspace(low,high,46);fig,ax=plt.subplots(figsize=(10,4.2));outside={}
        for truth,label,color in ((False,'정상','#94a3b8'),(True,'이상','#ef4444')):
            values=s[y==truth];heights,_=np.histogram(values,bins)
            ax.bar((bins[:-1]+bins[1:])/2,heights/max(len(values),1)*100,width=np.diff(bins)*.94,alpha=.6,label=label,color=color)
            outside[label]=float(np.mean((values<low)|(values>high))*100)
        ax.axvline(cutoff,color='black',ls='--',lw=1.8,label=f'threshold = {cutoff:.5f}')
        ax.set(xlabel='보정된 이상 점수 · 확률 아님',ylabel='각 집단 전체 중 비중 (%)',
            title=f'{NAMES[a]} · 전수 분포 | 표시 밖: 정상 {outside["정상"]:.2f}%, 이상 {outside["이상"]:.2f}%')
        ax.legend();fig.tight_layout();fig.savefig(plots/(a+'_threshold.png'),dpi=150);plt.close(fig)
        plot_info.append({'method':a,'display_min':low,'display_max':high,'outside_percent':outside,'full_population_histogram':True})
    write(OUT/'plot_metadata.json',plot_info)
    samples=joblib.load(OUT/'analysis_samples.joblib')
    fig,axes=plt.subplots(1,2,figsize=(13,4.5))
    for ax,col,label in zip(axes,('adapter_count_p95_z','gap_expected_prefix'),('동일 Adapter·처리건수 구간 대비 소요시간 편차','평소 실행 간격 대비 입력 공백')):
        values=samples[col].to_numpy(float);finite=values[np.isfinite(values)];lo,hi=np.quantile(finite,[.01,.99]);hi=max(hi,lo+1)
        bins=np.linspace(lo,hi,35)
        for truth,name,color in ((False,'정상','#94a3b8'),(True,'이상','#ef4444')):
            v=values[samples.actual_anomaly.to_numpy()==truth];v=v[np.isfinite(v)]
            ax.hist(v,bins=bins,weights=np.full(len(v),100/max(len(v),1)),alpha=.6,label=name,color=color)
        ax.set(title=label,xlabel='피처 값',ylabel='각 집단 표본 내 비중 (%)');ax.legend()
    fig.suptitle('설명용 입력 표본 · 꼬리 1~99% 범위만 표시 · 전수 지표와 구분',fontsize=12)
    fig.tight_layout();fig.savefig(plots/'inputs.png',dpi=150);plt.close(fig)

    comparisons=[]
    for before,after in zip(old['pooled'],summary['pooled']):
        comparisons.append({'algorithm':after['method'],'before_f1':before['f1'],'after_f1':after['f1'],
           'f1_delta_percentage_points':(after['f1']-before['f1'])*100,
           'before_normal_fpr':before['normal_false_positive_rate'],'after_normal_fpr':after['normal_false_positive_rate']})
    write(OUT/'comparisons.json',comparisons)
    head='\n'.join(f'- {NAMES[r["algorithm"]]}: F1 {r["before_f1"]*100:.2f}% → {r["after_f1"]*100:.2f}%, 정상 오탐률 {r["before_normal_fpr"]*100:.2f}% → {r["after_normal_fpr"]*100:.2f}%'for r in comparisons)
    feature_explanations={
      'current_arrival_excess':'현재 1분 실행 횟수의 기존 기준 대비 양수 편차',
      'current_prefix_burst':'현재 실행 횟수 / 먼저 관측한 최대 8분 평균 실행 횟수의 양수 log 비율',
      'gap_expected_prefix':'초기 관측 실행 주기로 보정한 연속 입력 공백',
      'gap_expected_recent':'최근 과거 실행 주기로 보정한 연속 입력 공백',
      'empty_expected_rate':'평소 실행이 예상되지만 현재 실행이 없는 정도',
      'current_source_delay':'현재 완료 로그의 조건부 소요시간 잔차 중 양수 부분을 asinh 압축',
      'current_source_upper':'현재 완료 소요시간이 정상 참고 분포의 위쪽 꼬리에서 드문 정도',
      'current_source_lower':'현재 완료 소요시간이 정상 참고 분포의 아래쪽 꼬리에서 드문 정도',
      'current_prefix_delay':'현재 잔차와 먼저 관측한 최대 8개 완료 분의 중앙값 차이 / MAD 하한',
      'current_recent_delay':'현재 잔차와 직전 최대 15분의 잔차 중앙값 차이 / MAD 하한',
      'current_delay_trend':'직전 최대 15분+현재 잔차의 양수 기울기 × 현재 초기 대비 지연 활성도',
      'current_population_delay':'현재 양수 잔차 × sqrt(log1p(현재 완료 횟수))',
      'current_duration_spread':'현재 최대 log 소요시간 - 현재 중앙 log 소요시간',
      'cadence_ready':'과거 실행 주기를 계산할 근거가 있는지',
      'traffic_assessed':'현재 실행량 기준을 계산할 근거가 있는지',
      'latency_assessed':'현재 완료 소요시간을 평가할 근거가 있는지',
      'temporal_assessed':'최근 변화량을 계산할 과거 근거가 있는지',
      'adapter_max_duration_z':'현재 1분 최대 log 소요시간의 Adapter 정상 중앙값 대비 편차 / IQR',
      'adapter_p95_upper_tail':'현재 1분 완료 P95가 같은 Adapter에서 드물게 큰 정도',
      'adapter_count_p95_z':'현재 1분 완료 log P95의 Adapter·totalCount 구간 기준 편차 / IQR',
      'adapter_count_p95_upper_tail':'같은 Adapter·totalCount 구간에서 현재 1분 P95가 드물게 큰 정도',
      'adapter_count_p95_lower_tail':'같은 Adapter·totalCount 구간에서 현재 1분 P95가 드물게 작은 정도',
      'adapter_stream_arrivals_z':'현재 Process 스트림 실행 횟수의 Adapter·요일유형·시간대 참고 분포 대비 편차',
      'adapter_reference_log_duration':'해당 Adapter·totalCount 구간의 정상 1분 log P95 중앙값',
      'adapter_delay_short_long':'Adapter 보정 지연의 5분-15분 평균 차이 × 현재 지연 활성도',
      'adapter_delay_ewma':'Adapter 보정 양수 지연의 EWMA(alpha=.2) × 현재 지연 활성도'}
    write(OUT/'feature_descriptions.json',feature_explanations)
    md,code=nbformat.v4.new_markdown_cell,nbformat.v4.new_code_cell
    nb=nbformat.v4.new_notebook(cells=[
      md('# Adapter 전문가를 없앤 단일 모델 · 최종 평가\n\nIF / MiniBatch K-means / LOF 각각 탐지기 **하나**. 각 입력은 **현재·과거 피처 17개 + Adapter 통계 피처 9개 = 26개**. 알고리즘 간 앙상블도 없다.'),
      md('## 1. 핵심 결과\n\n'+head+'\n\n아래 결과는 컬럼 관계·Hard를 포함한 기존 전체 2,167,766분 판정이다. 1행의 Process 원본 판정과는 구분한다.'),
      code("from pathlib import Path\nimport json, pandas as pd\nfrom IPython.display import display, Image, Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'reports/shared_model_target_lof/summary.json').exists())\nOUT=ROOT/'reports/shared_model_target_lof'\nread=lambda p: json.loads(p.read_text(encoding='utf-8'))\nresult=read(OUT/'summary.json'); selected=read(OUT/'selection.json')\ndef metrics(rows):\n    cols=['accuracy','precision','recall','f1','normal_false_positive_rate']\n    return (pd.DataFrame(rows).set_index('method')[cols]*100).round(2)\ndisplay(metrics(result['pooled']))\ndisplay(Image(filename=str(OUT/'figures/comparison.png')))\ndisplay(Image(filename=str(OUT/'figures/metrics.png')))"),
      md('**해석:** F1은 Precision과 Recall의 균형이다. 오탐률은 `FP / (TN + FP)`이며 정상 구간을 경보한 비율이다. 항상 정상이라고 해도 Accuracy는 82.39%이므로 정확도 하나만 보지 않는다. 세 알고리즘이 모든 시나리오에서 동시에 개선됐다는 뜻도 아니다.'),
      md('## 2. 무엇을 왜 바꿨나\n\n- 과거 5~15분 지연이 복구 후에도 남아 정상 구간을 오탐하는 문제 확인. 현재 완료 **1분** 소요시간을 중심으로 피처 재구성.\n- 최근 추세는 과거만 비교하되 현재 지연이 없어지면 추세·누적 지연의 영향도 줄어드는 연속 피처 상호작용 추가. 하드 룰 추가가 아님.\n- Adapter P95 참고 기준도 과거 5분 P95에서 현재 1분 완료 P95로 교체. `totalCount` 구간은 과거 5분 평균을 사용.\n- IF: 정상 공분산 축소 추정 + 공통 고정 회전. K-means: 군집 수와 정상 군집 반경. LOF: 중복 제거·이웃 수·참고 표본 수 비교.\n- 완료 로그가 없는 분과 있는 분의 점수 분포를 정상 참고 통계로 보정. Adapter 전문가 모델을 만든 것이 아님.'),
      md('## 3. 학습·설정 선택·평가를 구분\n\n정상 S 후보로 만든 train **19,793분**에서 모델·스케일러·Adapter 통계 학습. 정상 calibration **17,925분**에서 점수 참고 분포와 경보선 후보 생성. 기존 두 개발 분할 **35,782분 / 71,618분**에서 모두 오탐률 8% 미만인 후보 중 합산 F1로 설정 선택. 이 단계는 정답 라벨을 활용했다.\n\n기존 결과를 보며 구조를 개선했으므로 기존 벤치마크는 외부 블라인드 테스트가 아니다. 원본 전 기간의 모든 행을 직접 학습한 것도 아니다. S는 성능 정상 보장이 아닌 정상 후보이다.'),
      code("rows=[]\nfor a,r in selected['selected'].items():\n    for name,v in r['folds'].items(): rows.append({'method':a+' / 개발 '+name,**v})\ndisplay(metrics(rows))\nfresh=read(OUT/'fresh_confirmation_v2.json')\ndisplay(Markdown('### 설정 동결 후 새 seed로 만든 확인 사례 · 재선택에 사용하지 않음'))\ndisplay(metrics(fresh['pooled']))\ndisplay(Markdown('원본 생성기에서 시나리오당 이상 120사례+정상 대조 120사례. 같은 원본 pool을 공유하며 외부 운영 검증은 아님. seed='+str(fresh['seed'])))"),
      md('## 4. 시나리오별 성능과 모델 차이\n\n각 모델은 시나리오 이름을 맞히는 것이 아니라 이상 점수만 출력한다. 같은 피처를 넣어도 점수 계산 방식 때문에 결과가 다르다. **이번 합성 평가에서 관측된 차이**이지 알고리즘이 특정 장애에 항상 강하다는 보장은 아니다. 집단 지연은 같은 Adapter+Process 스트림의 반복 실행 지연이며 Adapter 전체의 여러 Process 동시 지연 실증은 아니다.'),
      code("display(Image(filename=str(OUT/'figures/scenarios.png')))\nfor dataset in result['datasets']:\n    display(Markdown('### '+dataset['dataset']))\n    display(metrics(dataset['metrics']))"),
      md('## 5. 입력 데이터 분포\n\n왼쪽: 같은 Adapter·처리건수 구간의 정상 소요시간 대비 편차. 오른쪽: 과거 실행 주기에 비해 로그가 오랫동안 비어 있는 정도. 원래 요청이 적은 스트림에서 단순히 로그 0건이라고 이상으로 정하지 않기 위한 문맥이다. **표본 그림**이며 전수 지표는 위 표를 사용한다.'),
      code("display(Image(filename=str(OUT/'figures/inputs.png')))\ndesc=read(OUT/'feature_descriptions.json')\ndisplay(pd.DataFrame(desc.items(),columns=['실제 피처명','의미']))"),
      md('## 6. 전수 점수 분포와 실제 threshold\n\n일반 막대 히스토그램이다. 정상과 이상 집단 각각 전체를 분모로 삼는다. **점선보다 큰 점수**가 경보이다. 표시 범위를 벗어난 꼬리 비중은 제목에 표시하며, 성능 계산에서는 한 건도 제외하지 않았다. IF·K-means는 정상 경험적 꼬리 보정, LOF는 정상 중앙값/P95 폭 보정이라 점수 크기를 서로 직접 비교하면 안 된다. 점수는 확률이 아니고 q=.99가 운영 오탐률 1%를 보장하지 않는다.'),
      code("for a in ['if','kmeans','lof']:\n    display(Image(filename=str(OUT/'figures'/f'{a}_threshold.png')))"),
      md('## 7. 정확한 하이퍼파라미터\n\n공통 seed=42, hybrid 압축·안정화, Adapter 피처 양수 가중치 .25. 아래 표에서 해당 알고리즘에 사용한 값만 읽는다. 모델 하나에 군집/이웃/트리를 모두 쓰는 것이 아니다.'),
      code("manifest=read(OUT/'handoff/manifest.json')\nfor r in manifest['models']:\n    a=r['algorithm']; s=r['settings']\n    keys={'if':['trees','samples','whitening','rotation'],'kmeans':['clusters','geometry'],'lof':['neighbors','reference_cap','metric']}[a]\n    values={k:s[k] for k in ['transform','adapter_weight',*keys]}\n    values.update({'입력 피처 수':len(r['columns']),'Adapter 피처 수':r['adapter_feature_count'],'학습/참고 행 수':r['fit_rows'],'정상 점수 quantile':selected['selected'][a]['q'],'score threshold':r['threshold'],'점수 보정':r['calibration_mode']+' / '+r['calibration_kind'],'탐지기 수':r['estimator_count']})\n    display(Markdown('### '+a)); display(pd.Series(values,name='설정').to_frame())\ndisplay(Markdown('K-means 고정값: batch_size=2048, n_init=10, max_iter=150. 중심까지의 거리를 군집별 정상 P95 반경으로 나눈 뒤 최솟값 사용. LOF는 novelty=True, 정상 중복 제거 후 최대 4096개 참조.'))"),
      md('## 8. 실시간 연결 시 의미\n\n완료된 Process 로그와 현재까지의 과거 상태로 연속 1분 grid를 만든다. `Adapter+Process ID`별 상태를 유지하며 로그가 없는 분도 생성해야 입력 중단을 탐지할 수 있다. 현재 완료된 소요시간만 사용하므로 미래 t+k 완료 로그는 읽지 않는다.\n\n초기 기준은 먼저 **관측한** 최대 8분/8개 완료 분이지 정상이라고 보장된 초반이 아니다. 새 Adapter는 전체 참고 통계로 대체한다. 모델 저장/재로딩·미등록 Adapter 추론은 감사 스크립트로 점검하지만 운영 워커의 상태 보관·지연 로그 처리·배포는 별도 작업이다.'),
      md('## 9. ADBench 활용 판단\n\n[ADBench 공식 저장소](https://github.com/Minqi824/ADBench), [NeurIPS 2022 논문](https://arxiv.org/abs/2206.09426).\n\n- **추천:** 현재 Process 피처 표의 알고리즘 비교, global/local/cluster/dependency 유형별 스트레스 테스트, 무관 피처·라벨 오염·중복 이상에 대한 강건성 비교.\n- **주의:** 기본 생성기는 GMM/분포 변형 기반의 표형 데이터 생성이다. 시간 순서·입력 중단·점진적 악화·SQL/하드 룰 정합성을 자동으로 보장하지 않는다. 원시 로그에 그대로 넣는 도구는 아님.\n- 따라서 현재 **시간 순서 기반 로그 시나리오는 유지**, ADBench는 별도 보조 평가 축으로 사용하는 편이 적합하다. 분할은 random split을 그대로 쓰지 말고 시간/사례/원본 템플릿 기준으로 누수를 통제해야 한다.\n- 이번 결과에 ADBench를 설치·실행하거나 기존 벤치마크를 교체하지는 않았다. 제안 단계이다.'),
      md('## 10. 참고 근거와 한계\n\n- [EIF 논문](https://arxiv.org/abs/1811.02141)의 축 정렬 편향 문제를 참고. **이번 IF는 고정 공통 회전 1개 + 기존 IF**이며 EIF 전체 구현이 아니다.\n- [OAS 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.OAS.html): 정상 공분산 축소 추정으로 전처리 안정화. 추가 탐지 모델 아님.\n- [LOF 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html): novelty 모드와 이웃 크기/거리 설정. 이웃 500은 더 넓은 주변을 비교하므로 미세한 국소 이상 민감도와 비용의 절충이 있다.\n- 기존 데이터·정답·기존 모델을 유지. 정상/이상 전체 분 판정, point adjustment 없음. 최종 평가 결과를 보고 threshold를 골라 끼우지 않음.\n- 낮은 오탐률은 이번 합성 평가 관측값이다. 합성 시간대 prior와 공유 원본 pool 때문에 실제 운영 일반화·정식 오탐률 보장은 불가.\n- 이전 confirmation의 위치 기반 라벨 연결 오류는 별도 새 확인 입력에서 case_id+window_start 키 정렬로 수정. 기존 전수 벤치마크는 원래부터 키 기반 정렬이며 이 오류의 영향을 받지 않음.\n- GitHub·Notion·운영 배포 변경 없음.')])
    nb.metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python'}}
    path=ROOT/'code/shared_model_target/result.ipynb'
    km=KernelManager(kernel_name='python3');km.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
    NotebookClient(nb,km=km,timeout=180,resources={'metadata':{'path':str(path.parent)}}).execute(cleanup_kc=True)
    nbformat.write(nb,path)
    text='# 단일 모델 + Adapter 피처 · 최종 결과\n\n'+head+'\n\n'+markdown_table(metric_table(summary['pooled']))
    text+='\n\n전체 기존 벤치마크 2,167,766분. Adapter 전문가 없음, 모델별 26개 피처 중 Adapter 통계 9개. 기존 데이터/정답 변경 없음.\n\n'
    text+='![전후 비교](figures/comparison.png)\n\n![시나리오 비교](figures/scenarios.png)\n\n'
    text+='설정 선택은 개발 라벨을 사용했으며 기존 평가 결과가 구조 설계에 영향을 준 반복 연구다. 외부 블라인드 평가가 아니다. 새 seed 확인 사례에서도 별도 평가했으며 재선택에 사용하지 않았다.\n\n'
    text+='기존 5~15분 지연의 복구 후 잔존 영향을 줄이고 현재 완료 1분 근거를 중심으로 재구성했다. IF는 정상 공분산 안정화/공통 회전, K-means는 16개 군집과 반경 정규화, LOF는 이웃 500/정상 참조 4096으로 선택했다. 순수 Adapter 피처 추가 효과만의 비교는 아니다.\n\n'
    text+='전체 해석·분포·정확한 파라미터·ADBench 검토: `code/shared_model_target/result.ipynb`. 저장 모델: `handoff/`. 독립 재계산 검증: `audit.json`.\n'
    (OUT/'RESULTS.md').write_text(text,encoding='utf-8')
    print('Report, figures and executed notebook saved',flush=True)


if __name__=='__main__':build()
