"""Executed, plain-bar report: unchanged log benchmark and ADBench 1000."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
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

OLD=ROOT/'reports/shared_model_target_lof'
NAMES={'if':'IF','kmeans':'K-means','lof':'LOF'}
COLORS=('#2563eb','#f59e0b','#16a34a')
SCENARIOS={'individual_delay':'개별 지연','group_delay':'집단 지연','column_relation':'컬럼 관계',
 'time_burst':'시간대 폭주','input_stop':'입력 중단','gradual_degradation':'점진적 악화','mixed_hard':'Hard'}


def build(output):
    out=Path(output).resolve();result=read(out/'summary.json');old=read(OLD/'summary.json');selected=read(out/'selection.json')
    ad=read(out/'adbench_results.json');manifest=read(out/'handoff/manifest.json')
    plots=out/'figures';plots.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'Malgun Gothic','font.size':11,'axes.unicode_minus':False,
       'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(13,4.8))
    for j,(source,label,color)in enumerate(((old,'직전 모델','#94a3b8'),(result,'최종 선정','#2563eb'))):
        for ax,key in zip(axes,('f1','normal_false_positive_rate')):
            bars=ax.bar(np.arange(3)+(j-.5)*.34,[r[key]*100 for r in source['pooled']],width=.31,label=label,color=color)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=10)
            ax.set(xticks=range(3),xticklabels=['IF','K-means','LOF'],ylabel='%',ylim=(0,70 if key=='f1'else 4.5));ax.legend()
    axes[0].set_title('F1 · 기존 전체 벤치마크');axes[1].set_title('정상 오탐률 · 낮을수록 좋음')
    fig.tight_layout();fig.savefig(plots/'comparison.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(14,7.1),sharey=True)
    for j,a in enumerate(ALGORITHMS):
        current=[d['metrics'][j]['f1']*100 for d in result['datasets']]
        before=[d['metrics'][j]['f1']*100 for d in old['datasets']]
        for ax,values in zip(axes,(current,np.asarray(current)-before)):
            bars=ax.barh(np.arange(7)+(j-1)*.25,values,height=.23,color=COLORS[j],label=NAMES[a])
            ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=8)
    axes[0].set(yticks=range(7),yticklabels=[SCENARIOS[d['dataset']]for d in result['datasets']],xlim=(0,105),title='시나리오별 최종 F1',xlabel='F1 (%)')
    axes[1].set(title='직전 대비 F1 변화 · 음수는 성능 저하',xlabel='변화 (%p)');axes[1].axvline(0,color='#475569',lw=1)
    axes[0].invert_yaxis();axes[0].legend();fig.tight_layout();fig.savefig(plots/'scenarios.png',dpi=150);plt.close(fig)
    ys=[];ss=[]
    for d in result['datasets']:
        p=np.load(out/(d['dataset']+'_scores.npz'));ys.append(p['y']);ss.append(p['scores'])
    y,scores=np.concatenate(ys),np.concatenate(ss)
    for j,a in enumerate(ALGORITHMS):
        s=scores[:,j];threshold=selected['selected'][a]['threshold']
        lo=min(0.,float(np.quantile(s,.001)));hi=max(threshold*3,float(np.quantile(s[~y],.995)),lo+1)
        bins=np.linspace(lo,hi,45);fig,ax=plt.subplots(figsize=(10,4.2));omitted={}
        for truth,label,color in((False,'정상','#94a3b8'),(True,'이상','#ef4444')):
            v=s[y==truth];heights,_=np.histogram(v,bins)
            ax.bar((bins[:-1]+bins[1:])/2,heights/len(v)*100,width=np.diff(bins)*.95,alpha=.6,label=label,color=color)
            omitted[label]=np.mean((v<lo)|(v>hi))*100
        ax.axvline(threshold,color='black',ls='--',label=f'threshold={threshold:.5f}')
        ax.set(title=f'{NAMES[a]} · 전수 점수 | 표시 밖 정상 {omitted["정상"]:.2f}%, 이상 {omitted["이상"]:.2f}%',
               xlabel='최종 보정 점수 · 확률 아님',ylabel='각 집단 전체 내 비중 (%)');ax.legend()
        fig.tight_layout();fig.savefig(plots/(a+'_threshold.png'),dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,4.8))
    for j,(version,label,color)in enumerate((('previous','직전 모델','#94a3b8'),('improved','최종 선정','#2563eb'))):
        rows=[r for r in ad['results']if r['version']==version]
        for ax,key in zip(axes,('f1','normal_false_positive_rate')):
            bars=ax.bar(np.arange(3)+(j-.5)*.34,[r[key]*100 for r in rows],width=.31,label=label,color=color)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=10)
            ax.set(xticks=range(3),xticklabels=['IF','K-means','LOF'],ylabel='%');ax.legend()
    axes[0].set(title='ADBench 1,000개 · F1',ylim=(0,70));axes[1].set(title='ADBench · 정상 오탐률',ylim=(0,5))
    fig.tight_layout();fig.savefig(plots/'adbench.png',dpi=150);plt.close(fig)
    fig,ax=plt.subplots(figsize=(11,4.8));modes=('global','local','cluster','dependency')
    for j,a in enumerate(ALGORITHMS):
        r=next(r for r in ad['results']if r['version']=='improved'and r['method']==a)
        bars=ax.bar(np.arange(4)+(j-1)*.25,[r['types'][m]['f1']*100 for m in modes],width=.23,color=COLORS[j],label=NAMES[a]);ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=9)
    ax.set(xticks=range(4),xticklabels=modes,ylim=(0,105),ylabel='F1 (%)',title='ADBench 유형별 · 각 정상 200개 + 이상 50개');ax.legend()
    fig.tight_layout();fig.savefig(plots/'adbench_types.png',dpi=150);plt.close(fig)

    changes=[{'method':a,'F1_before':b['f1'],'F1_after':r['f1'],'F1_delta_pp':(r['f1']-b['f1'])*100,
       'FPR_before':b['normal_false_positive_rate'],'FPR_after':r['normal_false_positive_rate']}for a,b,r in zip(ALGORITHMS,old['pooled'],result['pooled'])]
    write(out/'comparisons.json',changes)
    relative=str(out.relative_to(ROOT)).replace('\\','/')
    md,code=nbformat.v4.new_markdown_cell,nbformat.v4.new_code_cell
    cells=[md('# Adapter 전문가 없는 단일 모델 · 개선 및 ADBench 평가\n\n알고리즘별 탐지기 하나. IF·K-means·LOF 사이의 별도 앙상블도 없다. 기존 원본·평가 CSV·정답은 유지했다. 아래의 기존 로그 시나리오와 ADBench 숫자 피처 평가를 구분해서 읽는다.'),
      code("from pathlib import Path\nimport json,pandas as pd\nfrom IPython.display import display,Image,Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'data/adbench_1000/manifest.json').exists())\nOUT=ROOT/'"+relative+"'\nread=lambda p:json.loads(p.read_text(encoding='utf-8'))\nresult=read(OUT/'summary.json'); selected=read(OUT/'selection.json'); old=read(ROOT/'reports/shared_model_target_lof/summary.json')\ndef metrics(rows):\n    return (pd.DataFrame(rows).set_index('method')[[c for c in ['accuracy','precision','recall','f1','normal_false_positive_rate','average_precision','roc_auc'] if c in pd.DataFrame(rows)]]*100).round(2)"),
      md('## 1. 전체 결과\n\n기존 **6개 시나리오 + Hard 전체 2,167,766개 1분 판정**. 정상 1,785,918개, 이상 381,848개. 정상 오탐률은 FP/(TN+FP). 정상 대조군뿐 아니라 이상 사례 속 정상 배경 분도 포함한다. 항상 정상이라고 해도 Accuracy는 82.39%이므로 F1·Recall을 함께 본다.'),
      code("display(metrics(result['pooled']))\ndisplay(Image(filename=str(OUT/'figures/comparison.png')))\ndisplay(pd.DataFrame(read(OUT/'comparisons.json')))"),
      md('## 2. 무엇을 바꿨고 왜 효과가 있었나\n\n- **K-means**: 정상 S 학습 자료에도 드문 큰 변동이 있다. 안정화된 피처 공간에서 큰 거리 상위 1%를 **중심 적합에서만** 제외하는 후보를 비교하고 8개 군집을 선택했다. 실제 로그/평가 정답을 삭제한 것이 아니고, 정상 경보선 산정에는 정상 calibration 전체를 사용했다. 완전한 Trimmed k-means 구현은 아니다.\n- 현재 소요시간, 초기 관측 대비 지연, 증가 추세가 함께 높아지는 정도 등 **6개 관계 피처** 추가. 단순히 값 하나만 큰 경우와 여러 근거가 맞는 경우를 구분할 수 있게 했다.\n- 최근 완료에서 확인한 지연을 최대 3/8분까지 감쇠시키는 **9개 기억·트래픽 피처**도 비교. 새 정상 완료가 오면 바로 갱신되며 미래 완료를 읽지 않는다. 선택된 모델의 실제 입력 수는 아래 설정 표 참조.\n- **LOF**: 이웃 수·참고 표본 수·Adapter 피처 가중치·정상 점수 보정과 인과적인 점수 완화 후보를 비교했다. 채택 여부와 정확한 값은 manifest 표에 표시한다.\n- **IF**: 새 후보가 기존 전체 F1을 넘지 못해 기존 55.01% 모델을 유지했다. 모든 모델이 개선됐다고 주장하지 않는다.\n- PCA, 공분산 안정화, 거리 변경, 중복 빈도 보존/잡음 평활 등도 비교했지만 최종 선택과 구분한다. 하이퍼파라미터와 경보선도 달라졌으므로 순수 Adapter 피처 추가 효과만의 비교는 아니다.'),
      code("manifest=read(OUT/'handoff/manifest.json')\nfor r in manifest['models']:\n    a=r['algorithm']; s=r['settings']\n    relevant={'if':['trees','samples','whitening','rotation'],'kmeans':['clusters','geometry','trim','interactions'],'lof':['neighbors','reference_cap','metric','interactions','deduplicate','projection']}[a]\n    v={k:s[k] for k in ['adapter_weight','memory_weight','memory',*relevant] if k in s}\n    v.update({'total_features':len(r['columns']),'Adapter_features':r['adapter_feature_count'],'fit_or_reference_rows':r['fit_rows'],'normal_quantile':selected['selected'][a]['q'],'threshold':r['threshold'],'score_calibration':r['calibration_mode']+' / '+r['calibration_kind'],'detector_count':r['estimator_count']})\n    display(Markdown('### '+a));display(pd.Series(v,name='설정').to_frame())\n    if 'causal_score_filter' in r: display(pd.Series(r['causal_score_filter'],name='과거 점수 처리').to_frame())\n    display(pd.DataFrame({'actual_model_features':r['columns']}))"),
      md('## 3. 피처를 쉬운 말로 읽기\n\n- **처리건수**는 Process 실행 한 건의 `totalCount`. 분당 요청 수와 다르다.\n- **실행 횟수**는 현재 Adapter+Process 스트림의 시간창별 실행 집계.\n- `adapter_count_p95_z`: 현재 완료 1분 P95 소요시간을 같은 Adapter·최근 5분 평균 totalCount 구간의 정상 중앙값/IQR과 비교. 정확히 동일한 처리건수만 비교하는 것은 아니다.\n- `current_prefix_delay`: 현재 소요시간 잔차가 먼저 관측한 최대 8개 완료 분의 기준보다 얼마나 커졌는지. 초반이 정상이라는 보장은 없다.\n- `current_delay_trend`: 직전 최대 15분과 현재까지 확인한 완료 로그의 양수 증가 기울기. 미래 t+k 로그는 읽지 않는다.\n- `last_*_3m/8m`: 마지막 완료에서 확인한 근거를 짧게 감쇠해 기억. 0초 정상 로그를 꾸며 넣는 것이 아니다.\n- `coherent_*`: 지연·꼬리·추세·실행량 사이의 동시 변화. 새로운 확정 이상 하드 룰이 아니라 숫자 파생 피처다.\n\n모델은 하나여도 Adapter별 정상 참고 통계표는 여러 개이다. 참고가 부족하면 Adapter 단독 → 전체로 확장한다. Process 스트림 집계를 Adapter 정상 분포와 비교한 것이며 Adapter 전체 여러 Process 동시 장애의 실증은 아니다.'),
      md('## 4. 어떤 시나리오가 좋아지고 나빠졌나\n\n왼쪽은 최종 F1, 오른쪽은 직전 대비 변화다. **음수도 그대로 표시**한다. 전체 F1 상승이나 전체 오탐률 유지가 모든 시나리오의 동시 개선을 뜻하지 않는다.'),
      code("display(Image(filename=str(OUT/'figures/scenarios.png')))\nfor d in result['datasets']:\n    display(Markdown('### '+d['dataset']));display(metrics(d['metrics']))"),
      md('## 5. 실제 점수 분포와 경보선\n\n일반 막대 히스토그램. 정상/이상 각각의 전수 분모로 비중을 계산했다. **점선보다 큰 점수**가 경보이다. 표시 밖 꼬리 비중은 제목에 표시하지만 평가에서 제외하지 않았다. 모델별 보정 방식이 달라 점수/threshold 크기를 서로 직접 비교하면 안 된다. 점수는 확률이 아니며 정상 점수 P99가 운영 오탐률 1%를 보장하지 않는다.'),
      code("for a in ['if','kmeans','lof']: display(Image(filename=str(OUT/'figures'/f'{a}_threshold.png')))"),
      md('## 6. 학습·선택·확인을 구분\n\n탐지기와 스케일/Adapter 기준은 정상 train **19,793분**, 점수 기준은 정상 calibration **17,925분**으로 만들었다. K-means 중심 적합 행 수는 1% 제외 후 값이다. 기존 두 개발 분할 **35,782 / 71,618분**에서 파라미터와 경보선을 비교했다. 모델 학습은 정상 후보만의 비지도지만 **설정 선택에는 개발 정답 라벨을 사용했다**.\n\n기존 벤치마크 결과를 보며 구조를 여러 번 개선하고 IF 유지 여부도 판단했다. 따라서 이 벤치마크는 외부 블라인드 테스트가 아니다. 정상/개발/확인 case_id는 분리하되 원본 pool·생성기를 공유하므로 실제 운영 일반화 증거로 과장하지 않는다. 모든 기간의 원본 전 행을 직접 학습한 것도 아니다.'),
      code("fresh=read(OUT/'fresh_confirmation_v2.json')\ndisplay(Markdown('### 최종 모델 동결 후 새 seed 확인 · 재선택에 사용하지 않음'))\ndisplay(metrics(fresh['pooled']))\ndisplay(Markdown('seed='+str(fresh['seed'])+' / 시나리오당 이상 120사례+정상 120대조 / 같은 원본 생성기, 외부 블라인드 평가 아님'))"),
      md('## 7. ADBench 1,000개를 실제로 만들고 테스트\n\n정상 **800개 + 이상 200개**. 네 생성 유형별 정상 200개/이상 50개, 총 250개이다. 피처와 정답 CSV를 분리하고 ID/숫자값이 NPZ와 정확히 일치하는지 검증했다. 정답은 모델 입력이 아니라 평가 지표 계산에만 사용한다.\n\n**adbench 0.1.11 공식 `generate_realistic_synthetic` 메서드 본문을 수정 없이 호출**했다. 불필요한 DL import를 피하기 위해 해당 메서드만 로드했으며 pandas 3/copulas의 읽기 전용 배열은 동일 값의 쓰기 가능 복사로 호환 처리했다.\n\n- global: 넓어진 수치 범위의 독립 표본\n- local: 정상 GMM 공분산 3배\n- cluster: 정상 GMM 평균 3배\n- dependency: 정상 피처 관계를 끊고 각 열의 KDE에서 독립 표본\n\n모델 정상 train의 준비/관측 플래그가 모두 True인 자료 1,600개를 생성기 참고로 사용했다. 상수 열/플래그는 유지했고, 나머지 연속 열은 정상 표준화 후 생성·역변환했다. 완성한 1,000개로 모델이나 threshold를 다시 학습하지 않았다.'),
      code("ad=read(OUT/'adbench_results.json')\nfor version in ['previous','improved']:\n    display(Markdown('### '+version));display(metrics([r for r in ad['results']if r['version']==version]))\ndisplay(Image(filename=str(OUT/'figures/adbench.png')))\ndisplay(Image(filename=str(OUT/'figures/adbench_types.png')))\nfor a in ['if','kmeans','lof']:\n    r=next(r for r in ad['results']if r['version']=='improved'and r['method']==a)\n    display(Markdown('### '+a+' / ADBench 유형별'))\n    display(metrics([{'method':m,**v}for m,v in r['types'].items()]))"),
      md('**ADBench 해석:** 원시 Process 로그가 아니라 독립된 **숫자 피처 공간 평가**다. 시간 순서·입력 중단·점진적 악화를 생성한 것이 아니다. 현재 SQL/STATUS 하드 룰 검증도 적용 대상이 아니다. 생성기의 정상 레이블이 물리적으로 가능한 운영 정상 로그를 보장하지 않는다. 정상/이상 분포도 겹친다.\n\n로그 시나리오에서 F1이 55%를 넘었다고 ADBench 모든 유형에서 높아야 하는 것은 아니다. 같은 경보선을 고정했으므로 새 분포에서 Recall이 낮아질 수 있다. global과 local/dependency 결과를 따로 본다. 1,000개 소규모 결과를 공개 ADBench 전체 순위나 실제 운영 성능으로 해석하지 않는다.'),
      md('## 8. 백엔드 연결 및 재현 범위\n\n`handoff/`에 알고리즘별 학습 모델을 저장했다. 저장·재로딩, Process 원자료→피처→점수, 새 Adapter의 전체 통계 fallback, 미래가 과거 예측에 영향을 주지 않는지 점검한다.\n\n실시간 연결에는 Adapter+Process별 과거 상태와 연속 1분 grid, 정상 역사 집계 및 완료 로그가 필요하다. 로그가 없는 분도 만들어야 입력 중단을 탐지한다. 원시 한 행만 보내면 모든 시간 피처가 자동 계산되는 구조는 아니다. 상태 보관 워커·지연 도착 처리·운영 배포는 별도이며 이번에 GitHub/Notion/운영 모델을 자동 변경하지 않았다.\n\n참고: [ADBench 공식 저장소](https://github.com/Minqi824/ADBench), [NeurIPS 2022 논문](https://arxiv.org/abs/2206.09426), [MiniBatch K-means](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html), [LOF](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html).')]
    nb=nbformat.v4.new_notebook(cells=cells,metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python'}})
    path=ROOT/'code/shared_model_v2/result.ipynb';km=KernelManager(kernel_name='python3');km.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
    NotebookClient(nb,km=km,timeout=180,resources={'metadata':{'path':str(path.parent)}}).execute(cleanup_kc=True)
    nbformat.write(nb,path)
    rows=['# 단일 모델 개선 결과','',*(f'- {NAMES[r["method"]]}: F1 {r["F1_before"]*100:.2f}% → {r["F1_after"]*100:.2f}%; 정상 오탐률 {r["FPR_before"]*100:.2f}% → {r["FPR_after"]*100:.2f}%'for r in changes),
       '', '기존 전체 2,167,766분 평가, 6개+Hard 포함. 데이터·정답 변경 없음. IF는 기존 모델 유지.',
       '', '![전후 비교](figures/comparison.png)','', '![시나리오별](figures/scenarios.png)','',
       '![ADBench](figures/adbench.png)','', '상세 설정·분포·회귀 시나리오·실험 한계는 code/shared_model_v2/result.ipynb 참조.',
       '기존 벤치마크 결과를 보고 개선한 반복 연구이며 외부 블라인드 테스트가 아니다. ADBench는 별도 피처 공간 평가이다.']
    (out/'RESULTS.md').write_text('\n'.join(rows),encoding='utf-8')
    print('Executed notebook and ordinary-bar report saved',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);build(p.parse_args().output)
