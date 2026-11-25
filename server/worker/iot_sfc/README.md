# IoT-SFC 버전 6 소량 시험

실제 공개 CSV를 IoT 관측으로 투영하여 `원천 저장 → 별도 PostgreSQL 큐 → 통계 Rule·새 IF → 판정 저장`을 시험한다.
ESB `data_processes` 필드에 다른 의미의 숫자를 넣거나 기존 ESB 모델을 재사용하지 않는다.
기존 T/P/M Collector·Detector·활성 구성·JSON 계정·화면 API를 변경하지 않는 **수동 CLI 프로토타입**이다.
기존 화면 조회·알림, 추천 LLM, 학습 작업 큐·승인 적용은 이 시험 경로와 아직 연결하지 않았다.

공식 출처: <https://data.mendeley.com/datasets/j9xffc2psb/6>, DOI `10.17632/j9xffc2psb.6`, CC BY 4.0, Abdelwahid Oubaalla.
데이터 이름 `IoT-SFC v2`와 배포 버전 `6`을 구분한다. 원본·샘플·학습 모델·판정 파일은 Git에 넣지 않는다.

## 매핑

| 원천 | 시험 경로 | 의미 |
|---|---|---|
| `ts` | `source_time` 및 `allowed_values.ts` | Zeek Unix 초 → UTC, 십진 원문 보존 |
| `uid` | `allowed_values.uid` | 연결 식별자. Transaction 또는 Process 실행 ID로 주장하지 않음 |
| `proto`, `service`, `conn_state`, `log_type` | 관측 메타데이터 | 상태를 ESB 성공·실패 코드로 바꾸지 않음 |
| `duration` | `log1p_duration` | 연결 지속 시간(초). 개별 서비스 처리 시간과 구분 |
| `orig_bytes`, `resp_bytes` | 각 `log1p_*`, `log1p_total_bytes` | 송수신 바이트와 합계 |
| `orig_pkts`, `resp_pkts` | 각 `log1p_*`, `log1p_total_pkts` | 송수신 패킷과 합계 |
| `attack`, `type_of_attack` | 별도 정답 파일·`ground_truth` 표 | 정상 기준선 선별 및 최종 평가 전용. 추론에서 읽지 않음 |

이 버전은 `log_type=conn`만 선택한다. DNS·HTTP·MQTT 행에 없는 수치를 0으로 채워 섞지 않는다.
필요 수치의 결측·음수·비유한 값·소수 패킷 수는 `unavailable`이다. BODY, payload, 계정·비밀번호, MQTT 인증값,
IP, topic, URI, 자유문, 원본 응답은 허용 목록에 없다. 저장·파일·hash 전에 제외한다.

## 먼저 파일만 시험하기 — DB 변경 없음

아래는 Ubuntu rootless Docker와 저장소 루트 기준이다. 별도 이미지·디렉터리만 만든다.
컨테이너 기본 root는 이 환경에서 호스트의 실행 사용자로 매핑된다. `--user $(id -u):$(id -g)`를 추가하지 않는다.
서버에 이 코드가 올라온 이후 실행한다. 이 작업만으로 Git commit/push 또는 서버 반영을 수행하지 않는다.

```bash
cd /home/gst-dongguk/powercode-esb
mkdir -p /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6
chmod 700 /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6
docker build -f server/worker/iot_sfc/Dockerfile -t powercode-iot-sfc:prototype server/worker

# 네트워크에서 ZIP 첫 1MiB만 요청. 899MB 전체 다운로드·전체 ZIP 무결성 검증은 하지 않는다.
# 이미 존재하는 출력 디렉터리는 덮어쓰지 않는다.
docker run --rm -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work powercode-iot-sfc:prototype sample --output /work/sample --limit 20000

# 파일을 읽는 검사. 관측 원문은 출력하지 않는다.
docker run --rm -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work:ro powercode-iot-sfc:prototype inspect --csv /work/sample/sample.csv

# 파일 변경: 새 로컬 번들 생성. DB·기존 모델·운영 활성 버전은 변경하지 않는다.
docker run --rm -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work powercode-iot-sfc:prototype prepare --csv /work/sample/sample.csv --output /work/bundle-01

# 파일 읽기·실제 CPU 추론·시험 집계. DB 변경 없음.
docker run --rm -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work:ro powercode-iot-sfc:prototype test --bundle /work/bundle-01
```

외부 파일 모델은 사용하지 않는다. `prepare`로 직접 생성한 신뢰하는 로컬 번들만 읽는다.
고정 파일명·내용 SHA256·피처 버전·scikit-learn 버전을 확인한 뒤 joblib을 읽는다.
가져온 임의 manifest의 SHA는 신뢰 경계를 대신하지 않으므로 외부 pickle/번들로 바꿔 넣지 않는다.

원본 CSV를 이미 확보했다면 `sample` 대신 `prepare --csv /work/원본.csv --limit 20000 ...`를 실행할 수 있다.
샘플은 ZIP 시작 부분의 conn 행이며 시간·공격 세션의 대표 표본이 아니다. 고정 prefix에 행이 부족하면 실패한다.
대용량 원본 다운로드·아카이브 전체 SHA 검증·공격 세션별 분할은 별도 정식 평가에서 진행한다.

## PostgreSQL로 재생하기

이하 **변경 명령**은 별도 `powercode_iot` 스키마를 생성하고 그 안에만 시험 데이터를 쓴다.
기존 DB를 재생성하지 않는다. 표가 이미 있으면 SQL이 실패하며 임의 삭제·재생성하지 않는다.
스키마 소유 계정의 DDL 권한이 필요하다. 기존 운영용 제한 역할에 자동 권한을 추가하지 않는다.

