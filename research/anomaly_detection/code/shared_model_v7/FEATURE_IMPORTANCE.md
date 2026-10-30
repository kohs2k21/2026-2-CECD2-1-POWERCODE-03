# 피처 중요도 분석

- 실행된 결과: `feature_importance.ipynb`
- 그림과 원 수치: `reports/shared_model_v7/feature_importance/`
- 실행 코드: `feature_importance.py`
- 검증: `test_feature_importance.py`
- 완료 로그와 공백 감시 설명: `INPUT_MONITORING_EXPLAINED.md`

## 방법

모델 고유의 불순도 중요도나 SHAP이 아니라 **정상 수준 치환 기반 사후 중요도**다. 기존의 정상 변환을 거친 수치 신호를 0(정상 중앙값 이하의 이탈 없음)으로 바꾼 뒤 같은 모델과 경보 기준으로 다시 판정한다.

`중요도 = 원래 F1 − 해당 신호를 정상 수준으로 바꾼 F1`

단위는 %p다. 양수가 크면 해당 정보에 크게 의존했고, 음수면 해당 정보를 제거한 뒤 F1이 개선되었다. 음수를 숨기거나 중요도 합을 100%로 맞추지 않는다. 신호 간 상호작용이 있어 개별 중요도의 합이 묶음 중요도와 같을 필요는 없다.

- 주 분석: 현재 지연 / 지연 지속·수준 변화 / 증가 추세 / 요청 증가 / 입력 공백의 다섯 묶음
- 보조 분석: 15개 원천 수치 신호 + 조건부 정상 이웃 통계 + PMF 상태 통계
- 모델 코어뿐 아니라 정상 통계·경보 보정까지 포함한 **전체 파이프라인의 의존도**
- 피처 치환 후 관측 신호에 따른 기준표 선택은 다시 계산되지만, 학습된 기준표 값과 최종 경보선 1은 고정
- ID, 정답, 사례 순서, 원 로그, 모델 파일 변경 없음

## 범위

기존 개발 진단 캐시의 **시나리오별 이상 200개와 짝 정상 200개**, 총 77,140개 분 판정이다. 시나리오별 5,000쌍 전수 평가와 구분한다. 기존 릴리스에서 동일 판정의 원래 점수를 다시 추출해 계산 일치를 확인한다. bootstrap은 정상·이상 쌍을 유지한 사례 단위로 시나리오별 400회 수행한다.

해석은 이 개발 표본에 한정한다. 개별 피처의 정보가 다른 상관 피처로 대체될 수 있고, 정상 수준으로 치환한 조합이 실제 로그에는 존재하지 않을 수 있다. 중요도가 낮다는 이유만으로 삭제하거나 인과적 원인으로 단정하면 안 된다. 삭제·재학습 성능은 별도 미사용 검증이 필요하다.

## 실행

```powershell
.\.analysis-venv\Scripts\python.exe -u code/shared_model_v7/feature_importance.py
$env:PYTHONPATH='code'
.\.analysis-venv\Scripts\python.exe -m unittest shared_model_v7.test_feature_importance -v
```

기존 수치로 그림과 노트북만 다시 생성:

```powershell
.\.analysis-venv\Scripts\python.exe -u code/shared_model_v7/feature_importance.py --render-only
```

기존 모델, 기존 `result.ipynb`, 벤치마크 데이터셋은 덮어쓰지 않는다. Git push와 운영 배포도 수행하지 않는다.
