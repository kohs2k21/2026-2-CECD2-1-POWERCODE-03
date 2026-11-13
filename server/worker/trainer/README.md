# Trainer

`python -m trainer.runner`가 `training.jobs.v1` 큐를 소비한다. `recommend_feature`는 지정 기간의 P 관측으로 안전한 변수 정의를 얻어 버전으로 고정하고 기간 전체에 적용한다. `recommend_rule`은 정제된 행으로 Rule 초안을 생성한다. 사용자가 채택하면 별도 불변 Rule 버전이 생긴다. `train`은 train 분할만 학습해 IF·fit 산출물과 후보 구성을 만든다. `evaluate`는 고정 validation/test 분할과 수동 라벨로 적합성·지표를 기록한다. `evaluation_set`은 스냅샷 분할 하나를 불변 평가 세트로 고정하고, `compare`는 같은 세트 위에서 운영 버전과 후보 버전을 evaluate와 같은 핵심으로 평가해 비교 묶음을 남긴다(`comparison.py`, `contracts/config-comparison-evaluation.md` v1 구현 범위). 학습 성공만으로 활성화하지 않는다.

모델 호출 경계는 `../model_adapters.py`다. 변수 추천과 Process 행 Isolation Forest 학습을 지원한다. Rule v2는 GPU 생성 서비스에서 실제 측정·기준선을 받아 초안을 작성한다. 추천 작업은 관리자 전용이며 v03 LLM 감사 기록을 저장한다. IF는 train 분할에서 명시적으로 이상 라벨이 붙은 행과 결측 행을 제외하고, 남은 행을 서로 겹치지 않는 학습/판정선 집합으로 나눈다. 최소 500개 학습 행과 100개 판정선 행을 요구한다. 라벨이 없는 행도 사용하므로 정상 참조의 품질은 운영 데이터에서 별도로 검증해야 한다. 추천 결과는 허용 DSL/필드 검증을 통과해야 한다. 학습 산출물은 `ARTIFACT_ROOT` 안의 허용 경로, SHA-256, 열 순서와 fit 참조를 검증한 뒤 등록한다. 긴 작업의 큐/작업 임대는 `lease.py`가 함께 갱신하며 결과 확정 시 현재 attempt와 토큰을 재검사한다.

- 학습 산출: `detectionPolicy` 없이 `modelKey` 등 v3 키가 있는 요청은 모델 버전 schemaVersion 3(`model_version.py`), 그 밖은 기존 schemaVersion 1·2다. v3 작업은 시도 시작에 `operational_state.py`로 활성 모델 집합·판정 정책 리비전을 읽어 결과·평가 근거에 남긴다(`contracts/model-version.md`).
