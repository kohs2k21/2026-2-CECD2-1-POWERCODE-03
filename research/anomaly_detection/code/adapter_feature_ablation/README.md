# 단일 탐지기 + Adapter 파생 피처 실험

기존 Adapter 전문가와 앙상블을 수정/삭제하지 않고, 별도 연구 폴더에서 전문가를 사용하지 않는 구조를 비교한다. 사용자 요청의 '빼기'는 이번 실험의 구조에서 전문가를 제외한다는 뜻이며 기존 v18 아티팩트를 파괴하지 않는다. GitHub 업로드·배포·Notion 편집 없음.

## 비교

- IF·MiniBatch K-means·LOF 각각 **단일 estimator**.
- `baseline`: 기존 공통 숫자 피처 28개. 이미 포함된 Adapter+Process+totalCount 조건부 정보는 유지.
- `adapter_features`: 동일 피처 + Adapter 정상 통계 기반 10개.
- 같은 정상 재생 train 19,793분, 같은 독립 정상 개발 17,925분. 정상 후보는 성공 S 및 기존 유효성 필터를 통과한 원본 실행 기반.
- 스케일러/Adapter 통계 train 전용. 전체 모델 공통 threshold, 정상 개발 오탐 목표 3%. 1%·5% 민감도 별도.
- 테스트 정답을 보고 설정/하이퍼파라미터를 선택하지 않는 1회 고정 실험. 기존 데이터와 v18 원본 hash 보존.
- 모든 기존 평가 사례를 전수 평가하고, 컬럼 관계/Hard를 제외한 주요 5개 합산도 별도 제공.

## 파일

| 파일 | 역할 |
| --- | --- |
| features.py | Adapter 참고 분포와 피처 10개, fallback |
| models.py | 단일 IF/K-means/LOF, 공통 threshold |
| run.py | 학습 동결 및 기존 벤치마크 전수 평가 |
| inference.py | backend 연구 연결용 피처 생성/추론 및 3개 실험 아티팩트 내보내기 |
| report.py | 일반 막대 그래프·점수 히스토그램·결과 노트북 생성 |
| result.ipynb | 결과 표·그림·threshold·시나리오·오탐/미탐 사례 |
| test_ablation.py | 미래 정보 불변성·미등록 Adapter·직접 ID 제외·저장 검증 |
| diagnose.py | 정상 학습·개발 사이 피처/점수 크기 변화, 설정 변경 없음 |
| audit.py | 전수 점수로 혼동행렬 재계산, 원본 보존, 실제 입력 추론 점검 |
| ADBENCH.md | ADBench 공식 구현을 검토한 적용 방안과 제약 |

결과/모델: `reports/adapter_feature_ablation/`. 기존 결과/모델 덮어쓰기 없음.

## 실행

워크스페이스 루트의 `.analysis-venv/Scripts/python.exe` 사용.

```powershell
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/run.py --stage train
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/run.py --stage evaluate
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/diagnose.py
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/run.py --stage report
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/inference.py
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/execute_notebook.py
.analysis-venv/Scripts/python.exe code/adapter_feature_ablation/audit.py
$env:PYTHONPATH='code'
.analysis-venv/Scripts/python.exe -m unittest adapter_feature_ablation.test_ablation -v
```

학습에는 hash 확인된 기존 v15 개발 캐시, 평가는 hash 확인된 현재 v18 feature cache를 재사용한다. 없으면 기존 파이프라인에서 생성해야 하며 이 스크립트가 임의로 재생성/원본 변경하지 않는다. `frozen.json`이 있으면 train 재실행 거부.

## 입력과 해석

- 원본 Process 한 행을 즉시 넣는 API가 아님. 같은 Adapter+Process 스트림의 과거 문맥을 포함하는 연속 1분 grid와 완료 Process 행 필요.
- 새 Adapter는 전체 참고 통계 fallback. 표본 수·fallback 단계는 신뢰도 진단이며 그 자체를 이상으로 판정하지 않음.
- Adapter 시간대 실행량 피처는 **해당 Process 스트림을 Adapter 정상 분포와 비교한 값**. Adapter 전체 동시 트래픽과 다름.
- 현재 독립 시나리오 재생으로는 Adapter 내부 여러 Process 동시 지연 비율/활동 Process 감소를 검증할 수 없어 제외.
- 정상 prior는 합성, 원본 S는 정상 후보이지 성능 정상 보장 아님. 외부 운영 블라인드 평가 아님.
- 한 seed 결과만으로 통계적 우열 확정 불가. 이번 실험에서 좋아지지 않은 모델도 결과 그대로 보고.
