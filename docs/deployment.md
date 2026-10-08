Created: 2026-10-08T12:49:25+09:00
Updated: 2026-10-08T12:49:25+09:00
Author: root
Status: draft

# 앱 배포·복구

- 대상: 개인 포크의 `main` → GHCR → 서버 사용자 타이머
- 초기 서비스: `web`, `api`; 기존 PostgreSQL·워커·모델 적용 제외
- 개발: 기능 브랜치 → `dev`; 운영 버전: 검토 후 `dev` → `main`
- main CI 통과·모든 이미지 발행 후 배포 Release 공개; 발행 성공과 서버 적용 성공 구분
- 최초 실행 전 저장소 변수 `CD_ENABLED=true` 설정; 기본 미설정은 이미지 발행 비활성
- 변수 변경만으로 실행하지 않음; 활성화 이후 다음 `main` push에서 발행 시작
- 최초 GHCR 패키지 `powercode-web`, `powercode-api`: 공개 설정·서버 무인증 pull 확인
- 일반 앱 배포의 VPN/SSH 불필요; 최초 설치·장애 대응에는 기존 VPN/PEM 접속 사용

## 서버 준비

- 운영 계정의 rootless Docker·Compose·사용자 systemd·Linger=yes 필요
- 기존 컨테이너·포트 확인; 이 구성은 `powercode-app` 프로젝트, 기본 `127.0.0.1:18080`
- loopback 기본: 서버 자체 접근 또는 SSH 포트 전달; 외부 공개는 별도 방화벽/TLS·접근 경로 협의
- `/data/gst-dongguk/powercode/auth` 디렉터리 생성·운영 사용자 소유/권한 700
- API 전용 비밀 환경파일: 서버 외부 공유/Git 추가 금지·권한 600
- API 필수값: `JWT_SECRET`; 최초 계정: `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD`
- JWT secret 유지, 최초 seed 비밀번호는 임시 예시값 대신 별도 설정
- 기존 계정 이관 시 `users.json` 부모 디렉터리 유지; 배포 프로그램의 계정 자동 덮어쓰기 없음

서버의 배포 설정파일 예시(실제 비밀값 없음):

```ini
DOCKER_HOST=unix:///run/user/<운영 UID>/docker.sock
COMPOSE_PROJECT_NAME=powercode-app
API_ENV_FILE=/home/<운영 계정>/.config/powercode/api.env
AUTH_DATA_DIR=/data/gst-dongguk/powercode/auth
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=18080
```

- 위 경로는 실제 절대경로로 치환; API 환경파일 내용은 출력하지 않는 방식으로 준비
- Compose API UID 0: rootless에서 호스트 운영 계정으로 매핑, bind mount 쓰기 목적
- 로컬/CI smoke: 고유 프로젝트·임시 계정·임시 경로만 사용; 다른 프로젝트 변경 없음

## 설치·시작

- 검토한 `server/`를 서버로 전달; 아래 스크립트는 배포 프로그램·Compose만 설치
- 타이머 자동 활성화 없음; 설치 후 설정·공개 이미지 pull 검증 우선

```bash
bash /absolute/server/infra/scripts/install-deploy.sh /absolute/server /absolute/deploy.env
systemctl --user start powercode-deploy.service
journalctl --user -u powercode-deploy.service --no-pager -n 30
systemctl --user enable --now powercode-deploy.timer
```

- 최초 이미지 발행·설정 완료 전 실제 시작 보류
- 기본 주기: 직전 확인 작업 종료 후 5분, 부팅 후 첫 확인 약 2분
- 로그아웃 후 유지: Linger 설정 확인; 공유 서버 재부팅 시험은 서버 담당자와 일정 협의

## 상태·중지·복원

```bash
systemctl --user list-timers powercode-deploy.timer
systemctl --user stop powercode-deploy.timer
python3 ~/powercode-deploy/server/infra/scripts/deploy.py \
  --state-dir ~/.local/state/powercode-deploy \
  --compose-file ~/powercode-deploy/server/infra/compose.yaml --status
```

- 중지는 다음 확인을 중지; 현재 실행 중인 배포 완료 여부도 확인
- 이미지 다운로드 실패: 현재 서비스 유지·재시도 대기
- 교체 후 상태 점검 실패: 호환 가능한 직전 이미지 복원·실패 버전 반복 적용 차단
- 최초 배포 실패: 직전 버전 없음, 이번 앱 서비스만 정지·제거·영속 데이터 보존
- 수동 복원: 타이머 중지·진행 작업 종료 후 동일 설정 환경에서 위 명령에 `--rollback` 사용
- 실패 버전 재시도: 원인 해결 후 동일 설정 환경에서 `--retry-failed` 사용
- 수동 명령도 systemd와 동일한 `DOCKER_HOST`·API/데이터 경로 필요; 비밀파일을 로그/터미널에 출력하지 않을 것
- DB 스키마 복원·모델 버전 전환·볼륨 삭제·전체 Docker prune 제외
- 앱 재시작에 따른 짧은 중단 가능; 무중단 보장 없음

## 파일 변경·진단

- Compose/배포 프로그램 계약 변경: 타이머 중지 → 실행 종료 확인 → 새 설치 → 재검증
- 신규 이미지 digest만 변경: Release 배포 명세로 자동 반영
- 배포 상태·journal: Git 제외; 실패 원인은 정제된 단계/코드로 기록
- Docker 전체 초기화·기존 이미지/컨테이너 삭제로 문제 해결 금지
