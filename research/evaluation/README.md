# 평가 실험

- 구성: 실험별 하위 패키지에 코드·설정·시험·결과 문서·실행 산출물 배치
- [Detector 실시간·추세 병행 실험 (#45)](detector_dual_path/README.md): 재생·global IF/KMeans 비교·창/주기 선택·경합/복구 시험
- [실험 결과](detector_dual_path/docs/RESULTS.md)·[재현 범위](detector_dual_path/docs/REPRODUCTION.md)·[운영 이식 전 합의](detector_dual_path/docs/HANDOFF.md)
- Git 추적: 코드·설정·시험·공유 결과 요약
- Git 제외: 각 실험 `runs/`의 상세 CSV/JSON·학습 모델·cache, 고객 원본 데이터
- 운영 배포: 확정된 구현만 `server/`; 운영 코드의 `research/` 직접 import 금지
