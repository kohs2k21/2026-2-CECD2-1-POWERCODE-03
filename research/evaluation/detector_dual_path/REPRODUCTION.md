# 기존 프리셋 재현 범위

- 대상: Issue38 inventory의 joblib 3개; 원본 파일 SHA256 일치
- 환경: Python 3.12.14·기존 연구 requirements 정확 버전; pip dependency 충돌 없음
- PROCESS: 기존 `prepare`·10개 encoder 피처·global IF/KMeans
- TIME_WINDOW: 기존 `window_features`·12개 encoder 피처·global IF/KMeans
- 1분/5분 묶음 × 두 입력 단위 × 두 알고리즘: 총 8개 직접 점수 경로
- 동일 합성 입력에서 `Detector.score - threshold`와 `SixModels.score`의 해당 global 열 대조: 절대 오차 1e-12 이내·이상 판정 동일
- 반복 점수 계산: 입력·모델 변경 없음, 유한 점수·피처 shape 확인
- 기존 회귀: 111개 통과; 원본 archive 필요한 benchmark 시험 1개 skip
- 미제공: 원본 CSV/SQLite·기존 정답 사건·학습/검증 cache·대규모 예측 파일
- 미재현: 기존 고객 데이터의 성능·분포·대규모 평가 수치·재학습 결과
- 신규 실험: 독립 합성 정상/이상으로 새 fit·정규화·임계값 설정; 기존 모델 정확도 재현과 구분
- 검증: 구현 작성자와 별도 독립 자료/환경 검수자 실행
