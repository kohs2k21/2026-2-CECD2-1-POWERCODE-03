대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발

<mention-date start="2026-10-06"/> · `rule_and_model_strategy_analysis.ipynb`의 전량 실행 결과

Transaction 2,008,703행 / Process 3,714,199행 · 세 기간 전체 · ESB.SQL 읽기 검증 · 원본 데이터와 기존 분석 노트북 보존

<table_of_contents/>

<callout color="blue_bg">
	**권장 결론**<br>SQL/입력 불변 제약은 격리, F/H는 결과 알림, 완료·계수·거래/단계 관계는 조건부 검토로 구분합니다. 상위 1%/0.1%는 확정 장애가 아니라 **과거 학습 구간에서 고정한 통계 기준선**입니다. 원본 삭제 없이 학습 제외/격리/검토로 경로를 나눕니다.
</callout>

## 1. 설정: 입력값이 없으면 default, 의도한 0/False/빈 목록은 유지

다음 셀의 `USER_RULE_OVERRIDES`에 필요한 값만 넣습니다. 생략한 키만 default를 사용합니다.
잘못된 이름/음수 임계값/잘못된 시간대/정규식은 즉시 오류로 알려줍니다.

- `business_alert_statuses`: default F/H. Z는 데이터없음 결과이며 자동 실패로 합치지 않습니다.
- `pending_timeout_ms`: default None. SLA 미제공 시 타임아웃은 비활성입니다.
- 금지 업무·패턴·허용 시간대: default 빈 설정. TEST라는 이름이나 드문 범주를 자동으로 금지하지 않습니다.
- `count_conservation_confirmed`: default False. 계수 정의가 확정되기 전에는 검토로 분류합니다.
- 그룹 최소량 500학습/100검증/3관측일, 분위수 최소량 1,000(p99)/10,000(p99.9)은 **실험 시작값**이지 검증된 최적값이 아닙니다.

**실행 근거: 노트북 셀 5**

```python
USER_RULE_OVERRIDES = {}
CONFIG = resolve_config(USER_RULE_OVERRIDES)
OBSERVED_AS_OF = None
```

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>설정</td>
		<td>적용 값</td>
	</tr>
	<tr>
		<td>0</td>
		<td>version</td>
		<td>"rules-v1-20261006"</td>
	</tr>
	<tr>
		<td>1</td>
		<td>allowed_statuses</td>
		<td>["S", "F", "P", "N", "H", "Z"]</td>
	</tr>
	<tr>
		<td>2</td>
		<td>final_statuses</td>
		<td>["S", "F", "H", "Z"]</td>
	</tr>
	<tr>
		<td>3</td>
		<td>business_alert_statuses</td>
		<td>["F", "H"]</td>
	</tr>
	<tr>
		<td>4</td>
		<td>time_tolerance_ms</td>
		<td>1.0</td>
	</tr>
	<tr>
		<td>5</td>
		<td>pending_timeout_ms</td>
		<td>null</td>
	</tr>
	<tr>
		<td>6</td>
		<td>blocked_interfaces</td>
		<td>[]</td>
	</tr>
	<tr>
		<td>7</td>
		<td>pattern_rules</td>
		<td>[]</td>
	</tr>
	<tr>
		<td>8</td>
		<td>allowed_hours_by_interface</td>
		<td>{}</td>
	</tr>
	<tr>
		<td>9</td>
		<td>success_response_codes</td>
		<td>["0000"]</td>
	</tr>
	<tr>
		<td>10</td>
		<td>check_response_convention</td>
		<td>true</td>
	</tr>
	<tr>
		<td>11</td>
		<td>count_conservation_confirmed</td>
		<td>false</td>
	</tr>
	<tr>
		<td>12</td>
		<td>latency_quantiles</td>
		<td>[0.99, 0.999]</td>
	</tr>
	<tr>
		<td>13</td>
		<td>quantile_min_support</td>
		<td>{"0.99": 1000, "0.999": 10000}</td>
	</tr>
	<tr>
		<td>14</td>
		<td>min_group_train</td>
		<td>500</td>
	</tr>
	<tr>
		<td>15</td>
		<td>min_group_validation</td>
		<td>100</td>
	</tr>
	<tr>
		<td>16</td>
		<td>min_group_days</td>
		<td>3</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>STATUS</td>
		<td>사용자 제공 의미</td>
	</tr>
	<tr>
		<td>0</td>
		<td>S</td>
		<td>성공</td>
	</tr>
	<tr>
		<td>1</td>
		<td>F</td>
		<td>실패</td>
	</tr>
	<tr>
		<td>2</td>
		<td>P</td>
		<td>처리중</td>
	</tr>
	<tr>
		<td>3</td>
		<td>N</td>
		<td>미처리</td>
	</tr>
	<tr>
		<td>4</td>
		<td>H</td>
		<td>부분성공</td>
	</tr>
	<tr>
		<td>5</td>
		<td>Z</td>
		<td>데이터없음</td>
	</tr>
</table>

### 1.1 SQL에서 확정되는 것과 확정되지 않는 것

Transaction PK: **TRANSACTION_ID + PROCESS_HUB_ID + RETRY_COUNT**.
Process PK: **TRANSACTION_ID + PROCESS_ID + RETRY_COUNT**. PROCESS_HUB_ID도 NOT NULL입니다.

STATUS·시작/종료시각·카운트는 SQL상 nullable입니다. SQL은 상태별 종료 의무, 성공 응답코드,
total=success+error, 거래/단계 상태 전파를 CHECK/FK로 강제하지 않습니다.
따라서 이들을 모두 ‘SQL상 불가능한 오류’라고 부르면 안 됩니다.

데이터 연결은 기간 + 거래 ID + 허브 + 재시도를 사용하고, 매칭하지 못한 행은 검토로 남깁니다.
향후 재시도 단계 번호와 거래 재시도 번호가 다른 시스템이면 연결 계약을 별도로 정해야 합니다.

**실행 근거: 노트북 셀 7**

<details>
<summary>셀 7 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>table</td>
			<td>column</td>
			<td>type</td>
			<td>sql_not_null</td>
			<td>primary_key</td>
			<td>meaning</td>
		</tr>
		<tr>
			<td>0</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>TRANSACTION_ID</td>
			<td>VARCHAR2(80)</td>
			<td>True</td>
			<td>True</td>
			<td>트랜잭션 아이디</td>
		</tr>
		<tr>
			<td>1</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>INTERFACE_ID</td>
			<td>VARCHAR2(30)</td>
			<td>False</td>
			<td>False</td>
			<td>인터페이스 아이디</td>
		</tr>
		<tr>
			<td>2</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>PROCESS_HUB_ID</td>
			<td>VARCHAR2(20)</td>
			<td>True</td>
			<td>True</td>
			<td>처리 허브 아이디</td>
		</tr>
		<tr>
			<td>3</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>START_CHANNEL_ID</td>
			<td>VARCHAR2(20)</td>
			<td>False</td>
			<td>False</td>
			<td>시작 채널 아이디</td>
		</tr>
		<tr>
			<td>4</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>END_CHANNEL_ID</td>
			<td>VARCHAR2(20)</td>
			<td>False</td>
			<td>False</td>
			<td>종료 채널 아이디</td>
		</tr>
		<tr>
			<td>5</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>PROCESS_COUNT</td>
			<td>NUMBER</td>
			<td>False</td>
			<td>False</td>
			<td>프로세스 수</td>
		</tr>
		<tr>
			<td>6</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>STATUS</td>
			<td>VARCHAR2(10)</td>
			<td>False</td>
			<td>False</td>
			<td>처리 상태 코드</td>
		</tr>
		<tr>
			<td>7</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>RESPONSE_CODE</td>
			<td>VARCHAR2(10)</td>
			<td>False</td>
			<td>False</td>
			<td>응답 코드</td>
		</tr>
		<tr>
			<td>8</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>RESPONSE_MESSAGE</td>
			<td>VARCHAR2(500)</td>
			<td>False</td>
			<td>False</td>
			<td>응답 메시지</td>
		</tr>
		<tr>
			<td>9</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>INTERFACE_TYPE</td>
			<td>VARCHAR2(10)</td>
			<td>False</td>
			<td>False</td>
			<td>인터페이스 타입</td>
		</tr>
		<tr>
			<td>10</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>CATEGORY_NAME</td>
			<td>VARCHAR2(75)</td>
			<td>False</td>
			<td>False</td>
			<td>카테고리 이름</td>
		</tr>
		<tr>
			<td>11</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>START_TIME</td>
			<td>TIMESTAMP</td>
			<td>False</td>
			<td>False</td>
			<td>트랜잭션 시작 시간</td>
		</tr>
		<tr>
			<td>12</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>END_TIME</td>
			<td>TIMESTAMP</td>
			<td>False</td>
			<td>False</td>
			<td>트랜잭션 종료 시간</td>
		</tr>
		<tr>
			<td>13</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>PROCESS_TIME</td>
			<td>NUMBER</td>
			<td>False</td>
			<td>False</td>
			<td>트랜잭션 처리 시간</td>
		</tr>
		<tr>
			<td>14</td>
			<td>EC_TRANSACTION_MONITORING</td>
			<td>RETRY_COUNT</td>
			<td>NUMBER</td>
			<td>True</td>
			<td>True</td>
			<td>재시도 횟수</td>
		</tr>
		<tr>
			<td>15</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>TRANSACTION_ID</td>
			<td>VARCHAR2(80)</td>
			<td>True</td>
			<td>True</td>
			<td>트랜잭션 아이디</td>
		</tr>
		<tr>
			<td>16</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>PROCESS_ID</td>
			<td>VARCHAR2(36)</td>
			<td>True</td>
			<td>True</td>
			<td>프로세스 아이디</td>
		</tr>
		<tr>
			<td>17</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>DEPEND_PROCESS_ID</td>
			<td>VARCHAR2(36)</td>
			<td>False</td>
			<td>False</td>
			<td>의존 프로세스 아이디</td>
		</tr>
		<tr>
			<td>18</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>PROCESS_HUB_ID</td>
			<td>VARCHAR2(30)</td>
			<td>True</td>
			<td>False</td>
			<td>처리 허브 채널 아이디</td>
		</tr>
		<tr>
			<td>19</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>ADAPTER_TYPE</td>
			<td>VARCHAR2(30)</td>
			<td>False</td>
			<td>False</td>
			<td>어댑터 종류</td>
		</tr>
		<tr>
			<td>20</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>CHANNEL_ID</td>
			<td>VARCHAR2(20)</td>
			<td>False</td>
			<td>False</td>
			<td>처리 채널 아이디</td>
		</tr>
		<tr>
			<td>21</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>STATUS</td>
			<td>VARCHAR2(10)</td>
			<td>False</td>
			<td>False</td>
			<td>처리 상태 코드</td>
		</tr>
		<tr>
			<td>22</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>RESPONSE_CODE</td>
			<td>VARCHAR2(10)</td>
			<td>False</td>
			<td>False</td>
			<td>응답 코드</td>
		</tr>
		<tr>
			<td>23</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>RESPONSE_MESSAGE</td>
			<td>VARCHAR2(500)</td>
			<td>False</td>
			<td>False</td>
			<td>응답 메시지</td>
		</tr>
		<tr>
			<td>24</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>TOTAL_COUNT</td>
			<td>NUMBER</td>
			<td>False</td>
			<td>False</td>
			<td>전체 건수</td>
		</tr>
		<tr>
			<td>25</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>SUCCESS_COUNT</td>
			<td>NUMBER</td>
			<td>False</td>
			<td>False</td>
			<td>성공 건수</td>
		</tr>
		<tr>
			<td>26</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>ERROR_COUNT</td>
			<td>NUMBER</td>
			<td>False</td>
			<td>False</td>
			<td>실패 건수</td>
		</tr>
		<tr>
			<td>27</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>START_TIME</td>
			<td>TIMESTAMP</td>
			<td>False</td>
			<td>False</td>
			<td>프로세스 시작 시간</td>
		</tr>
		<tr>
			<td>28</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>END_TIME</td>
			<td>TIMESTAMP</td>
			<td>False</td>
			<td>False</td>
			<td>프로세스 종료 시간</td>
		</tr>
		<tr>
			<td>29</td>
			<td>EC_PROCESS_MONITORING</td>
			<td>RETRY_COUNT</td>
			<td>NUMBER</td>
			<td>True</td>
			<td>True</td>
			<td>재시도 횟수</td>
		</tr>
	</table>
