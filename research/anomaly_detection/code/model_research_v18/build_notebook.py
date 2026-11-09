"""Build the USER-APPROVED existing results.ipynb and standalone final result."""
import argparse
import base64
import json
from pathlib import Path
import shutil
import sys
import nbformat as nbf
from nbclient import NotebookClient
from jupyter_client import KernelManager

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
TARGET = ROOT/"code/model_research_v8/results.ipynb"
BACKUP = ROOT/"reports/model_research_v18/notebook_before_update.ipynb"


def build():
    BACKUP.parent.mkdir(exist_ok=True)
    if not BACKUP.exists(): shutil.copy2(TARGET, BACKUP)
    cells = []
    def md(text): cells.append(nbf.v4.new_markdown_cell(text.strip()))
    def code(text): cells.append(nbf.v4.new_code_cell(text.strip()))
    md("""# IF · K-means · LOF — 개선 결과와 벤치마크 난도 효과

3개 알고리즘의 **전체 / Adapter / 앙상블** 9개 전략이다. 앙상블은 같은 알고리즘의 실제 전체 점수와 Adapter 점수를 합친다.
세 알고리즘의 경보를 단순 OR한 모델이 아니다. 데이터 난도 완화 효과와 모델 자체 효과를 구분해서 읽는다.
이전 결과 노트북은 복구용으로 보존했다. 기존 운영 모델 자동 교체나 GitHub 업로드는 하지 않았다.""")
    code("""from pathlib import Path
import sys, json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display, Markdown
ROOT = next(p for p in (Path.cwd(), *Path.cwd().parents) if (p/'code/model_research_v18/report.py').exists())
sys.path.insert(0, str(ROOT/'code'))
from model_research_v18 import report
report.setup(); pd.set_option('display.max_columns', 14); pd.set_option('display.max_rows', 100)
s = report.summary()
selection = report.read(ROOT/'reports/model_research_v18/selection.json')
display(s['protocol'])""")
    md("""## 1. 무엇을 몇 개 평가했나?

기본 6개 시나리오 각각 5,000개 이상 사건 사례, 혼합 Hard 1,000개 사례, 총 31,000개와 같은 수의 정상 대조 사례다.
CSV 행, 사례, 1분 판단은 서로 다른 단위다. 정상 배경·빈 시간창도 포함한다.
STATUS=S는 성공 기록이라는 뜻이며 주입된 성능 이상이 없다는 정답이 아니다.""")
    code("""display(pd.DataFrame([{'시나리오': report.TASKS[d['dataset']], '사례 수': d['case_count'],
    '평가 분 수': d['metrics'][0]['n'], '정상 분': d['metrics'][0]['normal'], '이상 분': d['metrics'][0]['anomaly']}
    for d in s['datasets']]))
display(Markdown(f\"모든 분을 정상이라 하면 Accuracy는 **{s['pooled'][0]['always_normal_accuracy']*100:.2f}%**다. Accuracy만 높다고 좋은 탐지기가 아니다.\"))""")
    md("""## 2. 모델만 바꿨을 때와 데이터도 완화했을 때

`기존`은 직전에 보고한 v14의 9개 결과다. `모델만 변경`은 **같은 기존 mild-v1 데이터**에서 새 모델을 평가했다.
`난도 조정 후`는 같은 새 모델을 mild-v2에 넣은 결과다. 두 상승분을 모두 모델 개선이라고 부르지 않는다.
탐색에 쓰인 보수적 기준(v12 IF/K-means, v14 LOF)과 이전에 보고한 v14 IF 연구 후보도 구분한다.""")
    code("""display(report.effects().iloc[:, :6])
display(report.metric_changes())
display(report.table(report.stage_summary('parent')['pooled']))""")
    md("""## 3. 최종 현재 데이터에서의 9개 성능

Precision은 경보 중 실제 이상 비율, Recall은 이상 분을 잡은 비율, F1은 둘의 조화 평균이다.
정상 오탐률도 함께 본다. 정상·이상 비율과 사건 길이가 바뀌면 Accuracy·F1도 달라진다.
그래프는 일반 막대이며, 각 알고리즘의 전체·Adapter·앙상블을 나란히 비교한다.""")
    code("""display(report.table(s['pooled']))
report.bars(); plt.show()""")
    md("""## 4. 시나리오별 장단점

왼쪽 F1과 오른쪽 정상 오탐을 같이 읽는다. 긴 점진적 악화가 합산 지표를 크게 좌우하므로,
6개 기본 시나리오를 같은 비중으로 평균한 macro도 따로 본다. Hard는 완화하지 않았다.
어떤 시나리오에서 좋아졌다고 모든 장애와 실제 운영 기간에서 좋아졌다고 일반화하지 않는다.""")
    code("""report.scenario_bars(); plt.show()
display(report.task_metrics())
display(report.table(s['basic_macro']))
display(report.macro_effects())
display(Markdown('종합 F1이 상승해도 같은 비중의 시나리오 평균이나 특정 시나리오 성능은 떨어질 수 있다. 따라서 종합 순위만으로 모든 업무에 적용할 모델을 확정하지 않는다.'))""")
    md("""## 5. 실제 모델 구조와 성격

| 모델 | 점수의 근거 | 이번 변경과 한계 |
|---|---|---|
| IF | 정상에서 쉽게 분리되는 희귀 조합 | 기존 분기 유지 + 정상 학습 IF 시간 변화 분기. 거리만 보는 모델이 아님 |
| K-means | 정상 군집 중심에서의 거리 | 재학습/시간 분기 후보가 기존 군집을 넘지 못해 군집 보존, 정상 보정·결합 개선 |
| LOF | 가까운 정상 이웃과의 밀도 차이 | novelty=True, 기존 조건부/트래픽 채널 + 정상 학습 시간 변화 분기. 이웃 중복·정상 다양성에 민감 |

전체는 v15의 검증된 Recall 중심 후보, Adapter는 v17의 보수적 보정 후보다.
각 알고리즘의 실제 두 출력에 정상 참고 집단에서 구한 스케일을 적용하고 mean/max·가중치·threshold를 탐색했다.
지원되지 않는 Adapter는 전체와 정확히 동일한 점수/판정으로 fallback한다.""")
    code("""display(pd.DataFrame([{'알고리즘': a, '전체 가중치': r['global_weight'], '결합': r['combination'],
    '정상 분위수': r['quantile'], '보정 alpha': r['alpha'], '탐색 경보 제약 충족': r['ensemble_relative_constraints_met']}
    for a,r in selection['families'].items()]))
manifest = report.read(ROOT/'reports/model_research_v18/handoff/manifest.json')
display(pd.DataFrame([{'알고리즘': m['algorithm'], '지원 Adapter': ', '.join(m['expert_adapters']),
    '모델 크기 (MiB)': m['bytes']/1024**2} for m in manifest['models']]).round(2))""")
    md("""## 6. 어떤 피처·설정을 개선했나?

- 완료 로그가 없는 분을 새 시간 피처에서 지연 0으로 채우지 않고 건너뛴다. 보존한 legacy 피처의 과거 처리는 별개다.
- 이미 관측된 초반 최대 8개 완료 분의 median/MAD 대비 소요시간 변화, 최근 15분 변화·추세, 양의 변화 비율, EWMA.
  초반이 정상이라고 가정하지 않는다. 초반부터 이상이면 이 피처가 놓칠 수 있어 원래 조건부 분기를 남긴다.
- IF 시간 분기 후보 max_samples 512/2,048, trees 256/384. LOF 후보 k=30/75, reference 최대 2,048.
- 군집 K와 OAS whitening, 입력 미세 수치 안정화, 처리량 조건부 잔차 시간 분기도 시험했지만 무조건 채택하지 않았다.
- 정상 보정은 독립 사례 수로 수축한다: `선택 alpha × min(사례 수 / 30, 1)`.
  30은 이 연구의 휴리스틱이며 통계적 오탐 보장이나 새 하드 룰이 아니다.

새 모델이 항상 모든 지표를 올리는 것은 아니다. 아래 실제 before/after와 탈락 실험을 근거로 판단한다.""")
    md("""## 7. 입력 분포 — 설명용 표본

청크마다 고정 seed로 최대 120분을 뽑았다. 정상/이상 집단별 합은 100%이며 전체 빈도 비율 추정치가 아니다.
실행 수는 log(1+x), 시간은 log(1+ms)다. 양끝 0.5%는 끝 막대에 합쳤지만 성능 집계에서는 자르지 않았다.
소요시간은 완료 관측이 있는 분, 새 변화·추세는 과거 완료 관측이 충분한 분만 그린다. 미관측을 계산상 채운 0은 분포에 넣지 않는다.
반대로 실행 수의 실제 0은 입력 중단을 설명하는 값이므로 포함한다. 제목 아래의 n은 그림에서 사용한 표본 수다.
새 초반 대비/추세 피처는 IF·LOF 전체 모델에 쓰이며 K-means가 새 시간 분기를 채택했다는 뜻은 아니다.""")
    code("""report.input_histograms(); plt.show()""")
    md("""## 8. 점수 분포와 threshold 선

0보다 큰 **점수 - 해당 문맥의 threshold**이면 이상이다. 정상의 오른쪽은 오탐, 이상의 왼쪽은 미탐이다.
알고리즘별 단위가 달라 패널 간 절대 너비를 비교하지 않는다. 0 동률은 정상 판정이므로 왼쪽 구간에 넣었다.
파일별 최대 5,000분 표본, 집단별 100%, 양끝 0.5% 끝 bin 처리다. 일반 나란한 막대 히스토그램이다.""")
    code("""report.score_histograms(); plt.show()
thresholds = report.thresholds()
display(thresholds.groupby('알고리즘', sort=False).head(8))
display(report.source_thresholds().groupby(['알고리즘','경로'], sort=False).head(4))
display(Markdown('단계 startup/steady는 스트림 초기/이후 문맥이며 정답 구분이 아니다. 관측 gate는 0=둘 다 미평가, 1=트래픽만, 2=지연만, 3=둘 다 평가, -1=gate를 구분하지 않는 보정이다. 정상 분위수에 alpha·사례 수 보정이 적용되므로 99% 분위수가 실제 오탐 1%를 보장하지 않는다. 앙상블 경계와 실제 사용하는 전체·지원 Adapter 경계는 handoff manifest 및 동결 모델에 기록된다.'))""")
    md("""## 9. 탐지·오탐·미탐 사례와 실제 근거

실제 판정 근거는 저장된 margin > 0 여부다. 실행 수·무실행 연속 길이·지연 피처는 설명 단서이며 인과적 원인이나 SHAP 기여도가 아니다.
각 종류에서 처음 발견된 사례를 보여준다. 대표 통계로 해석하지 않는다.""")
    code("""examples = pd.DataFrame(report.read(report.location()/'decision_examples.json'))
display(examples.groupby(['method','outcome'], sort=False).head(1)[['method','outcome','dataset','adapterType',
    'score_minus_threshold','native_execution_arrivals','zero_run','duration_p95_ms','expert_available']])""")
    md("""## 10. 사례 단위 분할과 새 확인 사례

보정 정상 90개/시나리오를 가져오면 그 paired 이상 사례도 탐색에서 함께 제외했다.
탐색 90개/시나리오, 초기 확인 180개/시나리오. 초기 확인은 구조 문제를 진단하는 데 썼으므로 독립 최종 평가라고 재사용하지 않는다.
최종 후보 동결 후 새 seed의 240개/시나리오를 추가 확인했다. 확인 결과로 다시 선택하지 않았다.
원본 pool/생성기를 공유하고 기존 벤치마크를 이미 살펴본 연구이므로 외부 블라인드 운영 성능은 아니다.""")
    code("""validation = report.read(ROOT/'reports/model_research_v18/validation_results.json')
display({k:v for k,v in validation.items() if k != 'results'})
for i,a in enumerate(('if','kmeans','lof')):
    display(report.table(validation['results'][a]['baseline']['pooled'], offset=i*3).assign(집단='기준'))
    display(report.table(validation['results'][a]['candidate']['pooled'], offset=i*3).assign(집단='최종 후보'))""")
    md("""## 11. 사건 탐지율과 최초 지연

사건에 한 번이라도 경보가 있었는지와 모든 이상 분을 잡았는지는 다르다.
point adjustment는 하지 않았다. 탐지 지연 중앙값은 탐지된 사건만 포함하며 미탐을 0초로 처리하지 않는다.""")
    code("""display(report.event_table())
display(pd.DataFrame([{'시나리오': report.TASKS[d['dataset']], **d['events']['normal_case_false_alarm_rate']} for d in s['datasets']]).round(4))
display(report.table(s['supported_only']))
display(report.table(s['fallback_only']))""")
    md("""## 12. 벤치마크 난도 조정 — 모델과 무관한 변형

기존 mild-v1의 주입 편차에 1.1을 곱했다. 절대 소요시간 전체를 10% 늘린 것이 아니다.
정수 반올림·기존 hard cap으로 실제 증가가 제한된 사례도 있다.
입력 중단은 분 단위 올림 때문에 6/12/24분에서 7/14/27분으로 바뀐다.
모델이 탐지하는 사례만 고르는 작업은 없었다. 정상 대조군·Hard는 바이트 동일, STATUS=S, 하드 룰 적발 0건.
이전 활성 데이터는 reports의 복구용 백업이고 data/evaluation에는 활성 평가셋 한 개만 남겼다.""")
    code("""audit = report.read(ROOT/'reports/benchmark_mild_v2/audit.json')
display({k:v for k,v in audit.items() if k != 'hard_rule_validation'})
display(report.table(report.stage_summary('difficulty_only')['pooled']))
display(Markdown('위 표는 원래 동결 모델들을 새 데이터에 넣은 결과로, 난도 효과만 확인하기 위한 비교다.'))""")
    md("""## 13. 논문 근거와 미적용 사항

- [LOF, SIGMOD 2000](https://sigmodrecord.org/?download_id=6631&smd_process_download=1): 이웃 밀도 비교. novelty 모드·reference cap·중복 처리는 데이터/서비스를 위한 설정.
- [Kim 등, AAAI 2022](https://arxiv.org/abs/2109.05257): point adjustment 과대평가 경계. 분·사건 지표를 분리.
- [Tatbul 등, NeurIPS 2018](https://arxiv.org/abs/1803.03639): 점/구간 평가 차이. 구간 지표 전체 공식 재현은 아니며 사건 Recall·지연을 병기.
- [Schmidl 등, PVLDB 2022](https://timeeval.github.io/evaluation-paper/): 알고리즘/이상 종류별 차이. 시나리오와 비용을 구분.
- [OAS 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.OAS.html): 공분산 수축 후보 시험. Gaussian 가정의 성능 보장을 이 로그에 그대로 적용하지 않음.
- 새 prefix/MAD·사례 수 수축·결합 규칙은 자체 엔지니어링이며 인용 논문의 완전 재현이나 확정 하드 룰이 아니다.

## 14. 파일 전달과 운영 주의

입력은 Process 파생 연속 1분 grid와 완료 Process 행이다. Transaction 테이블은 필요 없다.
빈 시간창과 과거 prefix가 있어야 폭주·중단·추세를 볼 수 있다. 단일 CSV 행만 넣어 같은 탐지가 되지는 않는다.
신뢰된 hash로 joblib를 확인하고 로드한다. 합성 prior는 실제 운영 이력으로 검증해야 한다.
기존 운영 모델/원본 기간 데이터/기존 모델 바이너리는 유지한다. 최종 후보도 자동 배포하지 않는다.""")
    code("""display(pd.DataFrame([{'알고리즘': m['algorithm'], '파일': m['file'], 'bytes': m['bytes'], 'sha256': m['sha256']} for m in manifest['models']]))
display(manifest['dependencies'])
display(report.read(ROOT/'reports/model_research_v18/audit.json'))""")
    notebook = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec":{"display_name":"Python (.analysis-venv)","language":"python","name":"python3"}})
    nbf.write(notebook, TARGET)


def execute():
    nb = nbf.read(TARGET, as_version=4); km = KernelManager(kernel_name="python3")
    km.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(nb, km=km, timeout=600, resources={"metadata":{"path":str(TARGET.parent)}},
        on_cell_start=lambda cell, cell_index: print(f"Final result cell {cell_index}: {cell.cell_type}", flush=True))
    client.execute(cleanup_kc=True); nbf.write(nb, TARGET); nbf.write(nb, HERE/"result.ipynb")
    folder = ROOT/"reports/model_research_v18/figures"; folder.mkdir(exist_ok=True); figures = []
    for i, cell in enumerate(nb.cells):
        for out in cell.get("outputs", []):
            png = out.get("data", {}).get("image/png")
            if not png: continue
            if isinstance(png, list): png = "".join(png)
            name = f"figure_{len(figures)+1:02d}.png"; (folder/name).write_bytes(base64.b64decode(png))
            figures.append({"cell":i,"file":name})
    (folder/"manifest.json").write_text(json.dumps(figures, indent=2), encoding="utf-8")
    print(f"Executed {len(nb.cells)} cells; {len(figures)} figures; user-approved existing notebook updated", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--execute", action="store_true"); args = p.parse_args(); build()
    if args.execute: execute()