먼저 읽기 전용으로 기존 여부를 확인한다.

```bash
docker exec anomaly-postgres psql -U anomaly_app -d anomaly_detection -c "SELECT to_regnamespace('powercode_iot') AS existing_iot_schema;"
```

스키마가 없을 때만 백업 후 한 번 적용한다. 기존 서버의 DB·사용자 이름을 사용하며 비밀번호를 인자로 쓰지 않는다.

```bash
backup="/data/gst-dongguk/powercode-esb/backups/anomaly_detection_before_iot_$(date +%Y%m%d_%H%M%S).dump"
(umask 077; docker exec anomaly-postgres pg_dump -U anomaly_app -d anomaly_detection -Fc > "$backup")
test -s "$backup"
docker exec -i anomaly-postgres psql -U anomaly_app -d anomaly_detection -v ON_ERROR_STOP=1 < server/infra/prototypes/iot_sfc.sql
```

작업자는 `server/worker/.env`의 `DATABASE_URL`을 읽는다. `anomaly-net` 안에서 `anomaly-postgres:5432`에 연결할 수 있는
기존 설정이어야 한다. 파일 내용·접속 URI는 출력하지 않는다. 아래 `status` 같은 별도 명령은 없고 `report`를 사용한다.

```bash
# DB 쓰기 전 확인: .env와 DB 연결 없이 번들만 검사한다.
docker run --rm -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work:ro powercode-iot-sfc:prototype replay --bundle /work/bundle-01

# DB 변경: 선택한 모든 20,000 관측 저장. 시험 구간만 큐 등록. 라벨은 별도 표에 저장.
docker run --rm --network anomaly-net -v "$PWD/server/worker/.env:/app/.env:ro" -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work:ro powercode-iot-sfc:prototype replay --bundle /work/bundle-01 --rate 100 --apply

# DB 변경: 이 번들의 IoT 시험 큐만 소비. 큐가 비면 종료하므로 무한 대기하지 않는다.
docker run --rm --network anomaly-net -v "$PWD/server/worker/.env:/app/.env:ro" -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work:ro powercode-iot-sfc:prototype detect --bundle /work/bundle-01

# DB 읽기 전용 집계: 완료·미판정·큐 상태·정답 비교. 원천 행·인증정보는 출력하지 않는다.
docker run --rm --network anomaly-net -v "$PWD/server/worker/.env:/app/.env:ro" -v /data/gst-dongguk/powercode-esb/tmp/iot-sfc-v6:/work:ro powercode-iot-sfc:prototype report --bundle /work/bundle-01
```

`replay`가 끝나고 `detect`를 실행하는 순서가 기본이다. 먼저 `detect`를 실행하면 빈 큐를 확인하고 종료한다.
같은 번들로 재생하면 같은 ID를 쓰므로 관측·큐·판정이 중복되지 않는다. 파일·라벨 내용이 바뀌면 충돌로 거절한다.
완료 시 유효한 임대·token 확인과 결과 저장이 같은 트랜잭션이다. 소비자가 죽으면 임대 만료 후 재수행하며
3회 만료하면 dead가 된다. `report.complete`는 모든 시험 행의 판정이 저장되고 pending/leased/dead가 없을 때만 참이다.
`complete=true`여도 `unavailable`이 있을 수 있으므로 coverage도 확인한다.

## 시험 기준과 한계

- 시각의 고유값을 기준으로 앞 60%·다음 20%·마지막 20% 구간을 고정한다. 행 수 비율과 정확히 같지는 않다.
- 경계를 가로지르는 동일 uid 및 uid 없는 행은 학습·보정·시험에서 모두 제외한다. 요청 단계 관계는 생성하지 않는다.
- 학습 구간의 정상·완전 수치 행만 scaler와 IF 학습에 사용한다(최소 500행).
- 보정 구간의 정상·완전 수치 행만 IF p99 및 통계 Rule 상한 p99.5 산정에 사용한다(최소 100행).
- Rule은 연결 지속 시간·총 바이트·총 패킷의 상한 비교다. LLM 추천 모델·서비스 Rule CRUD가 만든 규칙이 아니다.
- 최종 판정은 Rule OR IF이며 각 결과와 근거 수치·임계값을 따로 저장한다.
- `precision_on_complete`, `recall_on_complete`, `false_positive_rate_on_complete`는 판정 가능·정답 확인 행 기준이다.
  `coverage`, 미판정 정상/이상 수, 전체 이상 행을 분모로 하는 `end_to_end_recall`을 함께 제공한다.
- 공격 시작 시각 정답 파일과 연결하지 않으므로 공격 세션별 탐지 지연·예측 리드타임·원인 단계 정확도를 출력하지 않는다.
- `--rate`는 과거 관측을 저장·등록하는 목표 속도이며 실제 센서 발생부터 판정까지의 지연을 의미하지 않는다.
- CPU IF와 수동 명령만 사용한다. 서버 GPU 접근·학습 작업 큐·운영 승인·UI·알림 검증은 포함하지 않는다.

## 개발 검증

저장소 루트에서 시험 전용 venv를 사용한다.

```bash
PYTHONPATH=server/worker python -m unittest discover -s server/worker/tests -p 'test_iot_sfc.py' -v
PYTHONPATH=server/worker python -m unittest discover -s server/worker/tests -p 'test_leases.py' -v
```