</details>

<details>
<summary>셀 7 · 근거 표 2 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>rule</td>
			<td>type</td>
			<td>condition</td>
			<td>action</td>
			<td>precondition</td>
		</tr>
		<tr>
			<td>0</td>
			<td>missing_sql_key</td>
			<td>SQL 불변</td>
			<td>PK/NOT NULL 필드 결측</td>
			<td>quarantine</td>
			<td>SQL 정의</td>
		</tr>
		<tr>
			<td>1</td>
			<td>duplicate_sql_key</td>
			<td>SQL 불변</td>
			<td>동일 기간 내 복합 PK 충돌</td>
			<td>quarantine</td>
			<td>원본 exact duplicate 제거 후 충돌</td>
		</tr>
		<tr>
			<td>2</td>
			<td>invalid_status</td>
			<td>상태 사전</td>
			<td>결측이 아닌 상태가 S/F/P/N/H/Z 밖</td>
			<td>quarantine</td>
			<td>사용자 제공 상태 사전</td>
		</tr>
		<tr>
			<td>3</td>
			<td>negative_duration</td>
			<td>시각 정합성</td>
			<td>종료가 시작보다 tolerance 이상 앞섬</td>
			<td>quarantine</td>
			<td>서로 동일 실행·동일 시계라는 전제</td>
		</tr>
		<tr>
			<td>4</td>
			<td>invalid_count_domain</td>
			<td>도메인 규칙</td>
			<td>건수/재시도 값이 음수 또는 비정수</td>
			<td>quarantine</td>
			<td>SQL NUMBER 자체는 CHECK를 선언하지 않음</td>
		</tr>
		<tr>
			<td>5</td>
			<td>missing_status</td>
			<td>품질 검토</td>
			<td>상태가 비어 있음</td>
			<td>review</td>
			<td>SQL STATUS는 nullable</td>
		</tr>
		<tr>
			<td>6</td>
			<td>missing_start</td>
			<td>품질 검토</td>
			<td>시작시각 없음</td>
			<td>review</td>
			<td>SQL START_TIME은 nullable</td>
		</tr>
		<tr>
			<td>7</td>
			<td>final_missing_end</td>
			<td>완료/수집 검토</td>
			<td>S/F/H/Z인데 종료시각 없음</td>
			<td>review</td>
			<td>N/P의 종료 결측과 구분</td>
		</tr>
		<tr>
			<td>8</td>
			<td>business_status</td>
			<td>설정형 정책</td>
			<td>설정한 F/H 상태</td>
			<td>business_alert</td>
			<td>실행 결과 알림; 확정 AI 이상 정답 아님</td>
		</tr>
		<tr>
			<td>9</td>
			<td>pending_timeout</td>
			<td>설정형 정책</td>
			<td>N/P 경과시간이 설정 SLA 초과</td>
			<td>business_alert</td>
			<td>default=None: 비활성; 실제 관측 cutoff 필수</td>
		</tr>
		<tr>
			<td>10</td>
			<td>blocked_interface</td>
			<td>설정형 정책</td>
			<td>설정된 금지 interfaceId</td>
			<td>business_alert</td>
			<td>default=[]: 업무명을 자동 금지하지 않음</td>
		</tr>
		<tr>
			<td>11</td>
			<td>configured_pattern</td>
			<td>설정형 정책</td>
			<td>설정된 범주 조합/정규식 만족</td>
			<td>business_alert</td>
			<td>default=[]: 조건들의 AND, 규칙들의 OR</td>
		</tr>
		<tr>
			<td>12</td>
			<td>outside_allowed_hours</td>
			<td>설정형 정책</td>
			<td>업무별 허용 KST 시간대 밖</td>
			<td>business_alert</td>
			<td>default={}: 이력의 희소 시간대를 자동 금지하지 않음</td>
		</tr>
		<tr>
			<td>13</td>
			<td>response_convention</td>
			<td>조건부 정합성</td>
			<td>S/응답코드 불일치 또는 F/성공코드</td>
			<td>review</td>
			<td>0000 관례는 SQL 제약이 아님; 설정 가능</td>
		</tr>
		<tr>
			<td>14</td>
			<td>count_conservation</td>
			<td>조건부 정합성</td>
			<td>완료 Process에서 total != success+error</td>
			<td>review</td>
			<td>confirmed=True일 때만 학습 격리</td>
		</tr>
		<tr>
			<td>15</td>
			<td>success_with_error</td>
			<td>조건부 정합성</td>
			<td>Process S인데 errorCount&gt;0</td>
			<td>review</td>
			<td>부분성공 H와 구별; 계수 정의 확인</td>
		</tr>
		<tr>
			<td>16</td>
			<td>tx_success_all_process_failed</td>
			<td>관계 정합성</td>
			<td>Tx S + 전체 기대 단계가 관측되고 모두 F</td>
			<td>review</td>
			<td>허브·재시도 일치와 수집 완전성 필요</td>
		</tr>
		<tr>
			<td>17</td>
			<td>process_count_mismatch</td>
			<td>관계 정합성</td>
			<td>완료 거래의 선언 단계 수와 관측 단계 수 차이</td>
			<td>review</td>
			<td>로그 수집 누락/조건부 단계 가능</td>
		</tr>
		<tr>
			<td>18</td>
			<td>orphan_process</td>
			<td>관계/수집 품질</td>
			<td>동일 허브·재시도 거래에 연결 실패</td>
			<td>review</td>
			<td>현재 추출 범위에 없는 부모일 수 있음</td>
		</tr>
		<tr>
			<td>19</td>
			<td>reference_p99/p999</td>
			<td>버전 고정 통계</td>
			<td>학습 S 로그의 고정 분위수 초과</td>
			<td>statistical_candidate</td>
			<td>절대 하드 룰 아님; 그룹→공통→통합 fallback</td>
		</tr>
	</table>
</details>

**DDL 확인**

전체 4개 테이블, 51개 컬럼 정의를 읽었습니다.

**의미:** 메시지 테이블 정의도 확인하지만 이번 수치 분석은 Transaction/Process 범위입니다. DDL은 실행하지 않습니다.

**설계 반영:** SQL 불변 제약과 운영 계약·통계 기준을 서로 다른 종류로 관리합니다.

## 2. 세 기간 전체 로드와 관측 품질

파일명이 뒤바뀐 최근 자료도 내용의 스키마로 찾습니다. 기존 공통 로더를 재사용하며 exact duplicate 제거 수,
시각/수치 파싱 실패, 출처, 관측 범위를 함께 표시합니다. 기존 노트북은 수정하지 않습니다.

**실행 근거: 노트북 셀 9**

