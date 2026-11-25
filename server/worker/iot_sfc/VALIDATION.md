# IoT-SFC 소량 시험 기록

사용 데이터·환경·방법·분할·라벨 비교·한계를 포함한 상세 설명은
[실험 보고서](../../../research/evaluation/iot_sfc/README.md)를 참고한다.

검증일: 2026-11-23 (Asia/Seoul). 사용자 서버에는 접속하거나 SQL을 적용하지 않았다.
기존 로컬·서버 PostgreSQL을 변경하지 않고 별도 임시 PostgreSQL 17 컨테이너를 사용했다.

## 실제 공개 데이터

- 출처: <https://data.mendeley.com/datasets/j9xffc2psb/6>
- 공식 파일 목록 API: <https://data.mendeley.com/public-api/datasets/j9xffc2psb/files?folder_id=root&version=6>
- 파일 `iot_dataset_complete.zip`, 크기 898,729,127 bytes. HTTP 206으로 첫 1,048,576 bytes만 수신했다.
- ZIP 첫 항목 `iot_dataset_complete.csv`의 88개 헤더를 확인했다. 원본의 `password`, `mqtt_password`,
  `payload` 등은 투영 목록에서 제외했다. ZIP 전체 CRC·공식 전체 SHA 검증은 하지 않았다.
- 앞부분 conn 행 20,000개만 투영한 소량 샘플. 정상 10,620개·이상 9,380개이며 대표 표본이 아니다.
- 수치 7개를 모두 계산할 수 있는 입력은 11,158개. 나머지 행을 0으로 채우지 않았다.
- 원천 구간은 UTC 2025-04-06 14:33:35.462984 ~ 2025-04-07 14:32:05.518323.

## 실제 CPU 학습 및 추론

- Python 3.14 / numpy 2.4.4 / scikit-learn 1.8.0 / joblib 1.5.3에서 직접 학습.
- 정상 학습 3,042개, 정상 보정 707개. 시험 정답으로 피처·임계값을 선택하지 않음.
- Docker Python 3.12 이미지 빌드 및 동일 번들 로드·시험 추론에서도 같은 집계 확인.
- 시험 3,852개 중 판정 가능 1,755개, 판정 불가 2,097개. 판정 불가를 정상으로 처리하지 않음.
- 판정 가능한 행에서 TP 459 / FP 6 / TN 593 / FN 697. 판정 불가 중 이상 575 / 정상 1,522.
- `coverage=0.45560747663551404`, `end_to_end_recall=0.2651646447140381`.
- 고정 상한 통계 Rule OR IF의 기능 시험 결과다. 전체 데이터 성능·대표 공격별 성능·운영 SLA·모델 추천 성능을 뜻하지 않는다.

## PostgreSQL·큐

- SQL 신규 적용과 최종 불변 trigger를 각각 새 임시 DB에서 검증.
- 실제 샘플 20,000개와 별도 정답 저장. 시험 구간 3,852개만 원자적으로 큐에 등록.
- 실제 소비 후 done 3,852 / 미확정 0 / pending·leased·dead 0. 모든 판정 저장 후 `report.complete=true`.
- 동일 번들 재생: stored 0 / duplicate 20,000 / queued 0. 기존 판정·큐 중복 없음.
- 만료된 소비자의 ack·renew·fail 거절, 재임대 token 교체, 과거 token 완료 거절 확인.
- 소비자가 3회 종료된 상황을 임대 만료로 재현하고 dead 전환 확인.
- 고정 run의 UPDATE·DELETE를 최종 SQL trigger가 거절하는 것 확인.

## 회귀와 실행 범위

- `test_iot_sfc.py` 11개: 민감 필드 투영, 라벨 독립 수치 입력, 결측·숫자 검증, 시각·uid 분리,
  제한된 샘플 생성, Range 무시 거절, 판정 불가와 평가 분모.
- 기존 `test_leases.py` 4개: 완료·실패·갱신 fencing 및 범위별 claim 회귀 통과.
- 서버용 CLI의 sample 다운로드도 별도 컨테이너에서 확인한다. 일부 요청이 기본 Python User-Agent로
  HTTP 오류를 반환하여 명시적인 User-Agent를 추가했다. TLS 검증은 끄지 않는다.
- 실제 사용자 서버, 기존 화면·알림, GPU, 추천 LLM, 학습 작업 큐, 운영 적용은 검증하지 않았다.

임시 DB 컨테이너는 검증 후 정리한다. 원본 CSV·샘플·학습 모델·개별 판정은 저장소에 추가하지 않는다.
