"""Render exact metric-table PNGs and fill the user's existing Notion outline.

Documentation only: never trains, changes a benchmark, or deploys a model.
Image upload/targeted page edits are performed through the Notion connector.
"""
import json
from pathlib import Path
import hashlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/"reports/model_research_v18"
OUTPUT = SOURCE/"notion_report_20261014"
NAMES = ("전체 Data", "Adapter 전문가", "앙상블")
ALGORITHMS = ("if", "kmeans", "lof")
TITLES = ("Isolation Forest", "Clustering · MiniBatch K-means", "LOF · Local Outlier Factor")
TASKS = {"individual_delay":"개별 지연", "group_delay":"집단 지연", "column_relation":"컬럼 관계",
         "time_burst":"시간대 폭주", "input_stop":"입력 중단", "gradual_degradation":"점진적 악화", "mixed_hard":"혼합 Hard"}


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def pct(value): return f"{value*100:.2f}%"
def indent(text): return "\n".join("\t"+line if line else "" for line in text.strip().splitlines())
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def draw(summary, algorithm, offset, title):
    fig = plt.figure(figsize=(12,10.5),facecolor="white")
    fig.text(.04,.95,title,fontsize=24,fontweight="bold",color="#16354B")
    fig.text(.04,.914,"최종 후보 v18 · 현재 mild-v2 벤치마크 · 2026-10-14",fontsize=14,color="#536270")
    fig.text(.04,.87,"전체 평가 분 합산 성능 (%)",fontsize=18,fontweight="bold")
    ax = fig.add_axes([.04,.64,.92,.205]); ax.axis("off")
    keys = ("accuracy","precision","recall","f1","normal_false_positive_rate")
    records = summary["pooled"][offset:offset+3]
    values = [[name]+[f"{r[k]*100:.2f}" for k in keys] for name,r in zip(NAMES,records)]
    table = ax.table(cellText=values,colLabels=["모델","Accuracy ↑","Precision ↑","Recall ↑","F1 ↑","정상 오탐 ↓"],
        cellLoc="center",bbox=[0,0,1,1],colWidths=[.22,.156,.156,.156,.156,.156])
    style(table,3,6)
    for col,key in enumerate(keys,1):
        best = min(range(3),key=lambda i:records[i][key]) if key=="normal_false_positive_rate" else max(range(3),key=lambda i:records[i][key])
        table[best+1,col].get_text().set_fontweight("bold")
    fig.text(.04,.605,"시나리오별 F1 (%)",fontsize=18,fontweight="bold")
    ax = fig.add_axes([.04,.18,.92,.397]); ax.axis("off")
    values = [[TASKS[d["dataset"]]]+[f"{d['metrics'][offset+i]['f1']*100:.2f}" for i in range(3)] for d in summary["datasets"]]
    values.append(["기본 6개 평균"]+[f"{summary['basic_macro'][offset+i]['f1']*100:.2f}" for i in range(3)])
    table = ax.table(cellText=values,colLabels=["시나리오",*NAMES],cellLoc="center",bbox=[0,0,1,1],colWidths=[.34,.22,.22,.22])
    style(table,len(values),4,scenario=True)
    for i,row in enumerate(values,1):
        table[i,1+max(range(3),key=lambda j:float(row[j+1]))].get_text().set_fontweight("bold")
    supports = summary["supported_only"][offset:offset+3]
    fig.text(.04,.125,"지원 Adapter 대상만의 F1: "+" / ".join(f"{n} {pct(r['f1'])}" for n,r in zip(NAMES,supports)),fontsize=13,color="#4D5261")
    fig.text(.04,.083,"Adapter 열은 지원 대상 전문가 + 미지원 대상 전체 fallback을 포함합니다.",fontsize=13,color="#4D5261")
    fig.text(.04,.043,"분 단위 집계 · 정상만 예측한 Accuracy 82.39% · 모델 효과와 난도 완화 효과는 구분",fontsize=13,color="#4D5261")
    path = OUTPUT/f"{algorithm}_performance.png"; fig.savefig(path,dpi=150); plt.close(fig)
    return path