<table fit-page-width="true" header-row="true">
	<tr>
		<td>source_period</td>
		<td>source_file</td>
		<td>rows</td>
		<td>invalid_numeric_values</td>
		<td>observed_days</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
		<td>1768054</td>
		<td>0</td>
		<td>59</td>
	</tr>
	<tr>
		<td>26년8월15일-26년9월14일</td>
		<td>26년8월15일-26년9월14일\transactions.csv</td>
		<td>213381</td>
		<td>0</td>
		<td>19</td>
	</tr>
	<tr>
		<td>26년9월30일-26년10월2일</td>
		<td>26년9월30일-26년10월2일\EC_MESSAGE_BODY.csv</td>
		<td>27268</td>
		<td>0</td>
		<td>3</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>source_period</td>
		<td>source_file</td>
		<td>rows</td>
		<td>invalid_numeric_values</td>
		<td>observed_days</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
		<td>3473285</td>
		<td>0</td>
		<td>60</td>
	</tr>
	<tr>
		<td>26년8월15일-26년9월14일</td>
		<td>26년8월15일-26년9월14일\processes.csv</td>
		<td>213509</td>
		<td>0</td>
		<td>19</td>
	</tr>
	<tr>
		<td>26년9월30일-26년10월2일</td>
		<td>26년9월30일-26년10월2일\EC_TRANSACTION_MONITORING.csv</td>
		<td>27405</td>
		<td>0</td>
		<td>3</td>
	</tr>
</table>

전체 Transaction=2,008,703, Process=3,714,199, 입력 합=5,722,902

### 2.1 Status 구성과 종료시각 결측: P/N을 실패로 합치지 않기

S=성공, F=실패, P=처리중, N=미처리, H=부분성공, Z=데이터없음입니다.
P/N의 endTime 결측은 완료 로그 누락과 다르게 해석해야 합니다. 스냅샷 시각·SLA 없이 오래 대기했다고 확정하지 않습니다.

**실행 근거: 노트북 셀 11**

<details>
<summary>셀 11 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>kind</td>
			<td>source_period</td>
			<td>status</td>
			<td>rows</td>
			<td>valid_duration</td>
			<td>missing_end</td>
			<td>missing_end_pct</td>
		</tr>
		<tr>
			<td>0</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>F</td>
			<td>306172</td>
			<td>306172</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>1</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>H</td>
			<td>58</td>
			<td>58</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>2</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>P</td>
			<td>7061</td>
			<td>1</td>
			<td>7060</td>
			<td>99.986</td>
		</tr>
		<tr>
			<td>3</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>S</td>
			<td>1454528</td>
			<td>1454528</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>4</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>Z</td>
			<td>235</td>
			<td>235</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>5</td>
			<td>Transaction</td>
			<td>26년8월15일-26년9월14일</td>
			<td>F</td>
			<td>132036</td>
			<td>132036</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>6</td>
			<td>Transaction</td>
			<td>26년8월15일-26년9월14일</td>
			<td>H</td>
			<td>1</td>
			<td>1</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>7</td>
			<td>Transaction</td>
			<td>26년8월15일-26년9월14일</td>
			<td>S</td>
			<td>81344</td>
			<td>81344</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>8</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>F</td>
			<td>43</td>
			<td>43</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>9</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>H</td>
			<td>2</td>
			<td>2</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>10</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>S</td>
			<td>27180</td>
			<td>27180</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>11</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>Z</td>
			<td>43</td>
			<td>43</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>12</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>F</td>
			<td>861</td>
			<td>851</td>
			<td>10</td>
			<td>1.161</td>
		</tr>
		<tr>
			<td>13</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>H</td>
			<td>35</td>
			<td>35</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>14</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>N</td>
			<td>565328</td>
			<td>1</td>
			<td>565322</td>
			<td>99.999</td>
		</tr>
		<tr>
			<td>15</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>P</td>
			<td>2095</td>
			<td>0</td>
			<td>2095</td>
			<td>100.000</td>
		</tr>
		<tr>
			<td>16</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>S</td>
			<td>2904451</td>
			<td>2904436</td>
			<td>15</td>
			<td>0.001</td>
		</tr>
		<tr>
			<td>17</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>Z</td>
			<td>515</td>
			<td>515</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>18</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>F</td>
			<td>131894</td>
			<td>131894</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>19</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>N</td>
			<td>136</td>
			<td>0</td>
			<td>136</td>
			<td>100.000</td>
		</tr>
		<tr>
			<td>20</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>S</td>
			<td>81461</td>
			<td>81461</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>21</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>Z</td>
			<td>18</td>
			<td>18</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>22</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>F</td>
			<td>30</td>
			<td>30</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>23</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>H</td>
			<td>2</td>
			<td>2</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>24</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>S</td>
			<td>27287</td>
			<td>27287</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>25</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>Z</td>
			<td>86</td>
			<td>86</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8103-941c-00b206b1b1e6"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 11 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**Status/결측 교차 분석**

표의 분모는 각 기간·Status의 전체 행입니다. 빈 칸은 해당 집단이 없어 계산할 수 없다는 뜻입니다.

**의미:** 실패 비율과 미처리/처리중 비율은 별개입니다. 성공도 운영상 이상을 포함할 수 있어 정상 정답으로 확정하지 않습니다.

**설계 반영:** F/H는 결과 알림, P/N은 진행 상태 감시, Z는 데이터 없음 결과로 각각 처리합니다.

## 3. 규칙 평가: 격리 / 업무 알림 / 품질 검토를 분리

고정 규칙은 데이터가 추가돼도 식이 바뀌지 않습니다. 입력 설정은 버전으로 관리합니다.
조건을 계산할 수 없는 행은 false 정상 대신 **비교 불가 분모**에 남깁니다.

**실행 근거: 노트북 셀 13**

<details>
<summary>셀 13 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>kind</td>
			<td>period</td>
			<td>rule</td>
			<td>rows</td>
			<td>eligible</td>
			<td>hits</td>
			<td>hit_pct</td>
			<td>not_evaluable</td>
		</tr>
		<tr>
			<td>0</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>missing_sql_key</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0.0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>1</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>duplicate_sql_key</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0.0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>2</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>invalid_status</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0.0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>3</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>missing_status</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0.0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>4</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>missing_start</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0.0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
		</tr>
		<tr>
			<td>79</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>pending_timeout</td>
			<td>27405</td>
			<td>0</td>
			<td>0</td>
			<td>NaN</td>
			<td>27405</td>
		</tr>
		<tr>
			<td>80</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>configured_pattern</td>
			<td>27405</td>
			<td>0</td>
			<td>0</td>
			<td>NaN</td>
			<td>27405</td>
		</tr>
		<tr>
			<td>81</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>response_convention</td>
			<td>27405</td>
			<td>27317</td>
			<td>0</td>
			<td>0.0</td>
			<td>88</td>
		</tr>
		<tr>
			<td>82</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>count_conservation</td>
			<td>27405</td>
			<td>27405</td>
			<td>0</td>
			<td>0.0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>83</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>success_with_error</td>
			<td>27405</td>
			<td>27287</td>
			<td>0</td>
			<td>0.0</td>
			<td>118</td>
		</tr>
	</table>
	노트북 표시 제한에 따른 생략 기호가 있는 표는 출력 범위만 옮겼습니다. 전체 계산 객체는 노트북의 해당 셀에서 확인할 수 있습니다.
</details>

### 3.1 관계 규칙: 거래 성공인데 기대 단계가 모두 실패 / 단계 수 불일치

‘모두 F’는 기대 단계 전체를 실제로 관측한 경우에만 계산합니다. 한 단계만 F이고 나머지 로그가 없으면
모두 실패라고 단정하지 않습니다. Process가 여러 재시도에 걸친 경우도 동일 시도만 비교합니다.

선행 종료 전 시작이나 거래 경계 이탈은 비동기/시계 오차일 수 있으므로 hard quarantine에 포함하지 않습니다.

**실행 근거: 노트북 셀 15**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>process_rows</td>
		<td>orphan_processes</td>
	</tr>
	<tr>
		<td>source_period</td>
		<td></td>
		<td></td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>3473285</td>
		<td>6140</td>
	</tr>
	<tr>
		<td>26년8월15일-26년9월14일</td>
		<td>213509</td>
		<td>2</td>
	</tr>
	<tr>
		<td>26년9월30일-26년10월2일</td>
		<td>27405</td>
		<td>4</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>transactions</td>
		<td>relation_comparable</td>
		<td>count_mismatch</td>
		<td>no_process</td>
		<td>success_all_F</td>
		<td>success_any_F</td>
	</tr>
	<tr>
		<td>source_period</td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>1768054</td>
		<td>1760990</td>
		<td>10438</td>
		<td>8018</td>
		<td>0</td>
		<td>0</td>
	</tr>
	<tr>
		<td>26년8월15일-26년9월14일</td>
		<td>213381</td>
		<td>213381</td>
		<td>29</td>
		<td>27</td>
		<td>1</td>
		<td>1</td>
	</tr>
	<tr>
		<td>26년9월30일-26년10월2일</td>
		<td>27268</td>
		<td>27268</td>
		<td>14</td>
		<td>14</td>
		<td>0</td>
		<td>0</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>source_period</td>
		<td>transactionId</td>
		<td>processHubId</td>
		<td>retryCount</td>
		<td>status</td>
		<td>processCount</td>
		<td>observed_processes</td>
		<td>process_failed</td>
	</tr>
	<tr>
		<td>1768073</td>
		<td>26년8월15일-26년9월14일</td>
		<td>TR-A_TESTQWEQ-20260908093831224-01</td>
		<td>HUB</td>
		<td>0.0</td>
		<td>S</td>
		<td>1.0</td>
		<td>1.0</td>
		<td>1.0</td>
	</tr>
