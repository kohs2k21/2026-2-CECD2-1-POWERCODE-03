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