def style(table,rows,columns,scenario=False):
    table.auto_set_font_size(False); table.set_fontsize(16)
    for (i,j),cell in table.get_celld().items():
        cell.set_edgecolor("white"); cell.set_linewidth(2)
        if i==0:
            cell.set_facecolor("#23465D"); cell.get_text().set_color("white"); cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor(("#F0F4F8" if i%2 else "#FAFBFC") if scenario else ("#EBF3FA","#EDF6F0","#FFF4DF")[i-1])
        cell.PAD=.05


def model_body(summary,algorithm,offset):
    p = summary["pooled"][offset:offset+3]
    supported = summary["supported_only"][offset:offset+3]
    def f(task,route): return pct(next(d for d in summary["datasets"] if d["dataset"]==task)["metrics"][offset+route]["f1"])
    def m(route,key): return pct(p[route][key])
    if algorithm=="if":
        strength = f"""1. 전체 Data 모델
\t- **희귀한 피처 조합과 과거 대비 소요시간 변화를 함께 보는 모델.** 기존 분기를 유지하고 시간 변화 IF 분기를 추가했다. 시간 분기 설정은 trees=384, max_samples=2,048이다.
\t- 개별 지연 F1 **{f('individual_delay',0)}**, 입력 중단 **{f('input_stop',0)}**, 컬럼 관계 **{f('column_relation',0)}**. 현재 세 알고리즘의 전체 모델 중 개별 지연·컬럼 관계에서 상대적으로 강하다.
\t- Precision **{m(0,'precision')}**, 정상 분 오탐 **{m(0,'normal_false_positive_rate')}**: 경보의 정확도와 오탐 억제는 앙상블보다 유리하다.
2. Adapter별 전문가 모델
\t- 정상 분 오탐 **{m(1,'normal_false_positive_rate')}**로 이 알고리즘의 세 경로 중 가장 낮다. 경보를 보수적으로 내는 선택지다.
\t- 지원되는 5개 Adapter에는 해당 전문가, 그 외에는 전체 모델을 사용한다. **새 Process ID가 기존 Adapter에 속하더라도 무조건 새 업무 전용 모델을 만들지 않는다.**
3. 앙상블 모델
\t- 전체·전문가 점수를 정상 참고 분포의 스케일로 맞춰 **전체 0.9 + 전문가 0.1**로 결합하고 문맥별 threshold를 다시 보정한다. 세 알고리즘의 단순 OR가 아니다.
\t- 종합 F1 **{m(2,'f1')}**, Recall **{m(2,'recall')}**로 현재 9개 전략 중 가장 높다. 집단 지연 {f('group_delay',2)}, 컬럼 관계 {f('column_relation',2)}, 점진적 악화 {f('gradual_degradation',2)}는 IF 전체보다 개선됐다.
\t- 같은 기존 데이터에서 앙상블 F1 36.29% → 45.32%. 현재 47.78%까지의 나머지 상승에는 난도 완화 효과가 포함된다."""
        weakness = f"""1. 전체 Data 모델
\t- Recall **{m(0,'recall')}**로 이상 분을 많이 놓친다. 점진적 악화 F1 **{f('gradual_degradation',0)}**, Hard **{f('mixed_hard',0)}**는 낮다.
\t- 같은 기존 데이터의 시나리오 균등 평균 F1은 55.48% → 54.80%로 내려갔다. 종합 F1 상승을 모든 시나리오의 개선이라고 해석하면 안 된다.
2. Adapter별 전문가 모델
\t- 종합 Recall **{m(1,'recall')}**, F1 **{m(1,'f1')}**로 전체 모델보다 낮다. 컬럼 관계 {f('column_relation',1)}, 점진적 악화 {f('gradual_degradation',1)}도 약하다.
\t- **지원 Adapter만 따로 평가하면 전문가 경로 F1 {pct(supported[1]['f1'])}**, 동일 대상 전체 경로 {pct(supported[0]['f1'])}. 표의 Adapter F1에는 fallback 성능도 포함되어 있으므로 전문가 자체가 좋다고 결론 내릴 수 없다.
3. 앙상블 모델
\t- Precision **{m(2,'precision')}**, 정상 분 오탐 **{m(2,'normal_false_positive_rate')}**: 전체 모델보다 탐지력은 높지만 오탐 비용이 늘었다. 정상 사례 중 한 번 이상 경보가 난 비율은 **12.36%**다.
\t- Hard F1 **{f('mixed_hard',2)}**로 여전히 낮다. 결합·threshold 보정 전체의 효과이며, 상승분 중 전문가 정보만의 기여를 분리한 증명은 아니다."""
    elif algorithm=="kmeans":
        strength = f"""1. 전체 Data 모델
\t- **정상 군집 중심에서 얼마나 벗어났는지 보는 모델.** 새 시간 군집 후보가 선택 기준을 넘지 못해 기존 군집을 유지하고 정상 점수 보정을 개선했다.
\t- 집단 지연 F1 **{f('group_delay',0)}**, 입력 중단 **{f('input_stop',0)}**. 세 전체 모델 중 두 시나리오에서 가장 높다. Hard {f('mixed_hard',0)}도 IF·LOF 전체보다 높다.
\t- Precision **{m(0,'precision')}**, 정상 분 오탐 **{m(0,'normal_false_positive_rate')}**로 Clustering의 세 경로 중 경보 정확도·오탐 비용이 가장 유리하다.
2. Adapter별 전문가 모델
\t- 점진적 악화 F1 **{f('gradual_degradation',1)}**로 전체 {f('gradual_degradation',0)}와 앙상블 {f('gradual_degradation',2)}보다 높다. 일부 국소 변화에서는 전문가 경로가 더 유리할 수 있다는 관측이다.
\t- 지원 Adapter만 보면 F1 {pct(supported[1]['f1'])}. 그 대상의 전체 경로와 직접 비교해야 하며, fallback을 포함한 전체 표와 혼동하지 않는다.
3. 앙상블 모델
\t- 전체 0.9 + 전문가 0.1의 보정된 점수 결합. 종합 F1 **{m(2,'f1')}**, Recall **{m(2,'recall')}**로 Clustering 안에서는 가장 높다.
\t- 입력 중단 **{f('input_stop',2)}**, Hard **{f('mixed_hard',2)}**는 3개 알고리즘의 앙상블 중 가장 높다. 시간대 폭주 **{f('time_burst',2)}**도 가장 높지만 IF와의 차이는 작다.
\t- 같은 기존 데이터의 앙상블 F1 41.96% → 42.36%: 모델 자체 개선 폭은 작다. 현재 45.19%를 전부 튜닝 효과로 보지 않는다."""
        weakness = f"""1. 전체 Data 모델
\t- 점진적 악화 F1 **{f('gradual_degradation',0)}**: 작은 변화가 정상 군집 안에 머무는 유형에 취약하다는 결과다. 컬럼 관계 **{f('column_relation',0)}**도 IF보다 낮다.
\t- 같은 기존 데이터의 시나리오 균등 평균 F1 46.50% → 46.32%. 종합 점수만으로 전반적 개선이라고 단정하지 않는다.
2. Adapter별 전문가 모델
\t- Precision **{m(1,'precision')}**, 정상 분 오탐 **{m(1,'normal_false_positive_rate')}**로 Clustering 전체보다 오탐이 많다. 현재 결과는 전문가를 쓰면 자동으로 오탐이 줄어든다는 주장을 지지하지 않는다.
\t- 집단 지연 {f('group_delay',1)}, 컬럼 관계 {f('column_relation',1)}도 전체 경로보다 낮다. 해당 집단의 학습 다양성·경계 적합성을 추가 검증해야 한다.
3. 앙상블 모델
\t- 정상 분 오탐 **{m(2,'normal_false_positive_rate')}**가 전체보다 높다. 정상 사례에 한 번 이상 경보가 난 비율은 **7.96%**다.
\t- 점진적 악화 **{f('gradual_degradation',2)}**는 전문가 단독보다 낮다. 앙상블이 모든 시나리오에서 우월하지 않다. Hard도 상대적으로 높을 뿐 절대 F1은 **{f('mixed_hard',2)}**로 낮다."""
    else:
        strength = f"""1. 전체 Data 모델
\t- **주변 정상 이웃과의 국소 밀도 차이를 보는 모델.** [LOF 원리·novelty 설정](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html)을 사용하고, 소요시간 변화 분기를 추가했다. 시간 분기는 k=30, 정상 reference 최대 2,048개다.
\t- 점진적 악화 F1 **{f('gradual_degradation',0)}**로 세 전체 모델 중 가장 높다. 입력 중단 {f('input_stop',0)}, 개별 지연 {f('individual_delay',0)}도 이 LOF 앙상블보다 높거나 비슷하다.
\t- Precision **{m(0,'precision')}**로 LOF의 세 경로 중 가장 높다. 기본 6개 균등 평균 F1 **45.78%**는 LOF 앙상블 41.62%보다 높다.
2. Adapter별 전문가 모델
\t- 시간대 폭주 F1 **{f('time_burst',1)}**로 LOF 전체 {f('time_burst',0)}·앙상블 {f('time_burst',2)}보다 높다. 일부 Adapter별 국소 분포를 보는 이점이 관측된다.
\t- 정상 분 오탐 **{m(1,'normal_false_positive_rate')}**는 LOF의 세 경로 중 가장 낮다. 다만 지원 대상 전문가와 미지원 fallback이 섞인 집계다.
3. 앙상블 모델
\t- 전체 0.9 + 전문가 0.1의 문맥 보정 결합. 종합 F1 **{m(2,'f1')}**, Recall **{m(2,'recall')}**로 LOF 안에서는 가장 높다.
\t- 점진적 악화 **{f('gradual_degradation',2)}**는 세 앙상블 중 가장 높다. 작은 시간 변화 탐지의 보완 후보로 볼 수 있다.
\t- 같은 기존 데이터의 앙상블 F1 31.56% → 41.10%, Recall +10.40%p. 신규 분기의 효과는 주로 놓치던 이상을 더 잡은 쪽이며 오탐 개선과는 다르다."""
        weakness = f"""1. 전체 Data 모델
\t- 컬럼 관계 F1 **{f('column_relation',0)}**, Hard **{f('mixed_hard',0)}**, 시간대 폭주 **{f('time_burst',0)}**는 낮다. 가까운 정상 참고 벡터의 다양성·중복과 분포 변화에 민감하다.
\t- 같은 기존 데이터에서 정상 분 오탐 2.70% → 4.36%. F1 개선은 오탐 감소가 아니라 Recall 증가와의 교환관계다.
2. Adapter별 전문가 모델
\t- 종합 Recall **{m(1,'recall')}**, F1 **{m(1,'f1')}**는 전체보다 낮다. 점진적 악화 {f('gradual_degradation',1)}, Hard {f('mixed_hard',1)}도 약하다.
\t- 지원 Adapter만의 전문가 F1 {pct(supported[1]['f1'])}는 그 대상 전체 {pct(supported[0]['f1'])}보다 낮다. Adapter별 이웃 분포를 별도 점검할 필요가 있다.
3. 앙상블 모델
\t- 개별 지연 F1 **{f('individual_delay',2)}**로 LOF 전체 {f('individual_delay',0)}보다 크게 낮다. 전문가 결합이 오히려 유효 신호를 약화한 사례이며 원인 기여량은 별도 분해가 필요하다.
\t- 정상 분 오탐 **{m(2,'normal_false_positive_rate')}**, 정상 사례 경보 비율 **14.69%**로 경보 비용이 높다. 시나리오 균등 평균에서는 전체보다 낮고, Hard도 **{f('mixed_hard',2)}**에 그친다."""
    return indent(f"""### **전체적 성능 표**
---
![{TITLES[offset//3]}: 전체·Adapter·앙상블 성능과 시나리오별 F1]({{{{IMAGE_{algorithm}}}}})
현재 mild-v2의 1분 판단 결과다. 아래 분석 수치도 같은 데이터·모델을 기준으로 한다. 수치 근거는 상단에 첨부한 최종 집계와 실험 보고서다.
### 분석
---
**강점**
{strength}
**약점**
{weakness}""")