</table>

**안전한 관계 검토**

전체 기대 단계가 관측됐는데 Tx S/모든 Process F인 후보: 1건.

**의미:** 이전 many-to-one 분석과 달리 허브·재시도까지 맞춘 연결입니다. 결과가 다르면 키 정의 차이부터 확인합니다.

**설계 반영:** 관계 불일치는 검토/학습 제외로 유지합니다. 운영 전파 계약과 수집 완전성이 확인된 뒤 차단 수준을 승인합니다.

### 3.2 설정 없는 정책은 비활성, 원본은 삭제하지 않음

공통 default 정책: F/H 알림만 활성. 금지 업무·패턴·허용 시간대·P/N 타임아웃은 사용자 설정 전에는 비활성입니다.
`reference_eligible`은 성공·완료·규칙 비위반의 보수적인 학습 후보입니다. 확정 정상 라벨은 아니며 다른 학습 정책과 비교해야 합니다.

**실행 근거: 노트북 셀 17**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>kind</td>
		<td>period</td>
		<td>rows</td>
		<td>quarantine</td>
		<td>business_alert</td>
		<td>quality_review</td>
		<td>reference_eligible</td>
	</tr>
	<tr>
		<td>0</td>
		<td>Transaction</td>
		<td>① 초기</td>
		<td>1768054</td>
		<td>0</td>
		<td>306230</td>
		<td>12892</td>
		<td>1454517</td>
	</tr>
	<tr>
		<td>1</td>
		<td>Transaction</td>
		<td>② 중간</td>
		<td>213381</td>
		<td>0</td>
		<td>132037</td>
		<td>30</td>
		<td>81342</td>
	</tr>
	<tr>
		<td>2</td>
		<td>Transaction</td>
		<td>③ 최근</td>
		<td>27268</td>
		<td>0</td>
		<td>45</td>
		<td>14</td>
		<td>27180</td>
	</tr>
	<tr>
		<td>3</td>
		<td>Process</td>
		<td>① 초기</td>
		<td>3473285</td>
		<td>0</td>
		<td>896</td>
		<td>370</td>
		<td>2904425</td>
	</tr>
	<tr>
		<td>4</td>
		<td>Process</td>
		<td>② 중간</td>
		<td>213509</td>
		<td>0</td>
		<td>131894</td>
		<td>16</td>
		<td>81461</td>
	</tr>
	<tr>
		<td>5</td>
		<td>Process</td>
		<td>③ 최근</td>
		<td>27405</td>
		<td>0</td>
		<td>32</td>
		<td>0</td>
		<td>27287</td>
	</tr>
</table>

<image src="file-upload://3f125113-b9ae-810d-9814-00b2dd1049ca"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 17 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**규칙 이후 경로**

격리·알림·검토는 서로 겹칠 수 있으므로 위 합계를 전체 이상 수로 더하지 않습니다.

**의미:** F/H 필터링은 정상 기준 학습의 정책이며 실패 데이터를 원본에서 삭제하는 과정이 아닙니다.

**설계 반영:** 학습 후보에서 제외된 로그도 별도 경보와 사후 평가용으로 보존합니다. S에서 발생하는 행동 이상도 모델이 검토합니다.

**실행 근거: 노트북 셀 18**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>kind</td>
		<td>period</td>
		<td>rule</td>
		<td>eligible</td>
		<td>hits</td>
		<td>hit_pct</td>
	</tr>
	<tr>
		<td>9</td>
		<td>Transaction</td>
		<td>25년12월24일-26년4월6일</td>
		<td>pending_timeout</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>10</td>
		<td>Transaction</td>
		<td>25년12월24일-26년4월6일</td>
		<td>blocked_interface</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>11</td>
		<td>Transaction</td>
		<td>25년12월24일-26년4월6일</td>
		<td>outside_allowed_hours</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>12</td>
		<td>Transaction</td>
		<td>25년12월24일-26년4월6일</td>
		<td>configured_pattern</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>23</td>
		<td>Transaction</td>
		<td>26년8월15일-26년9월14일</td>
		<td>pending_timeout</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>24</td>
		<td>Transaction</td>
		<td>26년8월15일-26년9월14일</td>
		<td>blocked_interface</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>25</td>
		<td>Transaction</td>
		<td>26년8월15일-26년9월14일</td>
		<td>outside_allowed_hours</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>26</td>
		<td>Transaction</td>
		<td>26년8월15일-26년9월14일</td>
		<td>configured_pattern</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>37</td>
		<td>Transaction</td>
		<td>26년9월30일-26년10월2일</td>
		<td>pending_timeout</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>38</td>
		<td>Transaction</td>
		<td>26년9월30일-26년10월2일</td>
		<td>blocked_interface</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>39</td>
		<td>Transaction</td>
		<td>26년9월30일-26년10월2일</td>
		<td>outside_allowed_hours</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>40</td>
		<td>Transaction</td>
		<td>26년9월30일-26년10월2일</td>
		<td>configured_pattern</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>51</td>
		<td>Process</td>
		<td>25년12월24일-26년4월6일</td>
		<td>pending_timeout</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>52</td>
		<td>Process</td>
		<td>25년12월24일-26년4월6일</td>
		<td>configured_pattern</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>65</td>
		<td>Process</td>
		<td>26년8월15일-26년9월14일</td>
		<td>pending_timeout</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>66</td>
		<td>Process</td>
		<td>26년8월15일-26년9월14일</td>
		<td>configured_pattern</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>79</td>
		<td>Process</td>
		<td>26년9월30일-26년10월2일</td>
		<td>pending_timeout</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
	<tr>
		<td>80</td>
		<td>Process</td>
		<td>26년9월30일-26년10월2일</td>
		<td>configured_pattern</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
	</tr>
</table>

<image src="file-upload://3f125113-b9ae-817b-a224-00b26b0a4418"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 18 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

<image src="file-upload://3f125113-b9ae-8137-9030-00b2c97ab0b1"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 18 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**비활성 정책**

분모 eligible=0이면 비율은 NaN입니다. 후보 0/비교 가능 0은 정상 확인이 아닙니다.

**의미:** 운영자가 금지 조건/SLA를 제공하지 않은 상태에서 임의로 장애를 만들지 않습니다.

**설계 반영:** 활성 정책과 설정 버전, 평가 시각을 함께 기록합니다.

### 3.3 특정 업무·패턴·시간대 정책을 정하기 위한 후보 분석

Transaction은 업무+Status+응답코드, Process는 어댑터+채널+Status+응답코드 조합의 실제 지원량을 봅니다.
상위 조합이라는 이유로 금지하지는 않습니다. ‘어떤 조건을 운영 정책으로 입력할지’ 결정할 근거입니다.
시간대별 F 비율은 30행 이상일 때만 그리며 관측된 시작 로그 안의 비율입니다.

