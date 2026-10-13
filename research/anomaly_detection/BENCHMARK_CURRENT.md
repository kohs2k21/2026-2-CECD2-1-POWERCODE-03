# 평가 벤치마크

기존 `data/evaluation`의 난이도를 소폭 완화했다. 현재 기본 평가 경로는 이 폴더다.
별도 `evaluation_mild_v1` 폴더는 사용하지 않는다. 변경 전 데이터는 복구·비교 근거로
`reports/benchmark_before_mild_20261014`에 보관했다. 모델 학습/threshold 보정에 사용하지 않는다.

| task | 이상 사례 수 | 변경 |
|---|---:|---|
| individual_delay | 5,000 | 정상 소요시간 대비 주입 편차 1.2배 |
| group_delay | 5,000 | 같은 대상 실행들의 지연 편차 1.2배 |
| column_relation | 5,000 | 처리량을 유지하고 정상 소요시간 대비 관계 편차 1.2배 |
| time_burst | 5,000 | 기존 추가 실행 수 1.2배, 정수 올림 |
| input_stop | 5,000 | 중단 5/10/20분에서 6/12/24분 |
| gradual_degradation | 5,000 | 같은 진행 모양에서 누적 지연 증가량 1.2배 |
| mixed_hard | 1,000 | 원본 파일 그대로 유지 |

각 사례의 정상 대조군도 있으므로 이상 31,000개와 정상 31,000개다.
사례 수, Process 행 수, 1분 평가 구간 수는 서로 다르다.

## 유지한 조건

- 모든 Process/검증용 Transaction의 STATUS는 S.
- 기존 하드 룰 정책과 통계적 상한은 변경하지 않음.
- 정상 대조군과 혼합 Hard는 원본과 byte-identical.
- 원본 사례 ID/Adapter/Process/정상 배경 유지. 모델 점수로 사례를 제외/재추출하지 않음.
- 원본 예약 source-template 기준과 학습된 v12 모델/threshold 유지.

20%는 **주입 변화량**을 키운 비율이다. 난이도가 정확히 20% 줄거나 F1이 그만큼
높아진다는 뜻이 아니다. 소요시간은 고정 하드 룰 상한에서 제한되며 정수 올림 효과가 있다.
반복 업무의 처리량·상태·건수 정합성·의존 관계는 그대로 유지한다.

## 파일 사용

`processes.csv`가 관측 입력, `normal_processes.csv`가 대응 정상 입력이다.
두 세계를 한 배치에 합치지 않는다. 모델에는 정답·case_id·시나리오를 피처로 넣지 않는다.
Process만 입력하며 Transaction 파일은 관계 하드 룰 검증용이다.
사례 연결은 `cases.csv`, `process_labels.csv`, `events.csv`를 사용한다.
중단 사건은 없는 행 자체가 아니라 `events.csv`와 시간창 정답으로 평가한다.
각 사례는 독립적인 시계열이다. 서로 다른 사례의 가상 시간을 합쳐 가짜 폭주를 만들지 않는다.

`manifest.json`에 버전/원본 해시/고정 정책/변경 범위가 있고 `validation.json`은 실제 CSV
전체 규칙 검증 결과다. 생성기와 원본은 공유하므로 외부 blind 또는 실제 장애 정답셋이 아니다.
원본 결과와 같이 보고 난이도 변경 효과와 모델 개선 효과를 구분한다.

`events.csv`의 `evidence_json.difficulty_adjustment.original_evidence`는 원본 주입 근거다.
지연의 `headroom_fraction`도 원본 생성 때 선택한 값이며, 완화 후에는 정상 소요시간별로
편차를 늘려 값이 달라질 수 있다. 최종 시간은 `target_duration_range_ms`와
`process_labels.csv`의 `normal_duration_ms`/`observed_duration_ms`로 확인한다.

결과: `reports/benchmark_mild_v1/COMPARISON.md`

시각화: `code/benchmark_difficulty/comparison.ipynb`

컬럼/정답 정의와 선행 연구 기반 생성 원리는 변경 전 안내인
`reports/benchmark_before_mild_20261014/README.md`를 참고한다.