def benchmark_sections():
    bodies = [
    ("개별 지연", "같은 업무·처리량에서 한 Process 실행만 유독 느린 상황을 탐지하는지 평가한다. 절대 시간이 길다는 이유만으로 이상이라고 판단하는 모델과 업무 조건에 맞춰 평가하는 모델을 구분한다.",
     "실제 정상 실행 템플릿을 재생하고 대상 Process 하나의 소요시간만 늘린다. Adapter + Process ID + 처리 건수 집단의 정상 P95보다 길되 기존 하드 룰의 duration 상한 아래에서 생성한다. STATUS·처리 건수는 그대로이며 의존 후속 Process가 있으면 시작/종료 시간을 함께 정렬해 논리적 모순을 만들지 않는다.",
     "이상 5,000개 사례와 정상 대조 5,000개. 원래 약한/중간/강한 주입을 번갈아 만들며 seed·시점·템플릿 선택으로 다양화했다. 현재 버전은 직전 주입 편차에 1.1을 적용하되 cap·정수 반올림을 유지했다. 모든 사례에서 정확히 10% 시간이 늘어난 것은 아니다."),
    ("집단 지연", "동일 업무 스트림에서 여러 실행이 일정 구간 함께 느려지는 장애를 평가한다. 한 행의 극단값보다 집단의 지속적 지연을 보는 능력을 확인한다.",
     "36분 정상 배경을 재생하고 그 중 12~23분 구간에서 대상 Adapter + Process ID의 실행 소요시간을 늘린다. 각 대상은 해당 처리량 조건의 정상보다 느리게 하되 하드 상한은 넘지 않는다. 요청 수 자체를 폭주시키는 시나리오는 아니다.",
     "이상/정상 각각 5,000개 paired 사례. 정상 전후 구간은 남겨 장애 구간 밖 경보도 오탐으로 평가한다. 현재 주입 편차 ×1.1 보정과 상한을 적용했다."),
    ("컬럼 관계", "total=success+error처럼 즉시 검증되는 산술 오류가 아니라, 처리량과 소요시간 사이의 평소 관계가 어긋나는 이상을 평가한다. 각 값이 허용 범위여도 조합은 이상일 수 있다.",
     "같은 Adapter 안에서 실제 데이터가 지원하는 저처리량·고처리량 조건 쌍을 찾는다. 서로 다른 Process ID일 수도 있다. 저처리량 실행에 고처리량 조건의 시간 특성을, 고처리량 실행에는 저처리량 쪽 시간 특성을 넣어 count-duration 의존성을 깨뜨린다. 저처리량 쪽은 자기 조건의 P95를 넘도록 검증한다.",
     "이상/정상 각각 5,000개. STATUS=S, errorCount=0, totalCount=successCount를 유지한다. 개별 count·duration 하드 cap은 통과한다. 두 행의 시간 합만으로는 변화를 모두 설명할 수 없으며 컬럼 조합이 핵심이다."),
    ("시간대 폭주", "평소 요청이 적은 시간대에 정상 형태의 실행이 갑자기 많아질 때 탐지하는지 평가한다. 각 행이 정상이어도 전체 실행 빈도는 이상일 수 있다.",
     "한적한 0~4시대의 40분 배경에서 일부 구간에 정상 워크플로 실행을 복제·추가한다. 기본 주입은 정상 대비 약 2~5배 요청량에서 시작하며 순간 pulse·지속 sustained·두 번의 pulse 형태를 섞는다. 실행별 소요시간과 처리 건수는 정상 템플릿을 사용하고 모두 S로 유지한다.",
     "이상/정상 각각 5,000개. 기본 폭주 구간 5/8/12분. 현재는 이전 추가 실행 수의 약 1.1배를 올림해 적용했으므로 현재 최종 배수는 원래 2~5배와 정확히 같지 않다. 과거 기준 요청량은 실제 운영 트래픽 관측치가 아니라 시간대·평일/주말별 합성 정상 이력이다."),
    ("입력 중단", "평소 실행이 예상되는 업무 스트림에서 일정 시간 아무 입력도 들어오지 않는 상태를 평가한다. 이상 행 하나를 찾는 것이 아니라 로그가 비어 있는 시간창을 탐지해야 한다.",
     "50분 정상 배경에서 대상 업무의 연속 구간 실행을 통째로 제거한다. 워크플로의 Process와 대응 Transaction 컨텍스트를 함께 제거해 processCount 불일치 하드 룰에 걸리지 않게 한다. 중간 Process 단계만 삭제하는 시나리오와는 다르다.",
     "이상/정상 각각 5,000개. 현재 중단 구간은 7/14/27분(직전 6/12/24분에서 ×1.1 후 올림). 1분 grid를 유지해 실행 수 0과 구간 정답을 남긴다. 수집기는 정상이며 업무가 실행돼야 한다는 합성 가정이 있다. 실서비스에서는 스케줄·휴일·수집기 상태 없이 모든 0건을 이상이라고 확정할 수 없다."),
    ("점진적 악화", "한 번에 튀는 지연이 아니라 시간이 흐르며 조금씩 느려지는 추세를 탐지하는지 평가한다. 아직 개별 하드 상한을 넘지 않은 조기 악화를 보는 시험이다.",
     "60분 정상 배경에서 20/30/40분 구간에 잠재 지연 증가량을 점차 더한다. 선형·계단형·부드러운 곡선 증가를 섞는다. 각 실행의 원래 정상 소요시간에 가산하므로 관측된 전체 시간 값이 매번 엄격히 증가하는 것은 아니다. 단조 증가하는 것은 주입한 잠재 지연 성분이다.",
     "이상/정상 각각 5,000개. 원래 증가량은 정상 중앙값의 0.3/0.6/1.0배 수준에서 하드 cap에 맞춰 제한했고, 이후 난도 보정도 같은 cap을 유지했다. 현재는 직전 주입 증가량 ×1.1을 적용한다. 모델의 prefix/MAD·최근 추세·EWMA 피처를 검증하되 정답을 피처에 넣지는 않는다."),
    ("혼합 Hard", "6개 이상 유형이 한 업무 사례 안에 연달아 나타나며, 정상과의 차이가 작고 정상 배경도 섞인 복합 상황을 평가한다. 쉽고 극단적인 이상만 잘 잡는 모델을 구분하는 스트레스 테스트다.",
     "90분 정상 배경에 개별 지연 → 집단 지연 → 컬럼 관계 → 폭주 → 입력 중단 → 점진적 악화를 서로 다른 구간에 삽입한다. 모든 이상을 동시에 같은 순간에 넣는 것이 아니다. 지연은 정상 P95와 cap 사이 여유의 약 2~12%, 폭주는 기본 1.2~2배, 중단은 1~3분, 추세 증가는 정상 중앙값의 5/10/20% 수준에서 생성한다.",
     "혼합 이상 1,000개와 정상 대조 1,000개. STATUS=S·하드 룰 통과 조건은 동일하다. 이번 난도 완화에서 Hard는 원본과 바이트 단위로 동일하게 보존했으며, 같은 새 모델의 예측도 변경 전후 동일함을 검증했다. 낮은 Hard F1을 실제 장애 전체의 성능이라고 일반화하지 않는다.")]
    output=[]
    for i,(name,purpose,method,note) in enumerate(bodies,1):
        header=f'## {i}. {name} {{toggle="true"}}'
        body=indent(f"""### 목적
---
{purpose}
### 생성 방식
---
{method}
- **규모·조건:** {note}
- **정답:** 주입한 대상 행/구간/사건을 별도 저장한다. 정상 대조군과 정상 배경도 포함하므로 전체 파일의 모든 행을 이상으로 간주하지 않는다.
- **생성 근거:** 실제 구현 `code/evaluation_benchmark/generate.py`의 해당 시나리오와 현재 난도 보정 `code/benchmark_difficulty/mild_v2.py`; 아래 설명은 구현을 확인한 것이며 외부 논문의 생성기를 그대로 복제한 것은 아니다.""")
        output.append({"header":header,"body":header+"\n"+body})
    return output


