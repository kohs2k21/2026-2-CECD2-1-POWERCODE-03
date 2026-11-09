# IF · MiniBatch K-means · LOF 실험

`result.ipynb`에서 3개 알고리즘 × 전체/Adapter/앙상블의 9가지 전략을 읽는다.
같은 알고리즘의 전체+Adapter 결합을 앙상블이라고 부르며, 세 알고리즘을 섞은 투표 모델이 아니다.

## 실행

워크스페이스 루트, Python 환경의 `PYTHONPATH=code`를 사용한다.

```powershell
.\.analysis-venv\Scripts\python.exe code/model_research_v14/run.py
.\.analysis-venv\Scripts\python.exe code/model_research_v14/evaluate.py --stage cache
.\.analysis-venv\Scripts\python.exe code/model_research_v14/evaluate.py --stage evaluate
.\.analysis-venv\Scripts\python.exe code/model_research_v14/inference.py
.\.analysis-venv\Scripts\python.exe code/model_research_v14/audit.py
.\.analysis-venv\Scripts\python.exe code/model_research_v14/build_notebook.py --execute
```

선택 결과는 동결한다. `run.py` 재실행으로 선택 파일을 덮어쓰지 않는다. 다음 튜닝은 다른 버전에 기록한다.
기존 v12 모델/원본 기간 CSV/현재 벤치마크는 변경하지 않는다.

## 파일 역할

- `data.py`: 기존 정상 source train/calibration/threshold 범위에서 다양한 60분 정상 재생 스트림 생성. 검증 템플릿을 model fit에 넣지 않는다.
- `models.py`: 별도 정상 사례의 점수로 문맥별 threshold 보정. 미지원 Adapter는 전체와 정확히 같은 결과.
- `run.py`: 정상 fit, 탐색 라벨로 후보/분위수 선택, 동결 후 확인 사례 평가. 기존보다 낮은 F1 후보는 IF/K-means를 자동 대체하지 않는다.
- `evaluate.py`: 현재 CSV를 1분 피처로 변환하고 9개 전략을 평가. 분·사건·탐지 지연·정상 사례 오탐을 구분.
- `inference.py`: benchmark CSV 없이 사용하는 신뢰된 joblib 연구 후보 저장.
- `audit.py`: 지표 재집계, 원본 데이터/모델 hash, 정확한 fallback, portable model 동일 결과 검증.
- `report.py`, `build_notebook.py`: 일반 막대/히스토그램과 판정선 0, 비교 표, 해석이 포함된 실행 노트북.
- `test_research.py`: causal prefix, label 불변성, LOF novelty/중복 처리, fallback 검증.

공통 새 피처/LOF 구현은 `model_research_v13`을 재사용한다. v13의 탈락 실험 파일도 보존한다.

## 입력 / 결과 단위

입력은 Process에서 만든 **연속 1분 grid + 완료 Process 행**이다. Transaction 테이블을 학습 입력으로 사용하지 않는다.
각 `case_id`에는 하나의 Adapter+ProcessID만 들어가고, 빈 분과 사용 가능한 과거 prefix를 보존한다.
단일 요청 1개 CSV 행만으로는 시간대 폭주나 입력 중단을 탐지할 수 없다.

주 성능 분모는 분 단위 의사결정이다. 시나리오당 5,000개는 사건/사례 수다.
정상 배경과 paired normal control을 함께 평가하며, STATUS=S가 정상 정답이라는 뜻은 아니다.

## 모델 전달

`reports/model_research_v14/handoff/`에 IF/K-means/LOF 각각의 joblib와 manifest를 저장한다.
`model_research_v14.inference.ProcessResearchMonitor.predict(native, completions)`를 사용한다.
로드 전 manifest의 hash를 확인하고 신뢰된 pickle만 사용한다. 정상 보정 사례의 원본 score 목록은 serving 모델에서 제거한다.

기존 운영 모델 자동 교체/배포는 하지 않는다. LOF가 검증 경보 예산을 넘었다면 manifest에 비교 후보 경고가 남는다.
분할 감사에서 보정 정상 270개 사례와 paired 이상 탐색 사례의 ID 공유를 확인했다.
정상 탐색/별도 확인/최종 사례와 보정 사례는 분리됐지만 탐색 전체를 사례 단위 독립이라고 부를 수 없다.
`case_partition_audit.json`과 `selection_before_case_audit.json`에 정정 및 원래 메타데이터를 보존했다.
검증에서 좋아진 IF threshold라도 새 확인 집단에서 오탐이 증가할 수 있으므로 운영 개선으로 확정하지 않는다.
합성 traffic history를 실제 이력으로 검증/교체하고 지연 도착/누락/새 업무/계절성에 대한 운영 검증이 필요하다.

## 벤치마크·기존 노트북

난도 조정 방향/폭과 기존 `results.ipynb` 대상 경로는 사용자 확인 대기다.
현재 평가 데이터는 변경하지 않았고, 이 폴더의 독립 `result.ipynb`를 추가했다.
이 때문에 데이터 난도 완화의 효과를 모델 개선 수치로 섞지 않는다.

논문·원리·재현하지 않은 부분은 실행 노트북 12절에 명시한다. 정상 재생 다양성 증가/중복 제거/문맥 보정은
이번 데이터에 대한 실험적 구현이며, 인용 논문의 성능 보장을 그대로 갖는다는 뜻이 아니다.