**실행 근거: 노트북 셀 20**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>source_period</td>
		<td>interfaceId</td>
		<td>status</td>
		<td>responseCode</td>
		<td>rows</td>
	</tr>
	<tr>
		<td>1307</td>
		<td>26년8월15일-26년9월14일</td>
		<td>IF_REST_ERROR_RESPONSE</td>
		<td>F</td>
		<td>4311</td>
		<td>78950</td>
	</tr>
	<tr>
		<td>1272</td>
		<td>26년8월15일-26년9월14일</td>
		<td>IF_CLAUDE_CREATE_001</td>
		<td>F</td>
		<td>4311</td>
		<td>37864</td>
	</tr>
	<tr>
		<td>93</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_AP2TCP_000</td>
		<td>F</td>
		<td>4801</td>
		<td>26057</td>
	</tr>
	<tr>
		<td>14</td>
		<td>25년12월24일-26년4월6일</td>
		<td>HUB_DB2API_001</td>
		<td>F</td>
		<td>4801</td>
		<td>21660</td>
	</tr>
	<tr>
		<td>103</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_AP2TCP_002</td>
		<td>F</td>
		<td>4801</td>
		<td>14466</td>
	</tr>
	<tr>
		<td>100</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_AP2TCP_001</td>
		<td>F</td>
		<td>4801</td>
		<td>13631</td>
	</tr>
	<tr>
		<td>1232</td>
		<td>25년12월24일-26년4월6일</td>
		<td>SVC_EXEC_TEST1</td>
		<td>F</td>
		<td>4801</td>
		<td>8536</td>
	</tr>
	<tr>
		<td>1278</td>
		<td>26년8월15일-26년9월14일</td>
		<td>IF_CLAUDE_DELETE_001</td>
		<td>F</td>
		<td>4311</td>
		<td>7737</td>
	</tr>
	<tr>
		<td>1275</td>
		<td>26년8월15일-26년9월14일</td>
		<td>IF_CLAUDE_CREATE_001</td>
		<td>F</td>
		<td>4999</td>
		<td>3692</td>
	</tr>
	<tr>
		<td>1309</td>
		<td>26년8월15일-26년9월14일</td>
		<td>IF_REST_ERROR_RESPONSE</td>
		<td>F</td>
		<td>4999</td>
		<td>3577</td>
	</tr>
	<tr>
		<td>1204</td>
		<td>25년12월24일-26년4월6일</td>
		<td>LOG_TEST_DEBUG</td>
		<td>F</td>
		<td>4801</td>
		<td>3563</td>
	</tr>
	<tr>
		<td>1211</td>
		<td>25년12월24일-26년4월6일</td>
		<td>LOG_TEST_INFO</td>
		<td>F</td>
		<td>4801</td>
		<td>3536</td>
	</tr>
	<tr>
		<td>1180</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_SUB2DB_102</td>
		<td>F</td>
		<td>4801</td>
		<td>3194</td>
	</tr>
	<tr>
		<td>1175</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_SUB2DB_101</td>
		<td>F</td>
		<td>4801</td>
		<td>3194</td>
	</tr>
	<tr>
		<td>1185</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_SUB2DB_103</td>
		<td>F</td>
		<td>4801</td>
		<td>3178</td>
	</tr>
	<tr>
		<td>1191</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_SUB2DB_104</td>
		<td>F</td>
		<td>4801</td>
		<td>3149</td>
	</tr>
	<tr>
		<td>533</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_952</td>
		<td>F</td>
		<td>4801</td>
		<td>3098</td>
	</tr>
	<tr>
		<td>527</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_951</td>
		<td>F</td>
		<td>4801</td>
		<td>3049</td>
	</tr>
	<tr>
		<td>467</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_940</td>
		<td>F</td>
		<td>4801</td>
		<td>2900</td>
	</tr>
	<tr>
		<td>540</td>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_953</td>
		<td>F</td>
		<td>4801</td>
		<td>2878</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>source_period</td>
		<td>adapterType</td>
		<td>channelId</td>
		<td>status</td>
		<td>responseCode</td>
		<td>rows</td>
	</tr>
	<tr>
		<td>232</td>
		<td>26년8월15일-26년9월14일</td>
		<td>SERVICE.EXECUTOR</td>
		<td>HUB</td>
		<td>F</td>
		<td>4311</td>
		<td>124553</td>
	</tr>
	<tr>
		<td>233</td>
		<td>26년8월15일-26년9월14일</td>
		<td>SERVICE.EXECUTOR</td>
		<td>HUB</td>
		<td>F</td>
		<td>4999</td>
		<td>7270</td>
	</tr>
	<tr>
		<td>55</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.READER</td>
		<td>TSC</td>
		<td>F</td>
		<td>4999</td>
		<td>235</td>
	</tr>
	<tr>
		<td>76</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.WRITER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4999</td>
		<td>108</td>
	</tr>
	<tr>
		<td>74</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.WRITER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4102</td>
		<td>83</td>
	</tr>
	<tr>
		<td>112</td>
		<td>25년12월24일-26년4월6일</td>
		<td>FILE.READER</td>
		<td>TSC</td>
		<td>F</td>
		<td>4201</td>
		<td>50</td>
	</tr>
	<tr>
		<td>89</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.WRITER</td>
		<td>TSC</td>
		<td>F</td>
		<td>9999</td>
		<td>41</td>
	</tr>
	<tr>
		<td>251</td>
		<td>26년9월30일-26년10월2일</td>
		<td>DB.WRITER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4121</td>
		<td>27</td>
	</tr>
	<tr>
		<td>77</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.WRITER</td>
		<td>SOL</td>
		<td>F</td>
		<td>9999</td>
		<td>22</td>
	</tr>
	<tr>
		<td>114</td>
		<td>25년12월24일-26년4월6일</td>
		<td>FILE.READER</td>
		<td>TSC</td>
		<td>F</td>
		<td>4999</td>
		<td>20</td>
	</tr>
	<tr>
		<td>145</td>
		<td>25년12월24일-26년4월6일</td>
		<td>GATEWAY.INVOKER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4701</td>
		<td>19</td>
	</tr>
	<tr>
		<td>70</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.UPDATER</td>
		<td>TSC</td>
		<td>H</td>
		<td>0000</td>
		<td>18</td>
	</tr>
	<tr>
		<td>168</td>
		<td>25년12월24일-26년4월6일</td>
		<td>SERVICE.EXECUTOR</td>
		<td>HUB</td>
		<td>F</td>
		<td>4999</td>
		<td>17</td>
	</tr>
	<tr>
		<td>75</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.WRITER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4903</td>
		<td>16</td>
	</tr>
	<tr>
		<td>177</td>
		<td>25년12월24일-26년4월6일</td>
		<td>SERVICE.EXECUTOR</td>
		<td>TSC</td>
		<td>F</td>
		<td>4301</td>
		<td>15</td>
	</tr>
	<tr>
		<td>103</td>
		<td>25년12월24일-26년4월6일</td>
		<td>FILE.READER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4201</td>
		<td>14</td>
	</tr>
	<tr>
		<td>181</td>
		<td>25년12월24일-26년4월6일</td>
		<td>SERVICE.INVOKER</td>
		<td>HUB</td>
		<td>F</td>
		<td>4302</td>
		<td>13</td>
	</tr>
	<tr>
		<td>209</td>
		<td>26년8월15일-26년9월14일</td>
		<td>DB.WRITER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4121</td>
		<td>13</td>
	</tr>
	<tr>
		<td>64</td>
		<td>25년12월24일-26년4월6일</td>
		<td>DB.UPDATER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4999</td>
		<td>13</td>
	</tr>
	<tr>
		<td>146</td>
		<td>25년12월24일-26년4월6일</td>
		<td>GATEWAY.INVOKER</td>
		<td>SOL</td>
		<td>F</td>
		<td>4999</td>
		<td>11</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>source_period</td>
		<td>hour</td>
		<td>rows</td>
		<td>failures</td>
		<td>failure_pct</td>
		<td>kind</td>
	</tr>
	<tr>
		<td>33</td>
		<td>26년8월15일-26년9월14일</td>
		<td>9</td>
		<td>8676</td>
		<td>5578</td>
		<td>64.292</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>87</td>
		<td>26년8월15일-26년9월14일</td>
		<td>9</td>
		<td>8673</td>
		<td>5564</td>
		<td>64.153</td>
		<td>Process</td>
	</tr>
	<tr>
		<td>34</td>
		<td>26년8월15일-26년9월14일</td>
		<td>10</td>
		<td>9143</td>
		<td>5844</td>
		<td>63.918</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>88</td>
		<td>26년8월15일-26년9월14일</td>
		<td>10</td>
		<td>9160</td>
		<td>5845</td>
		<td>63.810</td>
		<td>Process</td>
	</tr>
	<tr>
		<td>35</td>
		<td>26년8월15일-26년9월14일</td>
		<td>11</td>
		<td>9300</td>
		<td>5899</td>
		<td>63.430</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>89</td>
		<td>26년8월15일-26년9월14일</td>
		<td>11</td>
		<td>9317</td>
		<td>5881</td>
		<td>63.121</td>
		<td>Process</td>
	</tr>
	<tr>
		<td>36</td>
		<td>26년8월15일-26년9월14일</td>
		<td>12</td>
		<td>9358</td>
		<td>5902</td>
		<td>63.069</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>90</td>
		<td>26년8월15일-26년9월14일</td>
		<td>12</td>
		<td>9357</td>
		<td>5901</td>
		<td>63.065</td>
		<td>Process</td>
	</tr>
	<tr>
		<td>101</td>
		<td>26년8월15일-26년9월14일</td>
		<td>23</td>
		<td>8280</td>
		<td>5208</td>
		<td>62.899</td>
		<td>Process</td>
	</tr>
	<tr>
		<td>47</td>
		<td>26년8월15일-26년9월14일</td>
		<td>23</td>
		<td>8280</td>
		<td>5208</td>
		<td>62.899</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>43</td>
		<td>26년8월15일-26년9월14일</td>
		<td>19</td>
		<td>8281</td>
		<td>5207</td>
		<td>62.879</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>37</td>
		<td>26년8월15일-26년9월14일</td>
		<td>13</td>
		<td>9257</td>
		<td>5820</td>
		<td>62.871</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>97</td>
		<td>26년8월15일-26년9월14일</td>
		<td>19</td>
		<td>8283</td>
		<td>5207</td>
		<td>62.864</td>
		<td>Process</td>
	</tr>
	<tr>
		<td>44</td>
		<td>26년8월15일-26년9월14일</td>
		<td>20</td>
		<td>8280</td>
		<td>5200</td>
		<td>62.802</td>
		<td>Transaction</td>
	</tr>
	<tr>
		<td>98</td>
		<td>26년8월15일-26년9월14일</td>
		<td>20</td>
		<td>8280</td>
		<td>5200</td>
		<td>62.802</td>
		<td>Process</td>
	</tr>
</table>

<image src="file-upload://3f125113-b9ae-811a-aa69-00b2456db3da"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 20 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**정책 도출의 근거**

상위 실패/부분성공 조합과 관측 시간대별 F 비율을 비교했습니다. 회색 칸은 로그 없음/지원 부족입니다.

**의미:** 실패가 반복되는 조합은 결과 알림을 좁히는 후보입니다. 야간·특정 업무·높은 실패율을 자동 금지하거나 전체 시간대의 장애 확률로 해석하지 않습니다.

**설계 반영:** 패턴·업무·허용 시간대 하드 정책은 실제 운영 계약을 입력해야 활성화됩니다. default는 비활성입니다.

## 4. Status에 따른 처리시간 차이

완료 지연을 계산할 수 있는 행만 비교합니다. S/F 이외 상태도 표에는 보존하며,
소수 집단의 p99와 단일 극단값은 충분한 데이터 없이 안정적인 정상 범위가 아닙니다.

**실행 근거: 노트북 셀 22**

