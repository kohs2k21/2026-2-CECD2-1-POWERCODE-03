"""Executed report, ordinary bars and honest adoption/rejection reasons."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from model_research_v13.data import read

def report(out):
    out=Path(out).resolve();before=read(ROOT/'reports/shared_model_v2_release/summary.json');after=read(out/'summary.json')
    adopt=read(out/'adoption.json');figs=out/'figures';figs.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,3,figsize=(15,4.6))
    for ax,key,title in zip(axes,('f1','recall','normal_false_positive_rate'),('F1','Recall','정상 오탐률')):
        for j,(data,label,color)in enumerate(((before,'이전','#94a3b8'),(after,'채택 후','#2563eb'))):
            bars=ax.bar(np.arange(3)+(j-.5)*.34,[r[key]*100 for r in data['pooled']],width=.31,label=label,color=color)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
        ax.set(xticks=range(3),xticklabels=['IF','K-means','LOF'],ylabel='%',title=title,ylim=(0,80 if key!='normal_false_positive_rate'else 5))
        ax.legend()
    fig.tight_layout();fig.savefig(figs/'comparison.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(14,6))
    labels=[d['dataset']for d in after['datasets']]
    for j,a in enumerate(('if','kmeans','lof')):
        vals=np.array([d['metrics'][j]['f1']for d in after['datasets']])*100
        delta=vals-np.array([d['metrics'][j]['f1']for d in before['datasets']])*100
        for ax,v in zip(axes,(vals,delta)):
            bars=ax.barh(np.arange(7)+(j-1)*.25,v,height=.23,label=a)
            ax.bar_label(bars,fmt='%.1f',padding=2,fontsize=8)
            ax.set(yticks=range(7),yticklabels=labels)
    axes[0].set(title='시나리오별 채택 F1',xlabel='%');axes[1].set(title='이전 대비 F1 변화',xlabel='%p');axes[1].axvline(0,color='black',lw=.8)
    axes[0].legend();fig.tight_layout();fig.savefig(figs/'scenarios.png',dpi=150);plt.close(fig)
    ys=[];scores=[]
    for d in after['datasets']:
        z=np.load(out/(d['dataset']+'_scores.npz'));ys.append(z['y']);scores.append(z['scores'])
    y=np.concatenate(ys);s=np.concatenate(scores);sel=read(out/'selection.json')
    for j,a in enumerate(('if','kmeans','lof')):
        threshold=sel['selected'][a]['threshold'];lo=min(0.,float(np.quantile(s[:,j],.001)));hi=max(threshold*3,float(np.quantile(s[~y,j],.995)),lo+1)
        bins=np.linspace(lo,hi,45);fig,ax=plt.subplots(figsize=(10,4));outside=[]
        for truth,label,color in((False,'정상','#94a3b8'),(True,'이상','#ef4444')):
            v=s[y==truth,j];n,_=np.histogram(v,bins)
            ax.bar((bins[:-1]+bins[1:])/2,n/len(v)*100,width=np.diff(bins)*.95,alpha=.6,label=label,color=color)
            outside.append(f'{label} {np.mean((v<lo)|(v>hi))*100:.2f}%')
        ax.axvline(threshold,color='black',ls='--',label=f'경보선 {threshold:.5f}')
        ax.set(xlabel='보정된 이상 점수 · 확률 아님',ylabel='각 집단 전체 중 비중 (%)',title=a+' · 표시 밖: '+', '.join(outside));ax.legend()
        fig.tight_layout();fig.savefig(figs/(a+'_threshold.png'),dpi=150);plt.close(fig)
    rel=str(out.relative_to(ROOT)).replace('\\','/');md=nbformat.v4.new_markdown_cell;code=nbformat.v4.new_code_cell
    cells=[md('# 오탐률 유지 조건의 F1·Recall 개선 실험\n\n목표는 직전 release 대비 **두 지표 모두 +10~20%p**. 아래에서 목표 달성 여부와 실제 채택 여부를 구분한다. 기존 데이터·정답·기존 모델을 수정하지 않았다.'),
        code("from pathlib import Path\nimport json,pandas as pd\nfrom IPython.display import display,Image,Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents]if(p/'reports/shared_model_v2_release/summary.json').exists())\nOUT=ROOT/'"+rel+"'\nread=lambda p:json.loads(p.read_text(encoding='utf-8'))\nresult=read(OUT/'summary.json'); adoption=read(OUT/'adoption.json')\ndef metrics(rows):\n    return(pd.DataFrame(rows).set_index('method')[['accuracy','precision','recall','f1','normal_false_positive_rate']]*100).round(2)"),
        md('## 1. 전체 전후 비교\n\n동일 **2,167,766개 1분 판정**, 정상 1,785,918 / 이상 381,848. 오탐률=FP/(TN+FP). 이상 사례 속 정상 배경과 정상 대조군을 모두 포함한다. point adjustment 없음. 목표 미달이면 미달로 표시한다.'),
        code("display(metrics(result['pooled']))\ndisplay(pd.DataFrame(result['changes']))\ndisplay(Image(filename=str(OUT/'figures/comparison.png')))"),
        md('## 2. 무엇을 실제로 비교했나\n\n1. **시간 표현**: 현재 소요시간의 초기 관측 대비 변화, 누적 지연·EWMA, 관측 간격에 맞춘 짧은 기억, 실행 횟수의 과산포를 고려한 변화.\n2. **작은 지연**: 거의 일정한 스트림에서 기존 log 소요시간 최소 변동폭 0.15보다 작은 0.03/잔차 0.05 바닥을 비교. 작게 잡으면 민감해지지만 자연 변동도 이상처럼 보일 수 있다.\n3. **정상 분포 표현**: 정상 train의 고정 CDF·꼬리 요약과 피처 구성/가중치 비교. 문자열 ID는 숫자로 넣지 않았다.\n4. **알고리즘 기하**: IF 표본 수·피처 부분선택, K-means 군집 반경 보정의 강도, LOF의 Manhattan/Chebyshev/Cosine 거리와 이웃 수.\n5. **경보 점수**: 정상 점수의 Adapter·관측 상태 보정, 과거 점수 누적/기억. 정답 이벤트의 시작·끝은 사용하지 않는다.\n\n탐지기 학습은 정상 후보만 사용했지만 **하이퍼파라미터 선택에는 개발 라벨을 사용**했다. 피처/경보선 변경을 모두 순수 Adapter 피처 효과라고 해석하면 안 된다. 정상 S 역시 성능 정상의 보증은 아니다.'),
        code("manifest=read(OUT/'handoff/manifest.json'); selection=read(OUT/'selection.json')\nfor r in manifest['models']:\n    a=r['algorithm']; c=selection['selected'][a]\n    display(Markdown('### '+a))\n    display(pd.Series({'previous_retained':c['retained_previous'],'source':c['source'],'feature_count':len(r['columns']),'fit_rows':r['fit_rows'],'threshold':r['threshold'],'calibration_mode':r['calibration_mode'],'calibration_kind':r['calibration_kind'],'geometry_refit':r.get('geometry_refit'),'actual_estimator_parameters':r.get('actual_estimator_parameters',r['settings'])},name='실제 채택 설정').to_frame())"),
        md('## 3. 채택·탈락 기준\n\n전체 평가에서 **FP 증가 없음 + F1·Recall 모두 상승**인 후보만 채택. 하나라도 만족하지 못하면 이전 모델 유지. **각 후보는 전수 평가 전에 경보선을 동결**했다. 앞선 K-means 후보의 오탐 초과를 본 뒤, 다음 후보 하나는 별도 정상 calibration 점수의 **P99.5**라는 보수적인 정책으로 고정했다. 전수 평가 FPR을 맞추도록 cutoff를 역산하지는 않았다.\n\n앞선 결과를 보며 피처/정책을 다시 설계하고 평가한 반복 연구이다. 외부 블라인드 성능이 아니며, 기존 평가를 이용한 채택 자체에도 선택 편향이 있다.'),
        code("display(pd.DataFrame(adoption['attempts'])[['method','source','eligible','F1_gain_pp','recall_gain_pp','FPR_gain_pp']])\ndisplay(pd.Series(adoption['chosen'],name='채택 출처').to_frame())"),
        md('## 4. 시나리오별 결과\n\n전체 개선이 모든 시나리오의 개선을 뜻하지 않는다. 개별·집단 지연, 컬럼 관계, 폭주, 중단, 점진적 악화, Hard를 모두 유지했다.'),
        code("display(Image(filename=str(OUT/'figures/scenarios.png')))\nfor d in result['datasets']:\n    display(Markdown('### '+d['dataset'])); display(metrics(d['metrics']))"),
        md('## 5. 점수 분포와 경보선\n\n일반 막대 히스토그램. 점선보다 높은 점수가 경보이며 표시 밖 꼬리도 실제 평가에는 전부 포함했다. 모델 간 점수 크기는 직접 비교하지 않는다.'),
        code("for a in ['if','kmeans','lof']:display(Image(filename=str(OUT/'figures'/f'{a}_threshold.png')))"),
        md('## 6. 관측 한계와 실험에서 제외한 것\n\n개발 쌍을 비교했을 때 컬럼 관계 이상의 50%, 점진적 악화의 약 10%는 이 실험의 숫자 피처가 정상 대조군과 같았다. 해당 분에 새 완료가 없거나 아직 변화가 관측되지 않은 경우가 있다. **원시 로그의 다른 정보까지 없다는 이론적 불가능 증명은 아니다.**\n\n과거 개발 정상 대조군의 시작/완료 행 수 일부가 별도로 재집계되지 않은 것을 발견해, 이 두 필드를 쓰는 **flow=True 후보는 최종 선택에서 제외**했다. 기존 release 모델은 해당 필드를 피처로 쓰지 않는다. 오류를 모델 성능 향상처럼 이용하지 않았다.\n\nADBench 기존 1,000개는 독립 숫자 피처 표라서 새 시간 피처를 계산할 실제 이력이 없다. 없는 시간 정보를 만들어 재평가하지 않았다. 이번 결과는 원래 Process 시나리오 벤치마크 기준이다.'),
        code("display(pd.DataFrame(read(ROOT/'reports/shared_model_v3/observability.json')['records']))\nif(OUT/'fresh_comparison.json').exists():\n    fresh=read(OUT/'fresh_comparison.json')\n    display(Markdown('새 seed '+str(fresh['seed'])+' / 모델 동결 후 동일 사례로 전후 비교. 원형 생성기이며 mild-v2가 아니다. 같은 원본 pool이므로 외부 블라인드 평가가 아니고 재선택에 사용하지 않았다.'))\n    for version in ['baseline','adopted']:\n        display(Markdown('### 새 seed 확인 / '+version));display(metrics([r for r in fresh['rows']if r['version']==version]))\naudit=read(OUT/'audit.json')\ndisplay({k:audit[k]for k in ['passed','old_models_unchanged','minute_metrics_recomputed','truth_case_alignment_unchanged','no_increase_FP_each_model','F1_and_recall_no_regression_each_model','target_plus_10pp_met','unit_tests_passed']})"),
        md('**새 seed 해석:** IF는 F1·Recall 개선과 오탐 감소가 함께 재현됐다. K-means는 오탐은 줄었지만 F1 **60.86→60.29%**, Recall **49.08→47.37%**로 하락했다. 따라서 K-means의 일반화 개선은 확인되지 않았다. 전수 개선 후보와 새 사례 검증 결과를 구분해야 한다. 모델을 이 확인 결과로 다시 선택하지 않았고, 연구 후보 전달이지 운영 배포 승인이 아니다.'),
        md('## 7. 참고와 백엔드 적용\n\n[CUSUM · NIST](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm)은 작은 변화의 누적 관측이라는 설계 근거이다. 여기서는 감쇠·회복 해제된 파생 피처/점수 후보로 변형했으며 논문의 정확한 알고리즘 재현이나 성능 보장은 아니다. [LOF 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html)의 거리·이웃·novelty 설정과 [IF 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)의 표본·피처 부분선택을 비교했다.\n\n`handoff/` 모델은 동일한 Process-derived 연속 1분 grid와 완료 행 인터페이스를 사용한다. Adapter+Process별 과거 상태, 빈 분 생성, 지연 도착 처리는 backend 워커 책임이다. 운영 배포·GitHub·Notion 변경은 하지 않았다.')]
    nb=nbformat.v4.new_notebook(cells=cells,metadata={'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'},'language_info':{'name':'python'}})
    path=ROOT/'code/shared_model_v3/result.ipynb';km=KernelManager(kernel_name='python3');km.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
    NotebookClient(nb,km=km,timeout=180,resources={'metadata':{'path':str(path.parent)}}).execute(cleanup_kc=True);nbformat.write(nb,path)
    lines=['# F1·Recall 개선 실험','',*[f'- {r["method"]}: F1 {r["f1"]*100:.2f}%, Recall {r["recall"]*100:.2f}%, 정상 오탐률 {r["normal_false_positive_rate"]*100:.2f}%'for r in after['pooled']],
        '',*[f'- {r["method"]}: F1 {r["F1_gain_pp"]:+.2f}%p / Recall {r["recall_gain_pp"]:+.2f}%p / 오탐률 {r["FPR_gain_pp"]:+.2f}%p / +10%p 목표 {r["target_plus_10pp_met"]}'for r in after['changes']],
        '', '![전후 비교](figures/comparison.png)','', '기존 데이터·정답 변경 없음. 오탐률 증가 또는 F1/Recall 회귀 후보는 미채택. 개발 설정 선택 및 연구 벤치마크 채택 편향 있음. 외부 블라인드 결과가 아님.']
    if(out/'fresh_comparison.json').exists():
        lines.extend(['','## 새 seed 확인','',
            'IF는 개선이 재현됐지만 K-means는 F1 60.86→60.29%, Recall 49.08→47.37%로 하락. 오탐은 감소. K-means 일반화 개선은 미확인. 모델 재선택에는 사용하지 않았으며 운영 배포 승인이 아닌 연구 후보 묶음.'])
    (out/'RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Executed report saved',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);report(p.parse_args().output)
