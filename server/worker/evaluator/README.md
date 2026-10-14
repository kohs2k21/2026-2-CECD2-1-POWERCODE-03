# 평가 실행 위치

평가는 별도 서비스가 아닌 `trainer.runner`의 `evaluate` 분기에서 실행한다. 고정 스냅샷의 validation/test 멤버만 평가하고 `run_evaluations`에 모집단, 점수 가능 수, 정답 근거, 지표와 적용 가능성을 기록한다. 라벨이 부족하거나 점수가 누락되면 `unmeasured`로 남긴다. 이 디렉터리는 실행 진입점이 아니다.