<details>
<summary>셀 22 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>kind</td>
			<td>source_period</td>
			<td>status</td>
			<td>rows</td>
			<td>valid_duration</td>
			<td>p50_ms</td>
			<td>p95_ms</td>
			<td>p99_ms</td>
		</tr>
		<tr>
			<td>0</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>F</td>
			<td>306172</td>
			<td>306172</td>
			<td>4.0</td>
			<td>29.00</td>
			<td>1421.00</td>
		</tr>
		<tr>
			<td>1</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>H</td>
			<td>58</td>
			<td>58</td>
			<td>426.0</td>
			<td>18890.75</td>
			<td>49851.01</td>
		</tr>
		<tr>
			<td>2</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>P</td>
			<td>7061</td>
			<td>1</td>
			<td>11.0</td>
			<td>11.00</td>
			<td>11.00</td>
		</tr>
		<tr>
			<td>3</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>S</td>
			<td>1454528</td>
			<td>1454528</td>
			<td>6329.0</td>
			<td>9777.00</td>
			<td>11129.00</td>
		</tr>
		<tr>
			<td>4</td>
			<td>Transaction</td>
			<td>25년12월24일-26년4월6일</td>
			<td>Z</td>
			<td>235</td>
			<td>235</td>
			<td>89.0</td>
			<td>245.00</td>
			<td>527.48</td>
		</tr>
		<tr>
			<td>5</td>
			<td>Transaction</td>
			<td>26년8월15일-26년9월14일</td>
			<td>F</td>
			<td>132036</td>
			<td>132036</td>
			<td>51.0</td>
			<td>77.00</td>
			<td>133.00</td>
		</tr>
		<tr>
			<td>6</td>
			<td>Transaction</td>
			<td>26년8월15일-26년9월14일</td>
			<td>H</td>
			<td>1</td>
			<td>1</td>
			<td>9721.0</td>
			<td>9721.00</td>
			<td>9721.00</td>
		</tr>
		<tr>
			<td>7</td>
			<td>Transaction</td>
			<td>26년8월15일-26년9월14일</td>
			<td>S</td>
			<td>81344</td>
			<td>81344</td>
			<td>77.0</td>
			<td>175.00</td>
			<td>1057.57</td>
		</tr>
		<tr>
			<td>8</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>F</td>
			<td>43</td>
			<td>43</td>
			<td>7426.0</td>
			<td>15145.60</td>
			<td>28196.34</td>
		</tr>
		<tr>
			<td>9</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>H</td>
			<td>2</td>
			<td>2</td>
			<td>3142.0</td>
			<td>3314.80</td>
			<td>3330.16</td>
		</tr>
		<tr>
			<td>10</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>S</td>
			<td>27180</td>
			<td>27180</td>
			<td>5984.0</td>
			<td>9937.25</td>
			<td>52081.10</td>
		</tr>
		<tr>
			<td>11</td>
			<td>Transaction</td>
			<td>26년9월30일-26년10월2일</td>
			<td>Z</td>
			<td>43</td>
			<td>43</td>
			<td>1156.0</td>
			<td>1256.40</td>
			<td>1641.48</td>
		</tr>
		<tr>
			<td>12</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>F</td>
			<td>861</td>
			<td>851</td>
			<td>444.0</td>
			<td>125738.00</td>
			<td>1800006.50</td>
		</tr>
		<tr>
			<td>13</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>H</td>
			<td>35</td>
			<td>35</td>
			<td>141.0</td>
			<td>448.60</td>
			<td>1017.40</td>
		</tr>
		<tr>
			<td>14</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>N</td>
			<td>565328</td>
			<td>1</td>
			<td>676.0</td>
			<td>676.00</td>
			<td>676.00</td>
		</tr>
		<tr>
			<td>15</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>P</td>
			<td>2095</td>
			<td>0</td>
			<td>NaN</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>16</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>S</td>
			<td>2904451</td>
			<td>2904436</td>
			<td>993.0</td>
			<td>8761.00</td>
			<td>9678.00</td>
		</tr>
		<tr>
			<td>17</td>
			<td>Process</td>
			<td>25년12월24일-26년4월6일</td>
			<td>Z</td>
			<td>515</td>
			<td>515</td>
			<td>34.0</td>
			<td>131.30</td>
			<td>40321.06</td>
		</tr>
		<tr>
			<td>18</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>F</td>
			<td>131894</td>
			<td>131894</td>
			<td>20.0</td>
			<td>44.00</td>
			<td>59.00</td>
		</tr>
		<tr>
			<td>19</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>N</td>
			<td>136</td>
			<td>0</td>
			<td>NaN</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>20</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>S</td>
			<td>81461</td>
			<td>81461</td>
			<td>45.0</td>
			<td>143.00</td>
			<td>1022.00</td>
		</tr>
		<tr>
			<td>21</td>
			<td>Process</td>
			<td>26년8월15일-26년9월14일</td>
			<td>Z</td>
			<td>18</td>
			<td>18</td>
			<td>217.0</td>
			<td>373.50</td>
			<td>448.30</td>
		</tr>
		<tr>
			<td>22</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>F</td>
			<td>30</td>
			<td>30</td>
			<td>7446.5</td>
			<td>7757.05</td>
			<td>9051.38</td>
		</tr>
		<tr>
			<td>23</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>H</td>
			<td>2</td>
			<td>2</td>
			<td>2954.0</td>
			<td>3080.00</td>
			<td>3091.20</td>
		</tr>
		<tr>
			<td>24</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>S</td>
			<td>27287</td>
			<td>27287</td>
			<td>5969.0</td>
			<td>6417.70</td>
			<td>45631.18</td>
		</tr>
		<tr>
			<td>25</td>
			<td>Process</td>
			<td>26년9월30일-26년10월2일</td>
			<td>Z</td>
			<td>86</td>
			<td>86</td>
			<td>56.0</td>
			<td>1070.50</td>
			<td>1184.25</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-819c-9573-00b26a9e2d5d"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 22 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**상태별 지연**

점은 p50, 붉은 표시는 p95입니다. 표의 valid_duration가 실제 분위수 분모입니다.

**의미:** S/F 차이에 업무 구성·기간 변화가 섞입니다. F의 긴 시간은 장애 원인을 증명하지 않습니다.

**설계 반영:** 같은 업무·기간 안에서 상태를 나눈 다음 지연 차이를 비교합니다.

### 4.1 같은 기간·같은 업무 안에서 S/F 비교

두 상태 모두 유효 지연 30건 이상인 업무만 비교합니다. 이 분석은 사후 설명용이며 학습 입력에 Status를 넣는 과정이 아닙니다.

**실행 근거: 노트북 셀 24**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td></td>
		<td>n</td>
		<td>n</td>
		<td>p50</td>
		<td>p50</td>
		<td>p95</td>
		<td>p95</td>
		<td>derived</td>
	</tr>
	<tr>
		<td></td>
		<td>status</td>
		<td>F</td>
		<td>S</td>
		<td>F</td>
		<td>S</td>
		<td>F</td>
		<td>S</td>
		<td>F/S_p50</td>
	</tr>
	<tr>
		<td>source_period</td>
		<td>interfaceId</td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_908</td>
		<td>1447.0</td>
		<td>47429.0</td>
		<td>5.0</td>
		<td>5411.0</td>
		<td>66.10</td>
		<td>9709.60</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_916</td>
		<td>2107.0</td>
		<td>47306.0</td>
		<td>9.0</td>
		<td>5472.0</td>
		<td>70.70</td>
		<td>9726.00</td>
		<td>0.002</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_906</td>
		<td>2776.0</td>
		<td>47305.0</td>
		<td>9.0</td>
		<td>5421.0</td>
		<td>60.25</td>
		<td>9692.00</td>
		<td>0.002</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_919</td>
		<td>1466.0</td>
		<td>47298.0</td>
		<td>4.0</td>
		<td>5450.0</td>
		<td>58.00</td>
		<td>9807.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_905</td>
		<td>2367.0</td>
		<td>47298.0</td>
		<td>6.0</td>
		<td>5547.0</td>
		<td>49.00</td>
		<td>9821.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_917</td>
		<td>1618.0</td>
		<td>47284.0</td>
		<td>6.0</td>
		<td>5442.0</td>
		<td>76.15</td>
		<td>9754.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_902</td>
		<td>1899.0</td>
		<td>47268.0</td>
		<td>4.0</td>
		<td>5503.0</td>
		<td>29.00</td>
		<td>9679.65</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_907</td>
		<td>2196.0</td>
		<td>47252.0</td>
		<td>6.0</td>
		<td>5457.0</td>
		<td>44.25</td>
		<td>9743.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_904</td>
		<td>1656.0</td>
		<td>47230.0</td>
		<td>4.0</td>
		<td>5517.0</td>
		<td>50.00</td>
		<td>9766.55</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_910</td>
		<td>1326.0</td>
		<td>47228.0</td>
		<td>4.0</td>
		<td>5389.0</td>
		<td>99.75</td>
		<td>9756.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_909</td>
		<td>1514.0</td>
		<td>47138.0</td>
		<td>4.0</td>
		<td>5427.0</td>
		<td>58.80</td>
		<td>9750.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_914</td>
		<td>1833.0</td>
		<td>47101.0</td>
		<td>4.0</td>
		<td>5558.0</td>
		<td>30.00</td>
		<td>9776.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_918</td>
		<td>1407.0</td>
		<td>47081.0</td>
		<td>5.0</td>
		<td>5476.0</td>
		<td>84.40</td>
		<td>9738.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_901</td>
		<td>1743.0</td>
		<td>47021.0</td>
		<td>4.0</td>
		<td>5464.0</td>
		<td>31.00</td>
		<td>9695.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_915</td>
		<td>2433.0</td>
		<td>47001.0</td>
		<td>5.0</td>
		<td>5536.0</td>
		<td>45.00</td>
		<td>9822.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_912</td>
		<td>1584.0</td>
		<td>46950.0</td>
		<td>4.0</td>
		<td>5601.5</td>
		<td>29.00</td>
		<td>9753.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_911</td>
		<td>1646.0</td>
		<td>46884.0</td>
		<td>4.0</td>
		<td>5555.0</td>
		<td>34.50</td>
		<td>9762.55</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_903</td>
		<td>1060.0</td>
		<td>46860.0</td>
		<td>4.0</td>
		<td>5550.5</td>
		<td>342.60</td>
		<td>9712.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>25년12월24일-26년4월6일</td>
		<td>IF_DB2DB_913</td>
		<td>1111.0</td>
		<td>46502.0</td>
		<td>4.0</td>
		<td>5590.0</td>
		<td>172.50</td>
		<td>9744.00</td>
		<td>0.001</td>
	</tr>
	<tr>
		<td>26년8월15일-26년9월14일</td>
		<td>IF_CLAUDE_CREATE_001</td>
		<td>41625.0</td>
		<td>42741.0</td>
		<td>61.0</td>
		<td>75.0</td>
		<td>81.00</td>
		<td>169.00</td>
		<td>0.813</td>
	</tr>