def main():
    OUTPUT.mkdir(exist_ok=True)
    index=read(SOURCE/"evaluation_index.json"); summary_path=ROOT/index["current"]/"summary.json"
    assert digest(summary_path)==index["summary_sha256"]
    summary=read(summary_path); assert summary["protocol"]["benchmark_manifest_sha256"]==digest(ROOT/"data/evaluation/manifest.json")
    plt.rcParams.update({"font.family":"Malgun Gothic","axes.unicode_minus":False})
    images=[]; sections=[]
    for i,(a,title) in enumerate(zip(ALGORITHMS,TITLES)):
        path=draw(summary,a,i*3,title); images.append({"algorithm":a,"path":str(path),"sha256":digest(path)})
        header=(f'## 1.  Isolation Forest {{toggle="true"}}' if a=="if" else
                f'## 2. Clustering {{toggle="true"}}' if a=="kmeans" else f'## 3. LoF {{toggle="true"}}')
        sections.append({"header":header,"body":header+"\n"+model_body(summary,a,i*3)})
    sections+=benchmark_sections()
    model_intro="""<callout icon="📌" color="blue_bg">
\t**평가 기준:** 최종 후보 v18 + 현재 `esb-process-synthetic-mild-v2`, <mention-date start="2026-10-14"/> 기준. 진행 중인 모델 실험은 종료됐으며 자동 배포 결과가 아닌 연구 평가다.
\t입력은 **Process로부터 만든 연속 1분 grid와 완료 Process 행**이다. Transaction은 모델 입력이 아니고 데이터 생성·하드 룰 정합성 검증용 컨텍스트다.
\t전체 2,167,766개 분 판단의 종합 지표다. 정상/이상 비율이 다르므로 모든 분을 정상으로 예측해도 Accuracy는 82.39%다. 높은 Accuracy만으로 좋은 탐지기라고 결론 내리지 않는다.
</callout>
- **Accuracy:** 정상·이상 판단 전체 중 맞춘 비율. **Precision:** 경보 중 실제 이상 비율. **Recall:** 이상 분 중 잡은 비율. **F1:** Precision·Recall의 조화 평균. **정상 오탐률:** 정상 분 중 잘못 경보한 비율이다.
- **Adapter 표의 해석:** 전문가 지원 5종은 DB.READER, DB.WRITER, FILE.READER, FILE.WRITER, SERVICE.EXECUTOR다. 지원 대상 1,096,068분(50.56%)과 미지원 fallback 1,071,698분(49.44%)이 섞인 전체 결과이며, 순수 전문가 결과는 별도로 언급한다. 미지원 Adapter에서는 세 경로의 점수·판정이 전체와 정확히 동일하다.
- **앙상블의 뜻:** IF 안에서 전체+IF 전문가, Clustering 안에서 전체+군집 전문가, LOF 안에서 전체+LOF 전문가를 결합한다. 세 알고리즘의 경보를 OR한 것이 아니다. 현재 전체 가중치는 모두 0.9이며 결합 후 별도 정상 보정을 한다.
- **모델 개선과 난도 효과:** 같은 기존 데이터에서 앙상블 F1은 IF 36.29%→45.32%, Clustering 41.96%→42.36%, LOF 31.56%→41.10%다. 현재 성능표의 추가 상승은 난도 완화도 포함하며 모델 효과로 합산하지 않는다.
- **해석 한계:** 모든 시나리오·모든 지표가 개선된 것은 아니다. 학습 알고리즘은 비지도지만 개발 정답으로 파라미터를 선택했다. 최종 동결 뒤 새 seed의 240개/시나리오 paired 확인을 했으나 같은 합성 생성기·원본 pool을 공유하므로 외부 운영 블라인드 성능은 아니다.
**집계 근거와 상세 실험 보고서**
{{SUMMARY_ATTACHMENT}}
{{REPORT_ATTACHMENT}}
"""
    benchmark_intro="""<callout icon="🧪" color="gray_bg">
\t**공통 목적:** 실패 STATUS나 산술·SQL·시간 정합성 하드 룰만으로는 잡히지 않는 성능·관계·빈도·시간 변화 이상을 비교한다. 모든 기록의 STATUS=S이며 최종 검증에서 하드 룰 적발·미확인 플래그 모두 0건이다.
\t기본 6개 시나리오마다 이상 사례 5,000개 + 동일 정상 대조 사례 5,000개, 혼합 Hard는 각각 1,000개다. 총 **이상 31,000개 + 정상 대조 31,000개**이며 사례 수와 CSV 행 수와 1분 판단 수는 서로 다르다.
</callout>
- **원본 기반 재생:** 2025-12-24~2026-04-06, 2026-08-15~09-14, 2026-09-30~10-02의 3개 수집 기간에서 실제 실행 템플릿·Adapter·Process·처리 건수·소요시간 조건을 추출해 재생했다. 소스의 완전한 워크플로를 선택하고 count 보존, NOT NULL, 종료 시각, 재시도 정수 및 고정 하드 상한을 검증한다. ID·재생 시점은 합성으로 부여한다.
- **정상과 이상을 짝지음:** 같은 정상 실행에서 주입한 버전과 주입하지 않은 버전을 만든다. 정상 전후 배경도 남겨 오탐을 평가한다. 따라서 정답 파일은 필수이며 STATUS=S를 정상 정답으로 쓰지 않는다. 입력 중단은 해당 구간에 행 자체가 없으므로 시간창/사건 정답이 특히 중요하다.
- **파일 역할:** Process 원본 테이블과 정상 대조 테이블이 입력 재료다. `cases.csv`는 사례 구분, `process_labels.csv`는 행·출처, `events.csv`는 사건 구간, `windows.csv`는 1분 관측, `window_labels.csv`는 시간창 정답이다. 정답·변형 전 정상 건수는 모델 피처에 넣지 않는다.
- **다양성과 재현성:** 생성 seed=20261010. 템플릿·시점·난도·형태를 다양화하고 사례 fingerprint 중복을 검사한다. 수천 개 사례가 수천 개 독립 실제 장애를 의미하지는 않는다. 학습/정상 보정/탐색/확인은 사례 단위로 분리하고 paired 정상·이상도 함께 제외한다.
- **현재 난도 보정:** 직전 mild-v1의 주입 편차를 ×1.1로 강화해 더 구분하기 쉽게 했다. cap·반올림 때문에 모든 값이 정확히 10% 변하지는 않는다. 정상 대조군·Hard는 바이트 동일, 모델 점수로 탐지 쉬운 사례만 선별하지 않았다.
- **논문을 어떻게 참고했나:** [Schmidl et al., Anomaly Detection in Time Series: A Comprehensive Evaluation, PVLDB 2022](https://timeeval.github.io/evaluation-paper/)의 여러 이상 종류·알고리즘을 구분하는 평가 관점을 참고해 6유형+Hard를 분리했다. ESB 변형 공식은 자체 구현이며 해당 벤치마크의 완전 재현은 아니다.
- **평가 방식의 근거:** [Kim et al., Towards a Rigorous Evaluation of Time-series Anomaly Detection, AAAI 2022](https://arxiv.org/abs/2109.05257)의 point adjustment 과대평가 문제를 반영해 PA를 사용하지 않았다. [Tatbul et al., Precision and Recall for Time Series, NeurIPS 2018](https://arxiv.org/abs/1803.03639)의 점/구간 평가 구분을 참고해 분 지표와 사건 Recall·탐지 지연을 구분한다. 구간 평가 공식 전체를 재현한 것은 아니다.
"""
    output={"images":images,"sections":sections,"model_intro":model_intro.strip(),"benchmark_intro":benchmark_intro.strip(),
            "source_summary":str(summary_path),"source_summary_sha256":digest(summary_path),
            "benchmark_manifest_sha256":summary["protocol"]["benchmark_manifest_sha256"],
            "report_path":str(SOURCE/"RESULTS.md"),"only_documentation_generated":True}
    (OUTPUT/"content.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(OUTPUT),"images":len(images),"sections":len(sections),"source_summary_sha256":digest(summary_path)},ensure_ascii=False))


if __name__=="__main__": main()
