## 결론과 실행 결과

<callout color="blue_bg">
	**방향은 ‘규칙 우선 + 계층 fallback + 검증된 전문가 결합’입니다.**<br>업무별 차이를 반영하되 새 업무를 곧바로 이상으로 단정하지 않습니다. 통합 모델은 콜드스타트의 출발점이며, 학습/검증 근거가 확보된 전문가를 점진적으로 추가합니다.
</callout>

<mention-date start="2026-10-06"/> · 새 노트북 41셀/20코드 셀의 실제 실행 결과 · 32개 회귀 테스트 통과

## 분석 범위

Transaction 2,008,703행 + Process 3,714,199행 = 5,722,902 입력 행. 세 기간 전체이며 Merge를 별도 데이터로 더하지 않습니다. ESB.SQL은 문법/컬럼 정의를 읽었고 DDL은 실행하지 않았습니다.

## 먼저 구분할 세 종류의 규칙

<table fit-page-width="true" header-row="true">
	<tr>
		<td>종류</td>
		<td>예</td>
		<td>기본 처리</td>
	</tr>
	<tr>
		<td>불변 입력/정합성 규칙</td>
		<td>SQL 키·중복·정의 밖 Status·음수 시각/카운트</td>
		<td>격리. SQL 자체가 강제하는 제약과 도메인 전제를 구분</td>
	</tr>
	<tr>
		<td>설정형 업무 정책</td>
		<td>F/H·금지 업무·패턴·허용 시간대·SLA</td>
		<td>입력 없으면 default. 금지/스케줄/SLA는 기본 비활성</td>
	</tr>
	<tr>
		<td>버전 고정 통계</td>
		<td>학습 S 로그의 p99/p99.9</td>
		<td>통계 후보. 매번 재계산하지 않고 승인된 버전 적용</td>
	</tr>
</table>

F=실패, H=부분성공, P=처리중, N=미처리, Z=데이터없음, S=성공으로 구분했습니다. F/H를 원본에서 삭제하지 않으며, S만으로 확정 정상 라벨을 만들지도 않습니다.

## 모델을 나누는 기준과 데이터 지원

<table fit-page-width="true" header-row="true">
	<tr>
		<td>kind</td>
		<td>grouping</td>
		<td>groups_ready</td>
		<td>test_group_ready_pct</td>
		<td>test_unseen_pct</td>
		<td>train_log_variance_explained</td>
	</tr>
	<tr>
		<td>transaction</td>
		<td>업무</td>
		<td>20</td>
		<td>0.0000</td>
		<td>99.9398</td>
		<td>0.2581</td>
	</tr>
	<tr>
		<td>transaction</td>
		<td>카테고리</td>
		<td>2</td>
		<td>64.4727</td>
		<td>35.4743</td>
		<td>0.1816</td>
	</tr>
	<tr>
		<td>transaction</td>
		<td>카테고리+경로</td>
		<td>2</td>
		<td>39.9908</td>
		<td>35.4896</td>
		<td>0.1876</td>
	</tr>
	<tr>
		<td>transaction</td>
		<td>경로</td>
		<td>2</td>
		<td>75.4984</td>
		<td>0.0135</td>
		<td>0.0059</td>
	</tr>
	<tr>
		<td>transaction</td>
		<td>통합</td>
		<td>1</td>
		<td>100.0000</td>
		<td>0.0000</td>
		<td>0.0000</td>
	</tr>
	<tr>
		<td>process</td>
		<td>업무단계</td>
		<td>40</td>
		<td>0.0000</td>
		<td>99.8718</td>
		<td>0.9296</td>
	</tr>
	<tr>
		<td>process</td>
		<td>어댑터</td>
		<td>5</td>
		<td>99.8315</td>
		<td>0.0215</td>
		<td>0.9204</td>
	</tr>
	<tr>
		<td>process</td>
		<td>어댑터+채널</td>
		<td>3</td>
		<td>75.3893</td>
		<td>0.1165</td>
		<td>0.9205</td>
	</tr>
	<tr>
		<td>process</td>
		<td>어댑터+채널+허브</td>
		<td>3</td>
		<td>75.3893</td>
		<td>0.1165</td>
		<td>0.9205</td>
	</tr>
	<tr>
		<td>process</td>
		<td>통합</td>
		<td>1</td>
		<td>100.0000</td>
		<td>0.0000</td>
		<td>0.0000</td>
	</tr>
</table>

지원 비율은 모델 정확도가 아닙니다. ready는 적격 학습 S≥500·검증 S≥100·학습 관측일≥3이라는 실험 기본값을 만족한 그룹입니다. `test_unseen_pct`는 해당 그룹에 적격 학습 기준 로그가 없다는 뜻입니다. 분산 설명 비중은 학습 log1p 지연의 그룹 간 평균 차이이며 모델 성능이 아닙니다.

**실제 근거로 좁힌 출발점:** Process는 어댑터별 공통 모델을 먼저 비교합니다. adapterType의 시험 지원율 99.83%/분산 설명 92.04%에 비해 채널 추가는 지원율 75.39%로 낮추고 설명 비중은 거의 늘지 않았습니다. Transaction은 범주+유형(지원 64.47%/설명 18.16%)과 경로(지원 75.50%/설명 0.59%)를 모두 비교하되 통합 fallback을 유지합니다. 업무 ID별 지원율은 두 데이터 모두 0%여서 업무 모델 단독 배치는 현재 시험 구간을 커버하지 못합니다.

## 콜드스타트와 앙상블의 적용 순서

1. 새 업무 ID → 가용 공통 전문가 + 통합 모델. 업무 전문가 가중치 0.
2. 범주도 신규 → 통합 행동 모델 + 승인 규칙/SLA, 기준선 부족/OOD 표시.
3. 충분한 과거 이력·별도 검증 확보 → shadow 업무 모델 비교.
4. 검증된 전문가만 활성화. raw score를 바로 합치지 않고 과거 calibration 구간의 rank로 결합.
5. 새 데이터 자동 정상 학습/자동 승격을 피하고 기준선·모델·정책을 버전 관리.

## 상세 문서

RULE_PAGE_PLACEHOLDER

MODEL_PAGE_PLACEHOLDER

## 현재 완료와 다음 구현

**완료:** 설정형 규칙 엔진, SQL/허브·재시도 관계 점검, Status별 지연·결측, 동일 업무 S/F 비교, 패턴·시간대 후보 분석, 과거 고정 분위수, 그룹 지원량·콜드스타트 분석.

**다음:** A 통합/B 그룹/C 앙상블 모델 학습·시간/신규 업무 holdout 비교. 클러스터링·딥러닝은 후속 실험이며 현재 성능 우열이 확정되지 않았습니다.

기존 핵심 정리: <mention-page url="https://app.notion.com/p/3f125113b9ae805a88c3e24aac5d7776"/>

방법 참고: [scikit-learn outlier/novelty detection](https://scikit-learn.org/stable/modules/outlier_detection.html), [Deep One-Class Classification](https://proceedings.mlr.press/v80/ruff18a.html). 권장 라우팅과 최소 지원량은 현재 데이터에 대한 설계 제안입니다.
