# IF · MiniBatch K-means · LOF 최종 연구 후보

기존 모델 바이너리와 운영 연결은 유지한다. 이 폴더는 새 후보의 전체·Adapter·앙상블 9개 전략을 비교하는 연구 코드다. GitHub 업로드와 자동 배포는 하지 않는다.

## 변경 내용

- IF·LOF 전체 모델: 이미 관측된 완료 분에서 prefix/MAD, 최근 변화·기울기, 양의 변화 비율, EWMA를 파생하는 시간 변화 분기 추가. 완료 로그가 없는 분을 지연 0으로 간주하지 않는다.
- K-means: 새 시간 분기/군집 설정 후보가 탐색 제약을 넘지 못해서 기존 정상 군집을 유지. 정상 보정과 결합을 개선.
- Adapter: 불안정한 신규 시간 전문가 대신 기존 전문가의 보수적 사례 수 보정(v17) 선택.
- 앙상블: 각 알고리즘의 실제 전체 점수(v15)와 Adapter 점수(v17)를 정상 참고 분포의 스케일로 정규화해 결합. 세 알고리즘의 경보 OR가 아니다.
- 지원되지 않는 Adapter는 세 경로 모두 전체 점수와 정확히 같은 판정.

## 파일

| 파일 | 역할 |
|---|---|
| `models.py` | 실제 전체/Adapter 출력 결합, 문맥별 정상 보정, fallback |
| `run.py` | 사례 단위로 분리된 개발 데이터에서 결합 파라미터 선택·동결 |
| `confirm.py` | 동결 후 다른 seed의 240개/시나리오 추가 확인, 재선택 안 함 |
| `evaluate.py` | 기존 데이터의 모델 효과와 조정 데이터의 난도 효과 분리 |
| `inference.py` | 참조 학습 payload 제거 후 3개 joblib 후보와 hash/threshold manifest 내보내기 |
| `audit.py`, `test_research.py` | 혼동행렬 재집계, 정확한 fallback, 저장 전후 판정 동일성, 정답 비의존성 |
| `report.py`, `write_results.py` | 표·일반 막대 그래프·비교 보고서 |
| `build_notebook.py`, `result.ipynb` | 승인된 기존 v8 `results.ipynb`를 실행 결과 포함해 갱신; 동일한 최종 사본 |

v15–v17의 탈락 후보와 진단 결과는 별도 연구 폴더/보고서에 남겼다. 결과를 보고 좋은 후보만 있었던 것처럼 지우지 않는다.

## 실행

작업공간 루트에서 `.analysis-venv/Scripts/python.exe`를 사용한다. 필요한 기존 연구 모듈은 `code/` 전체에 있다. `run.py`와 `confirm.py`는 동결 결과를 덮어쓰지 않으며, 현재 결과를 다시 튜닝할 필요는 없다.

```powershell
.analysis-venv/Scripts/python.exe code/model_research_v18/evaluate.py --stage parent
.analysis-venv/Scripts/python.exe code/model_research_v18/evaluate.py --stage adjusted
.analysis-venv/Scripts/python.exe code/model_research_v18/inference.py
.analysis-venv/Scripts/python.exe code/model_research_v18/audit.py
.analysis-venv/Scripts/python.exe code/model_research_v18/write_results.py
.analysis-venv/Scripts/python.exe code/model_research_v8/build_notebook.py --execute
```

`parent`는 기존 mild-v1의 복구 백업, `adjusted`는 현재 `data/evaluation`을 읽는다. 수정한 CSV의 manifest hash가 달라지면 캐시를 새로 생성한다. 직접 생성 단계부터 재실행하면 복구/동결 보호 장치가 중단한다.

## 백엔드 입력과 출력

최종 3개 후보는 `reports/model_research_v18/handoff/`에 있다. **신뢰된 hash 확인 후** joblib를 읽고 `code`를 Python 경로에 둔다. 파일 크기/의존성/지원 Adapter/실제 threshold는 `manifest.json`에 기록한다. 학습 파이프라인의 모듈도 함께 필요하며 joblib만 보내는 독립 실행 파일이 아니다.

```python
import sys, joblib
sys.path.insert(0, "code")
model = joblib.load("reports/model_research_v18/handoff/if_v18_candidate.joblib")
# native: Process-derived contiguous 1-minute grid, past prefix retained
# completions: completed Process rows corresponding to that grid
margin, anomaly, supported = model.score_features(model.features(native, completions))
# columns: global, Adapter, ensemble; strict margin > 0 is an alert
```

Transaction 테이블은 사용하지 않는다. Process CSV 원본 한 행을 바로 넣는 API가 아니라, 과거 prefix와 빈 시간창도 포함한 연속 1분 grid/완료 행이 필요하다. 독립 요청마다 과거 이력을 삭제하면 폭주·중단·점진적 악화를 같은 방식으로 볼 수 없다. 서비스는 수집 시점 기준 과거 상태 유지, 늦은 로그 처리, 실제 운영 prior 갱신을 구현해야 한다.

## 해석·한계

- 5,000개는 사례 수이지 CSV 행 수가 아니다. 모든 STATUS=S라도 주입된 성능 장애는 이상 정답이다.
- 정답은 모델 입력/추론에 쓰지 않는다. **개발 정답으로 파라미터 선택**은 하므로 학습 알고리즘은 비지도지만 연구의 튜닝까지 완전 무라벨은 아니다.
- 같은 mild-v1에서 v14와 비교한 변화만 모델 개선이다. mild-v2의 주입 편차 ×1.1 효과는 별도로 보고한다. 정상 대조군과 Hard는 바이트 동일하게 보존했다.
- 모든 9개 F1이 높아져도 모든 Accuracy/Precision/Recall/오탐률이 좋아진 것은 아니다. 새 확인 사례에서도 탐색의 오탐 제약을 그대로 보장하지 못한다.
- 같은 합성 생성기/원본 pool을 공유하고 이전 벤치마크를 살펴본 적이 있다. 외부 운영 블라인드 검증이나 실제 장애의 성능 보장은 아니다.

근거/정량 결과: `reports/model_research_v18/RESULTS.md`, 실행된 기존 `code/model_research_v8/results.ipynb`.