</table>

<image src="file-upload://3f125113-b9ae-811c-9591-00b2acc526be"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 24 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**업무 안의 S/F 비교**

양쪽 유효 지연 30건 이상인 기간·업무 쌍: 47개.

**의미:** 점선 위는 해당 업무의 F 중앙값이 S보다 긴 경우, 아래는 실패가 더 빨리 끝나는 경우입니다.

**설계 반영:** 전체 Status 차이를 업무 차이로 오해하지 않도록 이 비교를 같이 봅니다. 요청 크기/부하까지 통제한 인과 분석은 아닙니다.

## 5. 데이터가 추가되어도 움직이지 않는 극단값 기준선

**상위 1%/0.1%는 데이터 불변 규칙이 아닙니다.** 학습 S 로그에서 p99/p99.9를 계산해 저장된
기준선 버전으로 고정하면, 그 버전을 적용하는 동안만 임계값이 고정됩니다.

현재 EDA는 관측 날짜 기준 과거 학습/검증/시험 분할을 재사용합니다. 기준선에는 검증/시험을 넣지 않습니다.
같은 거래가 경계를 넘으면 제외합니다. future leakage 없이 새 업무의 기준선을 만들 수 없는 한계를 드러냅니다.

**실행 근거: 노트북 셀 26**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>kind</td>
		<td>split</td>
		<td>rows</td>
		<td>reference_S</td>
		<td>first</td>
		<td>last</td>
	</tr>
	<tr>
		<td>0</td>
		<td>Transaction</td>
		<td>test</td>
		<td>111303</td>
		<td>36765</td>
		<td>2026-08-20 00:00:00.284</td>
		<td>2026-10-02 15:36:59.444</td>
	</tr>
	<tr>
		<td>1</td>
		<td>Transaction</td>
		<td>train</td>
		<td>1616874</td>
		<td>1304416</td>
		<td>2025-12-24 10:25:31.632</td>
		<td>2026-03-20 17:04:21.488</td>
	</tr>
	<tr>
		<td>2</td>
		<td>Transaction</td>
		<td>validation</td>
		<td>280526</td>
		<td>221858</td>
		<td>2026-03-23 08:12:59.258</td>
		<td>2026-08-19 23:59:55.035</td>
	</tr>
	<tr>
		<td>3</td>
		<td>Process</td>
		<td>excluded_boundary_transaction</td>
		<td>4</td>
		<td>3</td>
		<td>2026-01-12 10:28:56.989</td>
		<td>2026-04-02 11:14:00.511</td>
	</tr>
	<tr>
		<td>4</td>
		<td>Process</td>
		<td>excluded_missing_time_or_id</td>
		<td>325</td>
		<td>0</td>
		<td>NaT</td>
		<td>NaT</td>
	</tr>
	<tr>
		<td>5</td>
		<td>Process</td>
		<td>test</td>
		<td>111549</td>
		<td>36987</td>
		<td>2026-08-20 00:00:00.295</td>
		<td>2026-10-02 15:36:59.559</td>
	</tr>
	<tr>
		<td>6</td>
		<td>Process</td>
		<td>train</td>
		<td>3175652</td>
		<td>2608471</td>
		<td>2025-12-24 10:25:31.689</td>
		<td>2026-03-20 17:04:27.315</td>
	</tr>
	<tr>
		<td>7</td>
		<td>Process</td>
		<td>validation</td>
		<td>426669</td>
		<td>367712</td>
		<td>2026-03-23 08:12:59.703</td>
		<td>2026-08-19 23:59:55.042</td>
	</tr>
</table>

### 5.1 구체 업무 → 공통 범주/어댑터 → 통합으로 기준선 fallback

Transaction: interfaceId → interfaceType+시작/종료 채널 → 통합.
Process: processId → adapterType+channelId → 통합.
그룹 기준선에 지원량이 없으면 상위 기준으로 넘어가며 상위에서도 없으면 비교 불가로 남깁니다.
통합 기준은 서로 다른 업무가 섞인 약한 fallback이므로 SLA 대신 확정 차단 기준으로 쓰지 않습니다.

**실행 근거: 노트북 셀 28**

<details>
<summary>셀 28 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>n</td>
			<td>median</td>
			<td>q0.99</td>
			<td>q0.999</td>
		</tr>
		<tr>
			<td>group</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>["IF_DB2DB_908"]</td>
			<td>39908</td>
			<td>6159.5</td>
			<td>13108.09</td>
			<td>257584.609</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_902"]</td>
			<td>39872</td>
			<td>6206.0</td>
			<td>12799.51</td>
			<td>233696.112</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_901"]</td>
			<td>39785</td>
			<td>6189.0</td>
			<td>12805.92</td>
			<td>240371.048</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_916"]</td>
			<td>39759</td>
			<td>6218.0</td>
			<td>13544.20</td>
			<td>249266.504</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_906"]</td>
			<td>39756</td>
			<td>6183.0</td>
			<td>13116.85</td>
			<td>249403.605</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_911"]</td>
			<td>39652</td>
			<td>6282.0</td>
			<td>13914.30</td>
			<td>233115.988</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_919"]</td>
			<td>39596</td>
			<td>6253.0</td>
			<td>14084.35</td>
			<td>252217.815</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_905"]</td>
			<td>39591</td>
			<td>6310.0</td>
			<td>13746.70</td>
			<td>236918.410</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_917"]</td>
			<td>39586</td>
			<td>6213.0</td>
			<td>13747.95</td>
			<td>244017.870</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_912"]</td>
			<td>39563</td>
			<td>6321.0</td>
			<td>13626.58</td>
			<td>238603.622</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_918"]</td>
			<td>39554</td>
			<td>6233.0</td>
			<td>13481.17</td>
			<td>237752.993</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_907"]</td>
			<td>39550</td>
			<td>6217.0</td>
			<td>13299.28</td>
			<td>253653.428</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_904"]</td>
			<td>39529</td>
			<td>6262.0</td>
			<td>13109.00</td>
			<td>236050.584</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_910"]</td>
			<td>39523</td>
			<td>6198.0</td>
			<td>13075.04</td>
			<td>230060.242</td>
		</tr>
		<tr>
			<td>["IF_DB2DB_903"]</td>
			<td>39517</td>
			<td>6269.0</td>
			<td>13230.96</td>
			<td>237129.584</td>
		</tr>
	</table>
</details>

<details>
<summary>셀 28 · 근거 표 2 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>n</td>
			<td>median</td>
			<td>q0.99</td>
			<td>q0.999</td>
		</tr>
		<tr>
			<td>group</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>["DB.READER", "TSC"]</td>
			<td>1279522</td>
			<td>438.0</td>
			<td>1127.00</td>
			<td>2384.479</td>
		</tr>
		<tr>
			<td>["DB.WRITER", "SOL"]</td>
			<td>1279433</td>
			<td>6240.0</td>
			<td>10402.00</td>
			<td>20644.112</td>
		</tr>
		<tr>
			<td>["FILE.READER", "TSC"]</td>
			<td>24386</td>
			<td>454.0</td>
			<td>24201.60</td>
			<td>27285.400</td>
		</tr>
		<tr>
			<td>["FILE.WRITER", "SOL"]</td>
			<td>22341</td>
			<td>906.0</td>
			<td>1534.20</td>
			<td>2137.900</td>
		</tr>
		<tr>
			<td>["SERVICE.EXECUTOR", "HUB"]</td>
			<td>2595</td>
			<td>7038.0</td>
			<td>7107.54</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["SERVICE.EXECUTOR", "SOL"]</td>
			<td>45</td>
			<td>105.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["DB.READER", "SOL"]</td>
			<td>32</td>
			<td>214.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["FILE.READER", "SOL"]</td>
			<td>17</td>
			<td>4695.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["FILE.WRITER", "TSC"]</td>
			<td>17</td>
			<td>6854.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["DATA.TRANSFORMER", "TSC"]</td>
			<td>11</td>
			<td>161.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["DB.WRITER", "SSCH"]</td>
			<td>9</td>
			<td>853.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["SERVICE.EXECUTOR", "TSC"]</td>
			<td>8</td>
			<td>167.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["QUEUE.EXECUTOR", "TSC"]</td>
			<td>8</td>
			<td>769.5</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["CLIENT_STUB", "HUB"]</td>
			<td>7</td>
			<td>6265.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
		<tr>
			<td>["DB.UPDATER", "SOL"]</td>
			<td>7</td>
			<td>161.0</td>
			<td>NaN</td>
			<td>NaN</td>
		</tr>
	</table>
