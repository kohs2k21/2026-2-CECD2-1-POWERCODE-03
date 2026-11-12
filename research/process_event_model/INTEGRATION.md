# 연동 시 충돌 후보와 처리 방법

| 후보 | 영향 | 처리 방법 |
|---|---|---|
| 기존 #80·#38 모델 경로 덮어쓰기 | 기존 worker 또는 연구 결과 변경 | 별도 `research/process_event_model` 경로. 서버 설정·기존 기본 모델 자동 변경 없음 |
| 같은 이름의 Python 모듈 | 다른 research 폴더 코드가 먼저 import됨 | 별도 프로세스/가상환경, 이 패키지 `code` 하나만 PYTHONPATH에 추가 |
| 최신·직전 입력 혼용 | Process 행과 분 격자 계약 불일치 | 최신 `event_workflow`, 직전 `research_workflow` 분리 |
| joblib만 복사 | 사용자 정의 클래스 누락으로 로드 실패 | `code`·모델·정책·requirements 함께 전달, import 이름 유지 |
| Windows CRLF | 경보 정책의 런타임 SHA 검증 실패 | 패키지 범위 `.gitattributes`의 LF 정책 유지, `verify_package.py` 실행 |
| 요청마다 monitor 초기화 | 과거 추세 소실 | 알고리즘별 monitor 유지, 재시작 시 명시적 reset 또는 과거 재생 |
| 동일 수신 시각 배치 분할·시간 역행 | 상태 일관성 오류 | 완료 Transaction Process와 같은 availableTime 묶음을 함께 시간순 전달 |
| 여러 worker가 같은 그룹 처리 | 분산 상태 불일치 | Adapter+Process 세션별 순서 보장·단일 소유 또는 별도 상태 저장 설계 |
| event_row_id 재사용 | 개별 결과·정답 연결 오류 | 실제 실행별 안정적인 고유 ID 공급. 자동 생성 ID는 payload 내부 식별용 |
| KST/UTC 혼합 | 날짜·시간대 기준 변화 | 최신 naive 입력은 KST, aware 입력은 절대시각. 직전 epoch 좌표계는 재현 문서 확인 |
| 임의 공백 0건 생성 | 수집 장애와 실행 없음 혼동 | 직전 격자는 수집 범위가 확인된 경우만 생성. 최신은 가상 Process 행 생성 안 함 |
| 새 학습 모델에 공개 성능 표시 | 검증되지 않은 수치 오인 | 새 모델·정상 보정·개발/시험 정답을 별도로 검증 |

## 운영 범위

- 이번 PR은 연구 모델 전달이며 운영 수집·worker/API·DB 스키마·UI 자동 전환은 미포함
- 하드 룰/입력 품질 처리는 연동 계층에서 별도 적용. 이 CLI가 모든 SQL 적합성이나 Transaction-Process 정합성을 검사하는 것은 아님
- 현재 최신은 완료된 로그를 판정하며 진행 중 작업의 종료 전 장애, 로그가 전혀 없는 동안의 새 입력 중단 경보는 검증 범위 밖
- 최종 점수는 통계 결합을 포함한 이상 증거이며 확률·장애 확정·인과 설명이 아님
- SHAP/XAI 연계는 후속 작업. 현재 반환 파생 피처만으로 모델별 기여도를 구현했다고 표현하지 않음
- GitHub CI와 로컬 연구 테스트는 별개. 기존 CI가 이 패키지의 연구 테스트까지 자동 실행한다고 가정하지 않음
