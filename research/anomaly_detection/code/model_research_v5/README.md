# 논문 기반 후속 연구 (v5/v6/v7)

시작 파일은 실행 완료된 `results.ipynb`, 짧은 결론은 `RESEARCH_SUMMARY.md`.
Process-only 분 단위 IF / MiniBatch K-means 각각 전체·Adapter·앙상블 6개 경로를 비교한다.
기존 코드·데이터·모델은 보존했고 GitHub/Notion/운영 backend는 변경하지 않았다.

## 결과 요약

- v7 IF 전체: F1 **23.28% → 25.95%**, 정상 오탐 **5.70% → 1.37%**, Precision **37.20% → 69.81%**.
- Recall은 16.94% → 15.93%로 감소. 탐지량이 아니라 경보 정확성/오탐의 개선.
- IF 앙상블: F1 23.55% → 24.14%, 정상 오탐 4.22% → 1.15%.
- v7 K-means 종합 성능은 하락: 전체 F1 35.38% → 21.89%, 앙상블 35.92% → 17.09%.
- K-means 연구 기준으로는 v4 보존. v7 K-means의 관계 이상/개별 지연 변화는 특화 연구 결과이며 오탐 증가도 공개.
- 3번째 모델은 USAD 계열 시계열 Autoencoder 제안. 미구현·미평가.

## 파일

| 파일 | 역할 |
|---|---|
| `features.py` | `shift(1)` 기반 자기 과거 대비 피처, 초기/steady 구분, 정상 reset view |
| `models.py` | 일반/블록 회전 IF, 군집 반경·모양 보정 K-means, novelty LOF, 정상 분위수 보정·Adapter fallback |
| `run.py` | v5 피처·reset·회전·phase 보정 실험과 검증용 LOF. 실패도 보존 |
| `channels.py` | 트래픽/지연 2채널 점수, 경험적 정상 꼬리 보정, 지연 관측 부족 mask |
| `run_channels.py` | v6 새 검증 사례 탐색·동결·평가 |
| `run_matched.py` | v7 학습 원본만으로 짧은/연속·중앙 정상 재생, 피처/설정 탐색·동결·평가 |
| `audit.py` | 원래 라벨·시각·case index 동일성, confusion matrix 재계산, 엄격한 판정선과 정확한 fallback 검증 |
| `report.py` | 일반 막대·히스토그램·표 생성. 학습/threshold 변경 없음 |
| `build_notebook.py` | 마크다운/코드 분리 노트북 생성·실행, 그림 추출 |
| `test_research.py` | 인과성·에피소드 격리·회전·군집 거리·LOF·채널·학습 원본 선택 17개 테스트 |

## 실행

프로젝트 루트에서 `.analysis-venv/Scripts/python.exe` 사용:

```powershell
.analysis-venv/Scripts/python.exe code/model_research_v5/run_matched.py --stage evaluate
.analysis-venv/Scripts/python.exe code/model_research_v5/audit.py
.analysis-venv/Scripts/python.exe code/model_research_v5/build_notebook.py --execute
.analysis-venv/Scripts/python.exe -m unittest discover -s code/model_research_v5 -p test_research.py
```

동결된 `selection.json`이 있으면 `--stage tune`은 의도적으로 실패한다. 동일 폴더에서 덮어써 다시 고르지 않는다.
새 학습 실험은 새로운 출력 버전으로 분리해야 한다. 코드/입력/모델 hash가 다르면 기존 결과를 재사용하지 않는다.

## 저장 결과와 backend 주의

`reports/model_research_v5`, `...v6`, `...v7`에 각 버전의 모델, 설정, 확인/최종 결과, 예측 캐시를 저장했다.
v7 모델 `models_v7.joblib`: **3,451,694 bytes**. 정상 학습 참고값과 6개 평가 경로를 포함한다.
신뢰된 로컬 모델만 저장 hash와 함께 로드한다. 임의 외부 joblib 로드는 위험하다.

모델 `Suite.score(frame)`는 **이미 피처를 만든 DataFrame**을 받는다. 원본 CSV/Process 행을 그대로 받을 수 있는 모델이 아니다.
원본 Process → 중복 없는 실행 집계·완료 로그 → 과거만 이용한 1/5/15분 피처 → 선택 view → 점수/threshold 순서가 필요하다.
분 창이 비어도 행을 유지해야 입력 중단을 볼 수 있다. transaction 테이블은 입력에 쓰지 않는다.

기존 `code/process_anomaly_pipeline/artifacts/process_if_v1.joblib`와 backend 연결 코드는 변경하지 않았다.
v7 묶음에 종합 성능이 하락한 K-means도 포함되므로 **전체 묶음을 개선된 운영 버전으로 배포하지 말 것**.

## 평가 한계

- 평가 원본/라벨은 그대로지만 이전에 본 합성 평가셋. 새 블라인드 실험이 아님.
- 정상 모델 fit은 비지도. 시나리오 라벨을 사용한 설정 선택은 라벨 검증을 활용한 튜닝.
- v7 정상 생성기는 평가 도메인에 맞춘 자체 설계. 실제 정상 꼬리를 제외해도 안전하다는 의미가 아님.
- 서로 다른 원본 업무로 학습/보정/threshold 및 예약 검증 풀을 분리했지만, 검증/평가는 생성기·예약 원본 풀을 공유.
- 정상 샘플 아카이브/재생으로 학습. 수백만 행 전체 재학습 아님.
- 합산 F1와 정상 행 오탐, 시나리오 macro, 정상 사례 경보, Hard 사건 지표를 같이 볼 것.
- 90분 정상 Hard 사례의 v7 IF 전체 경보 비율 46.3%; 6종 전부 탐지한 Hard 사례는 어느 경로도 0/1,000.
- 신규 17 + 기존 개선 15 + 평가 10 + 기존 파이프라인 54 = **96개 테스트 통과**. 예측 캐시 감사도 완료.