</details>

<details>
<summary>셀 28 · 근거 표 3 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>n</td>
			<td>median</td>
			<td>q0.99</td>
			<td>q0.999</td>
			<td>kind</td>
		</tr>
		<tr>
			<td>group</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>GLOBAL</td>
			<td>1304416</td>
			<td>6714.0</td>
			<td>11337.0</td>
			<td>152269.87</td>
			<td>Transaction</td>
		</tr>
		<tr>
			<td>GLOBAL</td>
			<td>2608471</td>
			<td>1029.0</td>
			<td>9728.0</td>
			<td>18388.12</td>
			<td>Process</td>
		</tr>
	</table>
</details>

**실행 근거: 노트북 셀 29**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>kind</td>
		<td>q</td>
		<td>period</td>
		<td>test_rows</td>
		<td>comparable</td>
		<td>candidates</td>
		<td>candidate_pct</td>
		<td>business_reference</td>
		<td>common_reference</td>
		<td>global_reference</td>
		<td>no_reference</td>
	</tr>
	<tr>
		<td>0</td>
		<td>Transaction</td>
		<td>0.990</td>
		<td>② 중간</td>
		<td>84035</td>
		<td>84035</td>
		<td>34</td>
		<td>0.0405</td>
		<td>0</td>
		<td>83996</td>
		<td>39</td>
		<td>0</td>
	</tr>
	<tr>
		<td>1</td>
		<td>Transaction</td>
		<td>0.990</td>
		<td>③ 최근</td>
		<td>27268</td>
		<td>27268</td>
		<td>1182</td>
		<td>4.3348</td>
		<td>0</td>
		<td>36</td>
		<td>27232</td>
		<td>0</td>
	</tr>
	<tr>
		<td>2</td>
		<td>Transaction</td>
		<td>0.999</td>
		<td>② 중간</td>
		<td>84035</td>
		<td>84035</td>
		<td>0</td>
		<td>0.0000</td>
		<td>0</td>
		<td>58</td>
		<td>83977</td>
		<td>0</td>
	</tr>
	<tr>
		<td>3</td>
		<td>Transaction</td>
		<td>0.999</td>
		<td>③ 최근</td>
		<td>27268</td>
		<td>27268</td>
		<td>0</td>
		<td>0.0000</td>
		<td>0</td>
		<td>36</td>
		<td>27232</td>
		<td>0</td>
	</tr>
	<tr>
		<td>4</td>
		<td>Process</td>
		<td>0.990</td>
		<td>② 중간</td>
		<td>84144</td>
		<td>84064</td>
		<td>40</td>
		<td>0.0476</td>
		<td>0</td>
		<td>84030</td>
		<td>114</td>
		<td>0</td>
	</tr>
	<tr>
		<td>5</td>
		<td>Process</td>
		<td>0.990</td>
		<td>③ 최근</td>
		<td>27405</td>
		<td>27405</td>
		<td>1002</td>
		<td>3.6563</td>
		<td>0</td>
		<td>126</td>
		<td>27279</td>
		<td>0</td>
	</tr>
	<tr>
		<td>6</td>
		<td>Process</td>
		<td>0.999</td>
		<td>② 중간</td>
		<td>84144</td>
		<td>84064</td>
		<td>39</td>
		<td>0.0464</td>
		<td>0</td>
		<td>120</td>
		<td>84024</td>
		<td>0</td>
	</tr>
	<tr>
		<td>7</td>
		<td>Process</td>
		<td>0.999</td>
		<td>③ 최근</td>
		<td>27405</td>
		<td>27405</td>
		<td>356</td>
		<td>1.2990</td>
		<td>0</td>
		<td>126</td>
		<td>27279</td>
		<td>0</td>
	</tr>
</table>

<image src="file-upload://3f125113-b9ae-8129-a24c-00b258f59c37"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 29 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**고정 분위수 시험 결과**

후보는 threshold를 엄격히 초과한 실행입니다. 동률·분포 변화 때문에 시험 후보율은 1%/0.1%와 같지 않습니다.

**의미:** 새 업무가 초기 그룹에 없으면 대부분 공통/통합 기준선으로 떨어질 수 있습니다. 그것은 콜드스타트가 해결됐다는 뜻이 아닙니다.

**설계 반영:** 이 기준은 통계 후보/우선 검토용으로 사용합니다. 중요 업무의 하드 타임아웃은 승인된 SLA로 별도 설정합니다.

### 5.2 p99.9 지원량 민감도와 기본값 선택

최소 10,000행은 0.1% 꼬리에 약 10개 관측이 대응하는 시작 기준일 뿐, 독립성·안정성을 보장하지 않습니다.
아래 비교는 지원량을 1,000/5,000/10,000으로 바꿀 때 기준선이 가능한 그룹 수가 어떻게 달라지는지 보여줍니다.

**실행 근거: 노트북 셀 31**

<details>
<summary>셀 31 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>kind</td>
			<td>level</td>
			<td>min_support</td>
			<td>supported_groups</td>
			<td>groups</td>
		</tr>
		<tr>
			<td>0</td>
			<td>Transaction</td>
			<td>업무</td>
			<td>1000</td>
			<td>42</td>
			<td>99</td>
		</tr>
		<tr>
			<td>1</td>
			<td>Transaction</td>
			<td>업무</td>
			<td>5000</td>
			<td>41</td>
			<td>99</td>
		</tr>
		<tr>
			<td>2</td>
			<td>Transaction</td>
			<td>업무</td>
			<td>10000</td>
			<td>41</td>
			<td>99</td>
		</tr>
		<tr>
			<td>3</td>
			<td>Transaction</td>
			<td>공통경로</td>
			<td>1000</td>
			<td>2</td>
			<td>12</td>
		</tr>
		<tr>
			<td>4</td>
			<td>Transaction</td>
			<td>공통경로</td>
			<td>5000</td>
			<td>1</td>
			<td>12</td>
		</tr>
		<tr>
			<td>5</td>
			<td>Transaction</td>
			<td>공통경로</td>
			<td>10000</td>
			<td>1</td>
			<td>12</td>
		</tr>
		<tr>
			<td>6</td>
			<td>Transaction</td>
			<td>통합</td>
			<td>1000</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>7</td>
			<td>Transaction</td>
			<td>통합</td>
			<td>5000</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>8</td>
			<td>Transaction</td>
			<td>통합</td>
			<td>10000</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>9</td>
			<td>Process</td>
			<td>업무단계</td>
			<td>1000</td>
			<td>83</td>
			<td>218</td>
		</tr>
		<tr>
			<td>10</td>
			<td>Process</td>
			<td>업무단계</td>
			<td>5000</td>
			<td>82</td>
			<td>218</td>
		</tr>
		<tr>
			<td>11</td>
			<td>Process</td>
			<td>업무단계</td>
			<td>10000</td>
			<td>82</td>
			<td>218</td>
		</tr>
		<tr>
			<td>12</td>
			<td>Process</td>
			<td>공통어댑터</td>
			<td>1000</td>
			<td>5</td>
			<td>29</td>
		</tr>
		<tr>
			<td>13</td>
			<td>Process</td>
			<td>공통어댑터</td>
			<td>5000</td>
			<td>4</td>
			<td>29</td>
		</tr>
		<tr>
			<td>14</td>
			<td>Process</td>
			<td>공통어댑터</td>
			<td>10000</td>
			<td>4</td>
			<td>29</td>
		</tr>
		<tr>
			<td>15</td>
			<td>Process</td>
			<td>통합</td>
			<td>1000</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>16</td>
			<td>Process</td>
			<td>통합</td>
			<td>5000</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>17</td>
			<td>Process</td>
			<td>통합</td>
			<td>10000</td>
			<td>1</td>
			<td>1</td>
		</tr>
	</table>
</details>

## 고정 룰의 운영 승인 체크리스트

<table fit-page-width="true" header-row="true">
	<tr>
		<td>지금 정할 값</td>
		<td>운영자에게 확인할 값</td>
		<td>새 데이터마다 재계산하지 않을 값</td>
	</tr>
	<tr>
		<td>STATUS 사전 S/F/P/N/H/Z</td>
		<td>F/H 알림 범위, Z 처리 정책</td>
		<td>허용 상태 사전·규칙 버전</td>
	</tr>
	<tr>
		<td>SQL PK / NOT NULL</td>
		<td>거래 재시도와 단계 재시도의 대응</td>
		<td>연결 키·중복 처리 계약</td>
	</tr>
	<tr>
		<td>기본 시각 허용 오차 1ms</td>
		<td>시계 동기화·비동기 실행 허용</td>
		<td>승인한 tolerance / SLA</td>
	</tr>
	<tr>
		<td>금지 업무/패턴/시간대 default 비활성</td>
		<td>실제 금지 업무와 스케줄</td>
		<td>승인한 목록·정규식·시간대</td>
	</tr>
	<tr>
		<td>count 보존 default 검토</td>
		<td>어댑터별 total/success/error의 계수 정의</td>
		<td>확정한 보존식·계약 적용 범위</td>
	</tr>
	<tr>
		<td>p99/p99.9 학습 기준</td>
		<td>기준선 버전·지원량·재보정 승인</td>
		<td>해당 버전 임계값; 새 데이터 적용만 수행</td>
	</tr>
</table>

통계 후보, 규칙 위반, 실행 결과 실패, 확정 운영 장애는 서로 다릅니다. 사람 검토/실험으로 확인하기 전에는 후보율을 정확도라고 보고하지 않습니다.
