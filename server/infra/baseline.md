# 다중 컨테이너 실행 베이스라인

- 범위: 실행 환경·내부 연결·격리 검증
- CD 발행·서버 자동 적용: 비활성 유지
- DB migration·초기화·업그레이드·운영 볼륨 변경: 제외

## 이미지·실행 규약

| 서비스 | 이미지 빌드 파일 | 기본 실행 | 현재 준비 상태 |
|---|---|---|---|
| WebUI (`web`) | `server/frontend/Dockerfile` | nginx :8080 | 실행 가능 |
| Express API (`api`) | `server/backend/Dockerfile` | `node dist/index.js`, :5001 | 로그인·조회·SSE 실행 가능 |
| Collector | `server/worker/Dockerfile`, `SERVICE=collector` | 미준비 상태로 종료 코드 2 | 저장 라이브러리·CSV 재생만 구현 |
| Detector | 동일, `SERVICE=detector` | 미준비 상태로 종료 코드 2 | PROCESS 조회 라이브러리만 구현 |
| Trainer | 동일, `SERVICE=trainer` | 미준비 상태로 종료 코드 2 | lease 라이브러리만 구현 |
| LLM | 동일, `SERVICE=llm` | 미준비 상태로 종료 코드 2 | 추천·설명 실행기 미구현 |
| PostgreSQL | 별도 관리·격리 시험용 공식 이미지 | PostgreSQL :5432 | 앱 실행 주기와 분리 |

- 최종 7컨테이너 명세와 현재 실행 가능한 서비스 구분
- Worker 4종: 공통 Python 이미지를 서비스별 태그로 빌드; LLM 모델·추론 엔진 미포함
- 미구현 실행기: 정상 상태·단순 대기 프로세스 생성 없음
- 빌드 컨텍스트: `server/`; 연구 코드·실데이터·인증정보·가중치 제외
- `common/`·`contracts/`: Worker 이미지에 포함하는 공유 코드·계약; 별도 컨테이너 제외
- 파일저장소: 외부 영속 마운트; 별도 컨테이너 제외

저장소 루트에서 실행:

```bash
docker build -f server/frontend/Dockerfile -t powercode-web:baseline server
docker build -f server/backend/Dockerfile -t powercode-api:baseline server
for service in collector detector trainer llm; do
  docker build -f server/worker/Dockerfile --build-arg SERVICE=$service \
    -t powercode-$service:baseline server
done
```

## Collector CSV 검증

- 지정 파일의 허용 컬럼 검증만 수행하는 기존 명령 재사용
- 파일 경로: 별도 읽기 전용 마운트; 고객·운영 데이터는 격리 시험에 사용 금지
- `--apply` 미사용: DB 저장 없음
- BODY CSV 본문: 현재 재생 구현에서 제외; BODY 수집 완성 주장 금지

```bash
docker run --rm --mount type=bind,src=/absolute/synthetic-csv,dst=/input,readonly \
  powercode-collector:baseline python -m collector.replay_csv \
  --process-csv /input/process.csv --dataset-key synthetic-check \
  --replay-key check-1 --rate 0
```

## Compose 실행·연결

- 앱 구성: `compose.baseline.yaml`; PostgreSQL 서비스·DB 볼륨·SQL 자동 실행 없음
- 기본 실행: Web/API만; Worker는 `collector`·`detector`·`trainer`·`llm` profile로 명시 선택
- Worker 기본 명령: 미구현 상태로 종료 코드 2; 재시작 반복·healthy 위장 없음
- 별도 격리 DB: `compose.test.yaml` + `test-db` profile; 빈 임시 DB·tmpfs, 스키마 자동 생성 없음
- 기존 CD용 `compose.yaml`: 유지; 이번 베이스라인 실행 파일과 구분

| 환경변수 | 준비 내용 |
|---|---|
| `API_ENV_FILE` | 저장소 밖 API 환경 파일 절대 경로; JWT·실행 계정 설정 |
| `AUTH_DATA_DIR` | 실행 계정 저장 폴더 절대 경로; 기존 운영 계정 폴더의 시험 사용 금지 |
| `FILE_STORAGE_DIR` | 모델·fit·학습 산출물 폴더 절대 경로; 사전 생성·권한 확인 |
| `DATABASE_URL` | 선택 연결 주소; Compose 환경변수 기준, API 환경 파일의 동명 값보다 우선 |
| `DATABASE_NETWORK` | 기존 DB에 연결할 때만: 이미 DB가 연결된 Docker network 이름 |
| `WEB_PORT` | 기본 `18080`; 외부 공개 없이 `127.0.0.1` 바인딩 |

| 내부 주소·마운트 | 규약 |
|---|---|
| `http://api:5001` | WebUI nginx → Express API |
| `http://api:5001/health` | API 프로세스 생존; DB·추론 준비 상태를 의미하지 않음 |
| `http://api:5001/health/database` | DB 연결 상태; 미연결 시 503 |
| `http://web:8080/healthz` | nginx 프로세스 생존 |
| `postgres:5432` | 격리 시험용 DB, 같은 Compose 프로젝트 network에서만 해석 |
| 기존 DB 컨테이너 이름 | `compose.external-db.yaml`로 기존 공유 network 접속 필요 |
| `/app/data` | API만 쓰기 가능; 계정 JSON 영속 저장 |
| `/app/files` | API·Collector·Detector·LLM 읽기 전용, Trainer 쓰기 가능 |

- 실제 LLM HTTP API·Detector/Trainer 작업 연결: 미구현; 내부 주소·포트 임의 성공 처리 없음
- 호스트의 `localhost`·DB 바인딩 포트와 컨테이너 내부 주소 구분
- 운영 외부망·DB network 연결 변경: 이번 검증 제외
- `FILE_STORAGE_DIR`·`AUTH_DATA_DIR`: 사전 생성, bind `create_host_path: false`
- GPU: 별도 `compose.gpu.yaml` 선택; NVIDIA Container Toolkit·rootless GPU 설정·장치 접근 선행 확인
- GPU 예약이 CUDA 호환·추론 동작·메모리 여유를 보장하지 않음
- Trainer/Detector/LLM의 동일 GPU 사용: 메모리 경쟁 고려, 실제 모델 시험 전 별도 확인

앱 실행·중지는 앱 파일만 지정:

```bash
docker compose --env-file /absolute/baseline.env \
  -f server/infra/compose.baseline.yaml config --quiet
docker compose --env-file /absolute/baseline.env \
  -f server/infra/compose.baseline.yaml up -d --build --wait web api
docker compose --env-file /absolute/baseline.env \
  -f server/infra/compose.baseline.yaml restart api web
docker compose --env-file /absolute/baseline.env \
  -f server/infra/compose.baseline.yaml stop web api
```

- 환경 파일: 사용자 별도 준비, Git·공유 문서에 인증 값 기록 금지
- `config --quiet` 사용; 전체 config 출력은 인증 값 노출 가능
- 기존 DB 접속 필요 시에만 `-f server/infra/compose.external-db.yaml` 추가
- DB 컨테이너·기존 network는 별도 관리; 이 명령에서 생성·재생성·삭제 없음
- GPU 필요 시에만 `-f server/infra/compose.gpu.yaml` 추가
- Worker profile 시험: 종료 코드 2 정상 확인; 운영 업무 실행 성공과 구분
- `down -v`·전체 Docker prune·기존 운영 데이터 폴더 재사용 금지
