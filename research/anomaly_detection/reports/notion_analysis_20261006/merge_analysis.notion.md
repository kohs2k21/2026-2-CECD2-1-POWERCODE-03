<callout icon="💡" color="blue_bg">
	**연구 주제: 대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발**<br>거래 지연을 단계별 실행과 연결해 병목·누락·상태·시간 경계를 설명합니다. 두 원본을 결합해 만든 파생 데이터이지 세 번째 독립 로그 원본이 아닙니다.
</callout>

원본: `code/merge_analysis.ipynb` · 분석 단위: **거래 한 건에 프로세스 실행 구조를 요약한 한 행** · 실행 결과 확인일: <mention-date start="2026-10-06"/>.

이 문서는 노트북을 재실행하거나 모델을 재학습한 결과가 아니라, **현재 저장된 실행 출력**을 옮겨 해석한 것입니다. 이미지는 원본 PNG, 표는 저장된 수치표입니다. 원본 표의 반올림·상위 N개 출력·생략 표시도 유지합니다.

기간 표기: ① 파란색 = 25.12–26.04, ② 초록색 = 26.08–09, ③ 주황색 = 26.09.30–10.02. 전체 통계와 기간별 균형 표본을 구분해 읽습니다.

<callout icon="⚠️" color="yellow_bg">
	**후보 ≠ 확정 장애**<br>통계 규칙·정합성 규칙·비지도 점수는 서로 다른 점검 근거입니다. 분모가 다르고 후보가 중복될 수 있습니다. 후보 0건과 비교 불가는 다르며, 실패 상태도 실제 이상 정답 라벨이 아닙니다.
</callout>

<table_of_contents/>

<details>
<summary>출처와 재현성 정보</summary>
	노트북 SHA-256: `5222f5c5e31079144c64d5ee9826a6b301096b7056b627507ebcdd5d988350a7`
	공통 계산: `code/log_analysis_common.py`
	시각화: `code/log_analysis_visuals.py`
	셀 번호는 마크다운을 포함해 위에서부터 센 번호이며 실행 번호 In[n]과 다릅니다.
</details>

---

## 1. 입력·결합 키·1:N 관계

**핵심 결과**

거래 **2,008,703행**, Process **3,714,199행**을 `source_period + transactionId`로 결합합니다. 거래를 찾지 못한 고아 Process는 첫 기간에 **6,109행** 있습니다.

<details>
<summary>원본 수치표 펼치기 · 셀 4 · 3개</summary>
	**원본 표 1 · 셀 4**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>source_file</td>
			<td>format</td>
			<td>raw_rows</td>
			<td>rows</td>
			<td>exact_duplicates_removed</td>
			<td>duplicate_attempt_keys</td>
			<td>missing_transaction_id</td>
			<td>invalid_timestamp_values</td>
			<td>invalid_numeric_values</td>
			<td>first_start</td>
			<td>last_start</td>
			<td>last_end</td>
			<td>observed_days</td>
			<td>file_MB</td>
		</tr>
		<tr>
			<td>0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>csv</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2025-12-24 10:25:31.632</td>
			<td>2026-04-06 08:51:33.002</td>
			<td>2026-04-06 08:52:21.416</td>
			<td>59</td>
			<td>317.01</td>
		</tr>
		<tr>
			<td>1</td>
			<td>26년8월15일-26년9월14일</td>
			<td>26년8월15일-26년9월14일\transactions.csv</td>
			<td>csv</td>
			<td>213381</td>
			<td>213381</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-08-15 00:00:00.257</td>
			<td>2026-09-14 14:19:58.603</td>
			<td>2026-09-14 14:20:09.431</td>
			<td>19</td>
			<td>36.65</td>
		</tr>
		<tr>
			<td>2</td>
			<td>26년9월30일-26년10월2일</td>
			<td>26년9월30일-26년10월2일\EC_MESSAGE_BODY.csv</td>
			<td>sql</td>
			<td>27268</td>
			<td>27268</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-09-30 09:49:26.483</td>
			<td>2026-10-02 15:36:59.444</td>
			<td>2026-10-02 15:36:59.698</td>
			<td>3</td>
			<td>13.03</td>
		</tr>
	</table>

	**원본 표 2 · 셀 4**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>source_file</td>
			<td>format</td>
			<td>raw_rows</td>
			<td>rows</td>
			<td>exact_duplicates_removed</td>
			<td>duplicate_attempt_keys</td>
			<td>missing_transaction_id</td>
			<td>invalid_timestamp_values</td>
			<td>invalid_numeric_values</td>
			<td>first_start</td>
			<td>last_start</td>
			<td>last_end</td>
			<td>observed_days</td>
			<td>file_MB</td>
		</tr>
		<tr>
			<td>0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
			<td>csv</td>
			<td>3473285</td>
			<td>3473285</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2025-12-24 10:25:31.689</td>
			<td>2026-04-06 08:51:33.531</td>
			<td>2026-04-06 08:52:21.391</td>
			<td>60</td>
			<td>557.08</td>
		</tr>
		<tr>
			<td>1</td>
			<td>26년8월15일-26년9월14일</td>
			<td>26년8월15일-26년9월14일\processes.csv</td>
			<td>csv</td>
			<td>213509</td>
			<td>213509</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-08-15 00:00:00.284</td>
			<td>2026-09-14 14:19:59.710</td>
			<td>2026-09-14 14:20:09.407</td>
			<td>19</td>
			<td>37.05</td>
		</tr>
		<tr>
			<td>2</td>
			<td>26년9월30일-26년10월2일</td>
			<td>26년9월30일-26년10월2일\EC_TRANSACTION_MONITORING.csv</td>
			<td>csv</td>
			<td>27405</td>
			<td>27405</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-09-30 09:49:26.494</td>
			<td>2026-10-02 15:36:59.559</td>
			<td>2026-10-02 15:36:59.685</td>
			<td>3</td>
			<td>4.90</td>
		</tr>
	</table>

	**원본 표 3 · 셀 4**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>transaction_rows</td>
			<td>process_execution_rows</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>1768054</td>
			<td>3473285</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213381</td>
			<td>213509</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27268</td>
			<td>27405</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-81e8-b0f1-00b2cbe7a364"></image>

*출처: `merge_analysis.ipynb` · 셀 6 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8125-a9f0-00b210ad359d"></image>

*출처: `merge_analysis.ipynb` · 셀 6 · 그림 2. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-81d2-be55-00b278e08933"></image>

*출처: `merge_analysis.ipynb` · 셀 6 · 그림 3. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 전체 2,008,703건 중 ① 25.12–26.04가 88.02%입니다. 시각화는 30,000건을 기간별 균형 추출했습니다.

**원본 출력의 확인 수치**

- 전체 3,714,199건 중 ① 25.12–26.04가 93.51%입니다. 시각화는 30,000건을 기간별 균형 추출했습니다.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 종료시각 결측 567,442건 (16.34%).
- ② 26.08–09: 종료시각 결측 136건 (0.06%).
- ③ 26.09.30–10.02: 종료시각 결측 0건 (0.00%).

<details>
<summary>원본 수치표 펼치기 · 셀 6 · 2개</summary>
	**원본 표 1 · 셀 6**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>전체 행 수</td>
			<td>그림 표본 행 수</td>
			<td>전체 내 비중 (%)</td>
			<td>표본 내 비중 (%)</td>
			<td>기간별 추출률 (%)</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>① 25.12–26.04</td>
			<td>1768054</td>
			<td>10000</td>
			<td>88.020</td>
			<td>33.333</td>
			<td>0.566</td>
		</tr>
		<tr>
			<td>② 26.08–09</td>
			<td>213381</td>
			<td>10000</td>
			<td>10.623</td>
			<td>33.333</td>
			<td>4.686</td>
		</tr>
		<tr>
			<td>③ 26.09.30–10.02</td>
			<td>27268</td>
			<td>10000</td>
			<td>1.357</td>
			<td>33.333</td>
			<td>36.673</td>
		</tr>
	</table>

	**원본 표 2 · 셀 6**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>전체 행 수</td>
			<td>그림 표본 행 수</td>
			<td>전체 내 비중 (%)</td>
			<td>표본 내 비중 (%)</td>
			<td>기간별 추출률 (%)</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>① 25.12–26.04</td>
			<td>3473285</td>
			<td>10000</td>
			<td>93.514</td>
			<td>33.333</td>
			<td>0.288</td>
		</tr>
		<tr>
			<td>② 26.08–09</td>
			<td>213509</td>
			<td>10000</td>
			<td>5.748</td>
			<td>33.333</td>
			<td>4.684</td>
		</tr>
		<tr>
			<td>③ 26.09.30–10.02</td>
			<td>27405</td>
			<td>10000</td>
			<td>0.738</td>
			<td>33.333</td>
			<td>36.490</td>
		</tr>
	</table>
</details>

**원본 출력의 확인 수치**

- 거래에 연결되지 않은 Process 행 6,109건 / 전체 결합 대상 3,714,199건.

<details>
<summary>원본 수치표 펼치기 · 셀 8 · 2개</summary>
	**원본 표 1 · 셀 8**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>_merge</td>
			<td>left_only</td>
			<td>both</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>6109</td>
			<td>3467176</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>0</td>
			<td>213509</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>0</td>
			<td>27405</td>
		</tr>
	</table>

	**원본 표 2 · 셀 8**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>processId</td>
			<td>status_process</td>
			<td>duration_ms</td>
			<td>adapterType</td>
			<td>interfaceId</td>
			<td>status_transaction</td>
			<td>processCount</td>
			<td>_merge</td>
		</tr>
		<tr>
			<td>2598538</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_920-20260226140050480-6134</td>
			<td>IF_DB2DB_920_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598544</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DBEXECUTOR_004-20260226140100446-3672</td>
			<td>IF_DBEXECUTOR_004_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.EXECUTOR</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598609</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_934-20260226142257019-6272</td>
			<td>IF_DB2DB_934_PR002</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.WRITER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598610</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_934-20260226142257019-6272</td>
			<td>IF_DB2DB_934_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598712</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_955-20260226135805241-7201</td>
			<td>IF_DB2DB_955_PR002</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.WRITER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598713</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_955-20260226135805241-7201</td>
			<td>IF_DB2DB_955_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598719</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_AGENT2AGENT_001-20260226135800680-6124</td>
			<td>IF_AGENT2AGENT_001_PR002</td>
			<td>P</td>
			<td>NaN</td>
			<td>FILE.WRITER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598730</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_908-20260226135828560-4105</td>
			<td>IF_DB2DB_908_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598734</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_SUB2DB_103-20260226135835060-4884</td>
			<td>IF_SUB2DB_103_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>QUEUE.EXECUTOR</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598735</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_SUB2DB_103-20260226135835060-4884</td>
			<td>IF_SUB2DB_103_PR003</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.WRITER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598748</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_940-20260226135930112-5579</td>
			<td>IF_DB2DB_940_PR002</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.WRITER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598749</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_934-20260226135934441-3349</td>
			<td>IF_DB2DB_934_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598930</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_977-20260226140107023-1058</td>
			<td>IF_DB2DB_977_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598934</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_996-20260226140116188-0679</td>
			<td>IF_DB2DB_996_PR002</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.WRITER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
		<tr>
			<td>2598937</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_949-20260226140119641-6869</td>
			<td>IF_DB2DB_949_PR001</td>
			<td>N</td>
			<td>NaN</td>
			<td>DB.READER</td>
			<td>&lt;NA&gt;</td>
			<td>&lt;NA&gt;</td>
			<td>NaN</td>
			<td>left_only</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

거래 한 건에 여러 단계가 붙는 1:N 관계입니다. 고아 Process는 보존 정책·수집 범위 차이 때문일 수도 있으며 억지로 다른 거래에 연결하면 안 됩니다. 결합 결과 행 수를 원본 행 수에 다시 더하지 않습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>조인 카디널리티를 검증하고 고아 로그는 별도 감사합니다. 거래 수준 모델 입력은 Process를 집계한 뒤 만들며 반복된 거래 특성이 단계 수만큼 중복 학습되지 않게 합니다. 주기적으로 결합률을 감시합니다.
</callout>

---

## 2. 선언 단계 수와 실제 실행 구조

**핵심 결과**

Process 로그 없는 거래는 **8,056건**, 선언된 Process 수와 실제 고유 Process 수의 불일치는 **15,299건**입니다. 재시도 없는 현재 데이터에서는 실행 행 수와 고유 Process 수가 같습니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>Process 없는 거래</td>
		<td>단계 수 비교 가능</td>
		<td>단계 수 불일치</td>
	</tr>
	<tr>
		<td>①</td>
		<td>8,015</td>
		<td>1,768,051</td>
		<td>15,250</td>
	</tr>
	<tr>
		<td>②</td>
		<td>27</td>
		<td>213,381</td>
		<td>31</td>
	</tr>
	<tr>
		<td>③</td>
		<td>14</td>
		<td>27,268</td>
		<td>18</td>
	</tr>
</table>

<details>
<summary>원본 수치표 펼치기 · 셀 10 · 2개</summary>
	**원본 표 1 · 셀 10**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>transactionId</td>
			<td>interfaceId</td>
			<td>processHubId</td>
			<td>startChannelId</td>
			<td>endChannelId</td>
			<td>processCount</td>
			<td>status</td>
			<td>responseCode</td>
			<td>interfaceType</td>
			<td>categoryName</td>
			<td>startTime</td>
			<td>endTime</td>
			<td>processTime</td>
			<td>retryCount</td>
			<td>source_period</td>
			<td>source_file</td>
			<td>duration_ms</td>
			<td>hour</td>
			<td>dayofweek</td>
			<td>negative_duration</td>
			<td>...</td>
			<td>process_first_start</td>
			<td>process_last_end</td>
			<td>bottleneck_duration_ms</td>
			<td>process_duration_sum_ms</td>
			<td>logical_processes</td>
			<td>latest_success</td>
			<td>latest_failure</td>
			<td>process_derived_status</td>
			<td>no_process_logs</td>
			<td>process_count_diff</td>
			<td>count_comparable</td>
			<td>count_mismatch_candidate</td>
			<td>status_comparable</td>
			<td>status_mismatch_candidate</td>
			<td>start_delta_ms</td>
			<td>end_delta_ms</td>
			<td>process_span_ms</td>
			<td>orchestration_overhead_ms</td>
			<td>parallelism_ratio</td>
			<td>boundary_candidate</td>
		</tr>
		<tr>
			<td>0</td>
			<td>CUSTOM-IF_A2A_FILE_001-20260127113845933-5096</td>
			<td>IF_A2A_FILE_001</td>
			<td>DIRECT</td>
			<td>SOL</td>
			<td>TSC</td>
			<td>2.0</td>
			<td>F</td>
			<td>4201</td>
			<td>NORMAL</td>
			<td>NONE</td>
			<td>2026-01-27 11:38:46.110</td>
			<td>2026-01-27 11:38:47.477</td>
			<td>1367.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>1367.0</td>
			<td>11</td>
			<td>1</td>
			<td>False</td>
			<td>...</td>
			<td>2026-01-27 11:38:44.959</td>
			<td>2026-01-27 11:38:45.263</td>
			<td>64.0</td>
			<td>64.0</td>
			<td>2.0</td>
			<td>0.0</td>
			<td>1.0</td>
			<td>F</td>
			<td>False</td>
			<td>0.0</td>
			<td>True</td>
			<td>False</td>
			<td>True</td>
			<td>False</td>
			<td>-1151.0</td>
			<td>2214.0</td>
			<td>304.0</td>
			<td>1063.0</td>
			<td>0.046818</td>
			<td>True</td>
		</tr>
		<tr>
			<td>1</td>
			<td>CUSTOM-IF_A2A_FILE_001-20260127113856991-1470</td>
			<td>IF_A2A_FILE_001</td>
			<td>DIRECT</td>
			<td>SOL</td>
			<td>TSC</td>
			<td>2.0</td>
			<td>F</td>
			<td>4201</td>
			<td>NORMAL</td>
			<td>NONE</td>
			<td>2026-01-27 11:38:57.001</td>
			<td>2026-01-27 11:38:57.093</td>
			<td>92.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>92.0</td>
			<td>11</td>
			<td>1</td>
			<td>False</td>
			<td>...</td>
			<td>2026-01-27 11:38:54.716</td>
			<td>2026-01-27 11:38:54.851</td>
			<td>47.0</td>
			<td>47.0</td>
			<td>2.0</td>
			<td>0.0</td>
			<td>1.0</td>
			<td>F</td>
			<td>False</td>
			<td>0.0</td>
			<td>True</td>
			<td>False</td>
			<td>True</td>
			<td>False</td>
			<td>-2285.0</td>
			<td>2242.0</td>
			<td>135.0</td>
			<td>-43.0</td>
			<td>0.510870</td>
			<td>True</td>
		</tr>
		<tr>
			<td>2</td>
			<td>HUG_DB_WRITER_TEST_0001</td>
			<td>HUG_DB_WRITER_TEST</td>
			<td>HUB</td>
			<td>SOL</td>
			<td>SOL</td>
			<td>3.0</td>
			<td>S</td>
			<td>0000</td>
			<td>NORMAL</td>
			<td>NONE</td>
			<td>2026-03-26 14:17:03.184</td>
			<td>2026-03-26 14:17:03.486</td>
			<td>302.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>302.0</td>
			<td>14</td>
			<td>3</td>
			<td>False</td>
			<td>...</td>
			<td>2026-03-26 14:17:03.206</td>
			<td>2026-03-26 14:17:03.483</td>
			<td>122.0</td>
			<td>292.0</td>
			<td>3.0</td>
			<td>3.0</td>
			<td>0.0</td>
			<td>S</td>
			<td>False</td>
			<td>0.0</td>
			<td>True</td>
			<td>False</td>
			<td>True</td>
			<td>False</td>
			<td>22.0</td>
			<td>3.0</td>
			<td>277.0</td>
			<td>25.0</td>
			<td>0.966887</td>
			<td>False</td>
		</tr>
		<tr>
			<td>3</td>
			<td>KHUG_ILE2ILE_20260128112030218171</td>
			<td>HUG_FILE2FILE_BINDING_01</td>
			<td>HUB</td>
			<td>SOL</td>
			<td>TSC</td>
			<td>2.0</td>
			<td>S</td>
			<td>0000</td>
			<td>NORMAL</td>
			<td>NONE</td>
			<td>2026-01-28 11:20:30.488</td>
			<td>2026-01-28 11:20:40.149</td>
			<td>9661.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>9661.0</td>
			<td>11</td>
			<td>2</td>
			<td>False</td>
			<td>...</td>
			<td>2026-01-28 11:20:30.517</td>
			<td>2026-01-28 11:20:40.145</td>
			<td>7024.0</td>
			<td>9624.0</td>
			<td>2.0</td>
			<td>2.0</td>
			<td>0.0</td>
			<td>S</td>
			<td>False</td>
			<td>0.0</td>
			<td>True</td>
			<td>False</td>
			<td>True</td>
			<td>False</td>
			<td>29.0</td>
			<td>4.0</td>
			<td>9628.0</td>
			<td>33.0</td>
			<td>0.996170</td>
			<td>False</td>
		</tr>
		<tr>
			<td>4</td>
			<td>TR-AAA_DB_EDIT_TEST-20260102125141137-1</td>
			<td>AAA_DB_EDIT_TEST</td>
			<td>HUB</td>
			<td>SOL</td>
			<td>SOL</td>
			<td>4.0</td>
			<td>F</td>
			<td>4102</td>
			<td>NORMAL</td>
			<td>NONE</td>
			<td>2026-01-02 12:51:41.199</td>
			<td>2026-01-02 12:52:35.726</td>
			<td>54527.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>54527.0</td>
			<td>12</td>
			<td>4</td>
			<td>False</td>
			<td>...</td>
			<td>2026-01-02 12:51:41.237</td>
			<td>2026-01-02 12:52:35.774</td>
			<td>54537.0</td>
			<td>218017.0</td>
			<td>4.0</td>
			<td>1.0</td>
			<td>3.0</td>
			<td>F</td>
			<td>False</td>
			<td>0.0</td>
			<td>True</td>
			<td>False</td>
			<td>True</td>
			<td>False</td>
			<td>38.0</td>
			<td>-48.0</td>
			<td>54537.0</td>
			<td>-10.0</td>
			<td>3.998331</td>
			<td>True</td>
		</tr>
	</table>

	**원본 표 2 · 셀 10**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>transactions</td>
			<td>no_process</td>
			<td>count_comparable</td>
			<td>count_mismatches</td>
			<td>total_attempts</td>
			<td>total_logical_processes</td>
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
			<td>8015</td>
			<td>1768051</td>
			<td>15250</td>
			<td>3467176.0</td>
			<td>3467176.0</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213381</td>
			<td>27</td>
			<td>213381</td>
			<td>31</td>
			<td>213509.0</td>
			<td>213509.0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27268</td>
			<td>14</td>
			<td>27268</td>
			<td>18</td>
			<td>27405.0</td>
			<td>27405.0</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-81f7-b64c-00b2db48f817"></image>

*출처: `merge_analysis.ipynb` · 셀 11 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-81e1-9ced-00b2f877d404"></image>

*출처: `merge_analysis.ipynb` · 셀 11 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 선언 수 일치, 1,752,801건 (99.14%).
- ② 26.08–09: 최빈값 선언 수 일치, 213,350건 (99.99%).
- ③ 26.09.30–10.02: 최빈값 선언 수 일치, 27,250건 (99.93%).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 2.0, 1,637,311건 (92.61%).
- ② 26.08–09: 최빈값 1.0, 213,260건 (99.94%).
- ③ 26.09.30–10.02: 최빈값 1.0, 27,151건 (99.57%).

**원본 출력의 확인 수치**

- Process 로그 없는 거래 8,056건. 선언 수 비교 후보 15,299건(로그 없는 거래와 중복 가능).

**이 결과로 알 수 있는 내용**

실제 관측 단계 부족이 실행 누락인지 수집 누락인지 이 결과만으로 구분할 수 없습니다. 로그 없는 거래는 단계 수 불일치에도 포함될 수 있어 후보 수를 합산하면 안 됩니다. 상태 해석용 최신 실행과 원본 실행량 집계는 다른 목적입니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>`declared_count`, `distinct_processes`, `count_diff`, `no_process_logs`를 구분합니다. 실제 재시도가 생기면 단계 수는 고유 Process, 시도 수는 실행 행으로 계산합니다. 현재 완전히 중복인 시도 수·고유 수를 모두 넣는 모델과 하나를 제거한 모델을 비교합니다.
</callout>

---

## 3. 거래 상태와 최종 단계 상태

**핵심 결과**

양쪽이 S/F로 비교 가능한 **1,695,752건** 중 상태 불일치는 **30건(26 / 4 / 0건)**입니다.

<image src="file-upload://3f125113-b9ae-8126-b6b0-00b283caa6fd"></image>

*출처: `merge_analysis.ipynb` · 셀 13 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-817b-ad98-00b2a1546a86"></image>

*출처: `merge_analysis.ipynb` · 셀 13 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04 / S/F 상태 불일치: 26 / 1,455,313건 (0.002%).
- ② 26.08–09 / S/F 상태 불일치: 4 / 213,230건 (0.002%).
- ③ 26.09.30–10.02 / S/F 상태 불일치: 0 / 27,209건 (0.000%).

**원본 출력의 확인 수치**

- 양쪽이 S/F인 1,695,752건 중 상태 불일치 30건.

<details>
<summary>원본 수치표 펼치기 · 셀 13 · 2개</summary>
	**원본 표 1 · 셀 13**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>process_derived_status</td>
			<td>F</td>
			<td>NO_PROCESS</td>
			<td>OTHER</td>
			<td>S</td>
		</tr>
		<tr>
			<td>status</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>F</td>
			<td>132680</td>
			<td>5604</td>
			<td>299945</td>
			<td>22</td>
		</tr>
		<tr>
			<td>H</td>
			<td>29</td>
			<td>0</td>
			<td>32</td>
			<td>0</td>
		</tr>
		<tr>
			<td>P</td>
			<td>19</td>
			<td>2451</td>
			<td>4585</td>
			<td>6</td>
		</tr>
		<tr>
			<td>S</td>
			<td>8</td>
			<td>1</td>
			<td>1</td>
			<td>1563042</td>
		</tr>
		<tr>
			<td>Z</td>
			<td>0</td>
			<td>0</td>
			<td>278</td>
			<td>0</td>
		</tr>
	</table>

	**원본 표 2 · 셀 13**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>comparable</td>
			<td>mismatch</td>
			<td>mismatch_pct_of_comparable</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>1455313</td>
			<td>26</td>
			<td>0.002</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213230</td>
			<td>4</td>
			<td>0.002</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27209</td>
			<td>0</td>
			<td>0.0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

Process가 없는 NO_PROCESS, 다른 상태인 OTHER는 S/F 불일치 분모에서 제외됩니다. 실패 단계가 있어도 거래가 복구·보상 후 성공했는지, 기록 시점이 다른지 확인이 필요합니다. 최신 재시도 상태를 사용한 결과이지 전체 과거 상태의 단순 합계가 아닙니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>부모–단계 상태 불일치는 독립 감사 규칙과 설명 근거로 유지합니다. 복구·보상·부분 성공의 업무 정의를 확인하기 전에는 실패 라벨로 확정하지 않습니다. 사후 상태를 실시간 초기 입력에 넣지 않습니다.
</callout>

---

## 4. 거래 지연·병목·오버헤드·시간 경계

**핵심 결과**

오버헤드 중앙값은 **30ms / 28ms / 12ms**, Process 시간 합/거래 시간 중앙값은 **0.992 / 0.529 / 0.998**입니다. 거래 시간 경계 밖 Process 후보는 **30,405 / 15,372 / 7,380건**입니다. 통합 표에는 음수 오버헤드 **44건**, Process 실행 범위 최대 **약 80일**도 나타납니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>오버헤드 중앙값(ms)</td>
		<td>시간 합/거래 시간 중앙값</td>
		<td>경계 후보(전체 거래 대비)</td>
	</tr>
	<tr>
		<td>①</td>
		<td>30</td>
		<td>0.991663</td>
		<td>30,405 (1.720%)</td>
	</tr>
	<tr>
		<td>②</td>
		<td>28</td>
		<td>0.529412</td>
		<td>15,372 (7.204%)</td>
	</tr>
	<tr>
		<td>③</td>
		<td>12</td>
		<td>0.997662</td>
		<td>7,380 (27.065%)</td>
	</tr>
</table>

<details>
<summary>원본 수치표 펼치기 · 셀 15 · 3개</summary>
	**원본 표 1 · 셀 15**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>count</td>
			<td>mean</td>
			<td>std</td>
			<td>min</td>
			<td>1%</td>
			<td>25%</td>
			<td>50%</td>
			<td>75%</td>
			<td>95%</td>
			<td>99%</td>
			<td>99.9%</td>
			<td>max</td>
			<td>missing_pct</td>
			<td>zero_pct</td>
			<td>negative_count</td>
			<td>skewness</td>
		</tr>
		<tr>
			<td>duration_ms</td>
			<td>2001643.0</td>
			<td>5358.338</td>
			<td>96904.603</td>
			<td>1.000000e+00</td>
			<td>3.000</td>
			<td>144.000</td>
			<td>4814.000</td>
			<td>7514.000</td>
			<td>9617.000</td>
			<td>10818.000</td>
			<td>216979.070</td>
			<td>3.378161e+07</td>
			<td>0.351</td>
			<td>0.000</td>
			<td>0</td>
			<td>284.234</td>
		</tr>
		<tr>
			<td>process_span_ms</td>
			<td>1698154.0</td>
			<td>9470.488</td>
			<td>5306219.782</td>
			<td>0.000000e+00</td>
			<td>14.000</td>
			<td>2670.000</td>
			<td>5692.000</td>
			<td>7855.000</td>
			<td>9661.000</td>
			<td>10790.000</td>
			<td>24888.317</td>
			<td>6.914704e+09</td>
			<td>15.460</td>
			<td>0.001</td>
			<td>0</td>
			<td>1303.128</td>
		</tr>
		<tr>
			<td>process_duration_sum_ms</td>
			<td>1698154.0</td>
			<td>5385.673</td>
			<td>8399.588</td>
			<td>0.000000e+00</td>
			<td>14.000</td>
			<td>2662.000</td>
			<td>5672.000</td>
			<td>7835.000</td>
			<td>9639.000</td>
			<td>10767.470</td>
			<td>24900.541</td>
			<td>1.802839e+06</td>
			<td>15.460</td>
			<td>0.001</td>
			<td>0</td>
			<td>143.373</td>
		</tr>
		<tr>
			<td>bottleneck_duration_ms</td>
			<td>1698154.0</td>
			<td>5013.646</td>
			<td>8315.635</td>
			<td>0.000000e+00</td>
			<td>14.000</td>
			<td>2384.000</td>
			<td>5226.000</td>
			<td>7349.000</td>
			<td>9131.000</td>
			<td>10193.000</td>
			<td>24479.388</td>
			<td>1.802839e+06</td>
			<td>15.460</td>
			<td>0.001</td>
			<td>0</td>
			<td>147.642</td>
		</tr>
		<tr>
			<td>orchestration_overhead_ms</td>
			<td>1698130.0</td>
			<td>-3811.666</td>
			<td>5306259.798</td>
			<td>-6.914703e+09</td>
			<td>13.000</td>
			<td>26.000</td>
			<td>29.000</td>
			<td>35.000</td>
			<td>60.000</td>
			<td>126.000</td>
			<td>52903.097</td>
			<td>1.798615e+06</td>
			<td>15.461</td>
			<td>0.000</td>
			<td>44</td>
			<td>-1303.120</td>
		</tr>
		<tr>
			<td>parallelism_ratio</td>
			<td>1698130.0</td>
			<td>0.929</td>
			<td>0.383</td>
			<td>0.000000e+00</td>
			<td>0.349</td>
			<td>0.984</td>
			<td>0.991</td>
			<td>0.994</td>
			<td>0.996</td>
			<td>0.998</td>
			<td>1.022</td>
			<td>4.491520e+02</td>
			<td>15.461</td>
			<td>0.001</td>
			<td>0</td>
			<td>941.384</td>
		</tr>
		<tr>
			<td>start_delta_ms</td>
			<td>2000633.0</td>
			<td>-2942.879</td>
			<td>4889070.354</td>
			<td>-6.914704e+09</td>
			<td>-2124.000</td>
			<td>10.000</td>
			<td>16.000</td>
			<td>20.000</td>
			<td>38.000</td>
			<td>78.000</td>
			<td>183723.504</td>
			<td>2.784193e+07</td>
			<td>0.402</td>
			<td>0.203</td>
			<td>38119</td>
			<td>-1414.081</td>
		</tr>
		<tr>
			<td>end_delta_ms</td>
			<td>1698131.0</td>
			<td>51.242</td>
			<td>1415.121</td>
			<td>-2.233300e+04</td>
			<td>-2.000</td>
			<td>8.000</td>
			<td>13.000</td>
			<td>18.000</td>
			<td>31.000</td>
			<td>2178.000</td>
			<td>4946.000</td>
			<td>1.797302e+06</td>
			<td>15.461</td>
			<td>0.198</td>
			<td>20422</td>
			<td>1206.487</td>
		</tr>
	</table>

	**원본 표 2 · 셀 15**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>rows</td>
			<td>boundary_candidates</td>
			<td>median_overhead_ms</td>
			<td>median_parallelism</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>1768054</td>
			<td>30405</td>
			<td>30.0</td>
			<td>0.991663</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213381</td>
			<td>15372</td>
			<td>28.0</td>
			<td>0.529412</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27268</td>
			<td>7380</td>
			<td>12.0</td>
			<td>0.997662</td>
		</tr>
	</table>

	**원본 표 3 · 셀 15**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>start_delta_ms</td>
			<td>end_delta_ms</td>
			<td>duration_ms</td>
			<td>process_span_ms</td>
		</tr>
		<tr>
			<td>0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>CUSTOM-IF_A2A_FILE_001-20260127113845933-5096</td>
			<td>-1151.0</td>
			<td>2214.0</td>
			<td>1367.0</td>
			<td>304.0</td>
		</tr>
		<tr>
			<td>1</td>
			<td>25년12월24일-26년4월6일</td>
			<td>CUSTOM-IF_A2A_FILE_001-20260127113856991-1470</td>
			<td>-2285.0</td>
			<td>2242.0</td>
			<td>92.0</td>
			<td>135.0</td>
		</tr>
		<tr>
			<td>4</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-AAA_DB_EDIT_TEST-20260102125141137-1</td>
			<td>38.0</td>
			<td>-48.0</td>
			<td>54527.0</td>
			<td>54537.0</td>
		</tr>
		<tr>
			<td>5</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-AAA_DB_EDIT_TEST-20260326143306616-01</td>
			<td>32.0</td>
			<td>-125.0</td>
			<td>25704.0</td>
			<td>25797.0</td>
		</tr>
		<tr>
			<td>9</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-AA_SOAP_TEST-20260402142635343-01</td>
			<td>36.0</td>
			<td>-334.0</td>
			<td>44562.0</td>
			<td>44860.0</td>
		</tr>
		<tr>
			<td>10</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-AA_SOAP_TEST-20260402142719099-01</td>
			<td>28.0</td>
			<td>-384.0</td>
			<td>54299.0</td>
			<td>54655.0</td>
		</tr>
		<tr>
			<td>24657</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_DB_WRITER_TEST-20251230160908626-1</td>
			<td>22.0</td>
			<td>-27.0</td>
			<td>193.0</td>
			<td>198.0</td>
		</tr>
		<tr>
			<td>24662</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_DB_WRITER_TEST-20260114113603601-1</td>
			<td>32.0</td>
			<td>-564.0</td>
			<td>180.0</td>
			<td>712.0</td>
		</tr>
		<tr>
			<td>24663</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_DB_WRITER_TEST-20260114113724410-1</td>
			<td>297.0</td>
			<td>-16.0</td>
			<td>1817.0</td>
			<td>1536.0</td>
		</tr>
		<tr>
			<td>24664</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_DB_WRITER_TEST-20260115111449011-1</td>
			<td>597.0</td>
			<td>-27.0</td>
			<td>3894.0</td>
			<td>3324.0</td>
		</tr>
		<tr>
			<td>24665</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_DB_WRITER_TEST-20260115111616528-1</td>
			<td>71.0</td>
			<td>-1457.0</td>
			<td>360.0</td>
			<td>1746.0</td>
		</tr>
		<tr>
			<td>24699</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_DB_WRITER_TEST-20260330092450438-1365</td>
			<td>39.0</td>
			<td>-4.0</td>
			<td>275.0</td>
			<td>240.0</td>
		</tr>
		<tr>
			<td>24709</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_0001-20260128130717574-1</td>
			<td>29.0</td>
			<td>-1402.0</td>
			<td>269.0</td>
			<td>1642.0</td>
		</tr>
		<tr>
			<td>24714</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128111107215-1</td>
			<td>0.0</td>
			<td>-64.0</td>
			<td>28728.0</td>
			<td>28792.0</td>
		</tr>
		<tr>
			<td>24715</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128111159464-1</td>
			<td>0.0</td>
			<td>-23.0</td>
			<td>4763.0</td>
			<td>4786.0</td>
		</tr>
		<tr>
			<td>24716</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128111323877-1</td>
			<td>92.0</td>
			<td>-13.0</td>
			<td>1561.0</td>
			<td>1482.0</td>
		</tr>
		<tr>
			<td>24720</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128111948297-1</td>
			<td>110.0</td>
			<td>-1376.0</td>
			<td>8044.0</td>
			<td>9310.0</td>
		</tr>
		<tr>
			<td>24722</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128112530305-1</td>
			<td>77.0</td>
			<td>-1388.0</td>
			<td>5836.0</td>
			<td>7147.0</td>
		</tr>
		<tr>
			<td>24723</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128113350040-1</td>
			<td>967.0</td>
			<td>-274.0</td>
			<td>20827.0</td>
			<td>20134.0</td>
		</tr>
		<tr>
			<td>24727</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-HUG_FILE2FILE_BINDING_01-20260128131229581-1</td>
			<td>90.0</td>
			<td>-1474.0</td>
			<td>14552.0</td>
			<td>15936.0</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8148-b340-00b22c0fe522"></image>

*출처: `merge_analysis.ipynb` · 셀 16 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-814c-b2da-00b2ff054dec"></image>

*출처: `merge_analysis.ipynb` · 셀 16 · 그림 2. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8173-bb32-00b25fb8c541"></image>

*출처: `merge_analysis.ipynb` · 셀 16 · 그림 3. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 전체 오버헤드 중앙값 30ms, Process 시간 합/거래 시간 중앙값 0.992.
- ② 26.08–09: 전체 오버헤드 중앙값 28ms, Process 시간 합/거래 시간 중앙값 0.529.
- ③ 26.09.30–10.02: 전체 오버헤드 중앙값 12ms, Process 시간 합/거래 시간 중앙값 0.998.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 전체 중앙값 0.99, p95 1.00, p99 1.00.
- ② 26.08–09: 전체 중앙값 0.53, p95 0.82, p99 0.86.
- ③ 26.09.30–10.02: 전체 중앙값 1.00, p95 1.00, p99 1.00.

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 거래 시간 범위 밖 Process: 30,405 / 1,768,054건 (1.720%).
- ② 26.08–09 / 거래 시간 범위 밖 Process: 15,372 / 213,381건 (7.204%).
- ③ 26.09.30–10.02 / 거래 시간 범위 밖 Process: 7,380 / 27,268건 (27.065%).

**원본 출력의 확인 수치**

- 음수 오버헤드 또는 Process가 거래 범위를 벗어난 후보는 실행 시각/로그 정의를 함께 점검합니다.

**원본 출력의 확인 수치**

- 병목 후보 표는 업무·어댑터별 유효 Process 시간 p95 상위 그룹(실행 30건 이상)입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 16 · 1개</summary>
	**원본 표 1 · 셀 16**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td></td>
			<td>process_rows</td>
			<td>median_ms</td>
			<td>p95_ms</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>interfaceId</td>
			<td>adapterType</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_9xx_DELETE</td>
			<td>DB.DELETER</td>
			<td>144</td>
			<td>80230.0</td>
			<td>570741.80</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_976</td>
			<td>DB.WRITER</td>
			<td>1582</td>
			<td>19466.0</td>
			<td>393243.20</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_966</td>
			<td>DB.WRITER</td>
			<td>1669</td>
			<td>8804.0</td>
			<td>392532.50</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_946</td>
			<td>DB.WRITER</td>
			<td>1578</td>
			<td>8255.0</td>
			<td>392420.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_970</td>
			<td>DB.WRITER</td>
			<td>1496</td>
			<td>21660.0</td>
			<td>362698.80</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_990</td>
			<td>DB.WRITER</td>
			<td>1868</td>
			<td>14675.0</td>
			<td>362124.50</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_950</td>
			<td>DB.WRITER</td>
			<td>1709</td>
			<td>14390.0</td>
			<td>361553.30</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_960</td>
			<td>DB.WRITER</td>
			<td>1018</td>
			<td>195157.0</td>
			<td>357328.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_997</td>
			<td>DB.WRITER</td>
			<td>1488</td>
			<td>16493.0</td>
			<td>354138.40</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_987</td>
			<td>DB.WRITER</td>
			<td>1698</td>
			<td>17017.0</td>
			<td>353086.20</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_979</td>
			<td>DB.WRITER</td>
			<td>1904</td>
			<td>25435.0</td>
			<td>333542.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_947</td>
			<td>DB.WRITER</td>
			<td>1361</td>
			<td>22372.0</td>
			<td>332949.25</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_980</td>
			<td>DB.WRITER</td>
			<td>1965</td>
			<td>19004.0</td>
			<td>323226.50</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_959</td>
			<td>DB.WRITER</td>
			<td>2112</td>
			<td>20913.5</td>
			<td>313563.25</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_981</td>
			<td>DB.WRITER</td>
			<td>2215</td>
			<td>25348.5</td>
			<td>298954.15</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_971</td>
			<td>DB.WRITER</td>
			<td>1503</td>
			<td>42589.5</td>
			<td>269022.05</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_956</td>
			<td>DB.WRITER</td>
			<td>1874</td>
			<td>144145.5</td>
			<td>266413.65</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_961</td>
			<td>DB.WRITER</td>
			<td>1687</td>
			<td>23294.0</td>
			<td>260361.20</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_962</td>
			<td>DB.WRITER</td>
			<td>2497</td>
			<td>16845.0</td>
			<td>222861.75</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_982</td>
			<td>DB.WRITER</td>
			<td>2330</td>
			<td>19599.5</td>
			<td>221695.65</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

거래 지연이 가장 긴 단계 때문인지 단계 간 대기/오케스트레이션 때문인지 분리해서 볼 수 있습니다. 오버헤드는 거래 시간에서 관측된 Process span을 뺀 값이지 순수 엔진 오버헤드의 확정 측정값은 아닙니다. 80일 span 같은 극단값은 거래 키·타임스탬프·수집 정의 점검 대상입니다. 경계 초과는 비동기 실행이나 시계 차이로도 발생할 수 있습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>`bottleneck_duration_ms`, `process_span_ms`, 부호를 보존한 `signed_log(overhead)`, 실행 구조를 후보 설명에 사용합니다. 종료 로그가 일부만 있으면 span·합·병목이 과소 측정될 수 있으므로 `valid_duration_count`, 완료 비율, 결측 표시를 추가하고 점수 신뢰도를 낮춥니다. 극단 시간 값을 로그 변환만으로 덮지 말고 원본 키/시각을 확인합니다.
</callout>

---

## 5. 선행 관계와 거래 단위 설명

**핵심 결과**

Process 분석과 같은 선행 관계에서 로그 누락 **5,169건**, 시간 겹침 **105,235건**을 확인합니다.

<image src="file-upload://3f125113-b9ae-8156-9f5e-00b2daa41d00"></image>

*출처: `merge_analysis.ipynb` · 셀 18 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 선행 로그 없음: 5,169 / 1,713,241건 (0.302%).
- ② 26.08–09 / 선행 로그 없음: 0 / 152건 (0.000%).
- ③ 26.09.30–10.02 / 선행 로그 없음: 0 / 147건 (0.000%).
- ① 25.12–26.04 / 선행 종료 전 시작: 104,961 / 1,450,157건 (7.238%).
- ② 26.08–09 / 선행 종료 전 시작: 127 / 139건 (91.367%).
- ③ 26.09.30–10.02 / 선행 종료 전 시작: 147 / 147건 (100.000%).

**원본 출력의 확인 수치**

- 선행 관계 1,713,540건 중 시간 비교 가능 1,450,443건. 선행 로그 없음 5,169건; 시간 겹침 105,235건.

<details>
<summary>원본 수치표 펼치기 · 셀 18 · 1개</summary>
	**원본 표 1 · 셀 18**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>references</td>
			<td>missing</td>
			<td>retries_ambiguous</td>
			<td>comparable</td>
			<td>overlap_candidates</td>
			<td>template_only_elsewhere</td>
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
			<td>1713241</td>
			<td>5169</td>
			<td>0</td>
			<td>1450157</td>
			<td>104961</td>
			<td>5169</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>152</td>
			<td>0</td>
			<td>0</td>
			<td>139</td>
			<td>127</td>
			<td>0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>147</td>
			<td>0</td>
			<td>0</td>
			<td>147</td>
			<td>147</td>
			<td>0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

같은 원본의 관계를 거래 수준에서 해석하는 것이므로 새로운 독립 후보 건수가 아닙니다. 선행 종료시각 결측·연결 모호성·진짜 선행 로그 누락은 서로 다릅니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>거래별 선행 로그 누락 수·시간 비교 가능 수·겹침 비율을 집계한 추가 피처를 실험합니다. 분모가 0이면 비율을 0으로 넣지 말고 결측/비교 불가로 표시합니다. 현재 Merge 기준 모델에는 이 집계 피처가 입력되지 않았습니다.
</callout>

---

## 6. 실행 시퀀스와 드문 흐름

**핵심 결과**

균형 표본 거래 **30,000건** 중 시퀀스 생성은 **29,946건**입니다. 첫 기간의 최빈 시퀀스 `DB.READER → DB.WRITER`는 **91.54%**, 이후 `SERVICE.EXECUTOR`는 **99.92% / 99.72%**입니다.

<image src="file-upload://3f125113-b9ae-813a-904b-00b2847b4779"></image>

*출처: `merge_analysis.ipynb` · 셀 20 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 DB.READER → DB.WRITER가 91.54%, 상위 4개 합계 97.47%.
- ② 26.08–09: 최빈 SERVICE.EXECUTOR가 99.92%, 상위 4개 합계 99.98%.
- ③ 26.09.30–10.02: 최빈 SERVICE.EXECUTOR가 99.72%, 상위 4개 합계 99.93%.

**원본 출력의 확인 수치**

- 표본 거래 30,000건 중 시퀀스 생성 29,946건. Process 로그 없는 표본은 시퀀스 빈도의 분모에서 제외했습니다.

<details>
<summary>원본 수치표 펼치기 · 셀 20 · 2개</summary>
	**원본 표 1 · 셀 20**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>sample_transactions</td>
			<td>unique_sequences</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>9951</td>
			<td>18</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>9998</td>
			<td>6</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>9997</td>
			<td>9</td>
		</tr>
	</table>

	**원본 표 2 · 셀 20**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>추출 거래 수</td>
			<td>시퀀스 생성 거래 수</td>
			<td>시퀀스 없음</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>① 25.12–26.04</td>
			<td>10000</td>
			<td>9951</td>
			<td>49</td>
		</tr>
		<tr>
			<td>② 26.08–09</td>
			<td>10000</td>
			<td>9998</td>
			<td>2</td>
		</tr>
		<tr>
			<td>③ 26.09.30–10.02</td>
			<td>10000</td>
			<td>9997</td>
			<td>3</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

전체 거래의 확정 비율이 아니라 Process가 연결된 표본에서 본 실행 흐름입니다. 시작시각 순서가 실제 의존 관계 순서와 같다는 보장은 없습니다. 희귀 시퀀스는 신규 업무·정상 변형·누락 등 여러 원인을 가질 수 있습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>시퀀스 빈도·단계 전이·예상 단계 누락을 학습 구간에서 계산하는 별도 구조 피처로 확장합니다. 시작시각 정렬만 쓰는 표현과 실제 의존 그래프 표현을 비교합니다. 현재 기준 모델에는 시퀀스 피처가 입력되지 않았습니다.
</callout>

---

## 7. 거래 수준 시계열과 분포 변화

**핵심 결과**

일별 거래량·p95는 Transaction과 같은 거래 원본을 사용합니다. 두 번째 기간의 PSI는 Process 시간 합/거래 시간 **10.1465**, 실행 행 수와 고유 단계 수 각각 **10.1310**입니다. 신규 업무 ID 행 비율은 약 **99.97%**입니다.

<image src="file-upload://3f125113-b9ae-81fc-b5eb-00b285192cc3"></image>

*출처: `merge_analysis.ipynb` · 셀 22 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-810c-90c8-00b263c7b0e9"></image>

*출처: `merge_analysis.ipynb` · 셀 22 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 59일, 최대 329,988 (2025-12-28).
- ② 26.08–09: 관측 19일, 최대 25,922 (2026-08-19).
- ③ 26.09.30–10.02: 관측 3일, 최대 27,051 (2026-09-30).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 59일, 최대 1,800,114 (2026-03-20).
- ② 26.08–09: 관측 19일, 최대 78,980 (2026-09-04).
- ③ 26.09.30–10.02: 관측 3일, 최대 64,471 (2026-10-02).

<image src="file-upload://3f125113-b9ae-8113-ae68-00b26e588315"></image>

*출처: `merge_analysis.ipynb` · 셀 24 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ② 26.08–09 / Process 시간 합 / 거래 시간: PSI 10.146.
- ② 26.08–09 / Process 실행 행 수: PSI 10.131.
- ② 26.08–09 / 고유 Process 수: PSI 10.131.
- ② 26.08–09 / interfaceId: 기준 기간에 없던 값을 가진 로그 99.97%.

<details>
<summary>원본 수치표 펼치기 · 셀 24 · 2개</summary>
	**원본 표 1 · 셀 24**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>feature</td>
			<td>source_period</td>
			<td>reference_rows</td>
			<td>current_nonmissing</td>
			<td>bins</td>
			<td>PSI</td>
			<td>median</td>
			<td>p99</td>
			<td>missing_pct</td>
		</tr>
		<tr>
			<td>0</td>
			<td>duration_ms</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1760994</td>
			<td>1760994</td>
			<td>12</td>
			<td>0.0000</td>
			<td>5458.0000</td>
			<td>10952.0000</td>
			<td>0.3993</td>
		</tr>
		<tr>
			<td>1</td>
			<td>duration_ms</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1760994</td>
			<td>213381</td>
			<td>12</td>
			<td>10.0313</td>
			<td>58.0000</td>
			<td>398.2000</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>2</td>
			<td>duration_ms</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1760994</td>
			<td>27268</td>
			<td>12</td>
			<td>2.8529</td>
			<td>5984.0000</td>
			<td>52072.9700</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>3</td>
			<td>process_attempts</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1760039</td>
			<td>1760039</td>
			<td>4</td>
			<td>0.0000</td>
			<td>2.0000</td>
			<td>3.0000</td>
			<td>0.4533</td>
		</tr>
		<tr>
			<td>4</td>
			<td>process_attempts</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1760039</td>
			<td>213354</td>
			<td>4</td>
			<td>10.1310</td>
			<td>1.0000</td>
			<td>1.0000</td>
			<td>0.0127</td>
		</tr>
		<tr>
			<td>5</td>
			<td>process_attempts</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1760039</td>
			<td>27254</td>
			<td>4</td>
			<td>8.0588</td>
			<td>1.0000</td>
			<td>1.0000</td>
			<td>0.0513</td>
		</tr>
		<tr>
			<td>6</td>
			<td>distinct_processes</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1760039</td>
			<td>1760039</td>
			<td>4</td>
			<td>0.0000</td>
			<td>2.0000</td>
			<td>3.0000</td>
			<td>0.4533</td>
		</tr>
		<tr>
			<td>7</td>
			<td>distinct_processes</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1760039</td>
			<td>213354</td>
			<td>4</td>
			<td>10.1310</td>
			<td>1.0000</td>
			<td>1.0000</td>
			<td>0.0127</td>
		</tr>
		<tr>
			<td>8</td>
			<td>distinct_processes</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1760039</td>
			<td>27254</td>
			<td>4</td>
			<td>8.0588</td>
			<td>1.0000</td>
			<td>1.0000</td>
			<td>0.0513</td>
		</tr>
		<tr>
			<td>9</td>
			<td>bottleneck_duration_ms</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1457669</td>
			<td>1457669</td>
			<td>12</td>
			<td>0.0000</td>
			<td>5805.0000</td>
			<td>10357.0000</td>
			<td>17.5552</td>
		</tr>
		<tr>
			<td>10</td>
			<td>bottleneck_duration_ms</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1457669</td>
			<td>213231</td>
			<td>12</td>
			<td>10.0453</td>
			<td>31.0000</td>
			<td>174.0000</td>
			<td>0.0703</td>
		</tr>
		<tr>
			<td>11</td>
			<td>bottleneck_duration_ms</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1457669</td>
			<td>27254</td>
			<td>12</td>
			<td>2.2470</td>
			<td>5969.0000</td>
			<td>45544.8200</td>
			<td>0.0513</td>
		</tr>
		<tr>
			<td>12</td>
			<td>orchestration_overhead_ms</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1457645</td>
			<td>1457645</td>
			<td>12</td>
			<td>0.0000</td>
			<td>30.0000</td>
			<td>136.0000</td>
			<td>17.5565</td>
		</tr>
		<tr>
			<td>13</td>
			<td>orchestration_overhead_ms</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1457645</td>
			<td>213231</td>
			<td>12</td>
			<td>0.3808</td>
			<td>28.0000</td>
			<td>48.0000</td>
			<td>0.0703</td>
		</tr>
		<tr>
			<td>14</td>
			<td>orchestration_overhead_ms</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1457645</td>
			<td>27254</td>
			<td>12</td>
			<td>4.9666</td>
			<td>12.0000</td>
			<td>32533.1300</td>
			<td>0.0513</td>
		</tr>
		<tr>
			<td>15</td>
			<td>parallelism_ratio</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1457645</td>
			<td>1457645</td>
			<td>12</td>
			<td>0.0000</td>
			<td>0.9917</td>
			<td>0.9967</td>
			<td>17.5565</td>
		</tr>
		<tr>
			<td>16</td>
			<td>parallelism_ratio</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1457645</td>
			<td>213231</td>
			<td>12</td>
			<td>10.1465</td>
			<td>0.5294</td>
			<td>0.8606</td>
			<td>0.0703</td>
		</tr>
		<tr>
			<td>17</td>
			<td>parallelism_ratio</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1457645</td>
			<td>27254</td>
			<td>12</td>
			<td>2.2970</td>
			<td>0.9977</td>
			<td>0.9997</td>
			<td>0.0513</td>
		</tr>
	</table>

	**원본 표 2 · 셀 24**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>feature</td>
			<td>source_period</td>
			<td>unique_values</td>
			<td>new_values</td>
			<td>new_value_row_pct</td>
		</tr>
		<tr>
			<td>0</td>
			<td>interfaceId</td>
			<td>25년12월24일-26년4월6일</td>
			<td>365</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>1</td>
			<td>interfaceId</td>
			<td>26년8월15일-26년9월14일</td>
			<td>47</td>
			<td>31</td>
			<td>99.974</td>
		</tr>
		<tr>
			<td>2</td>
			<td>interfaceId</td>
			<td>26년9월30일-26년10월2일</td>
			<td>12</td>
			<td>10</td>
			<td>99.879</td>
		</tr>
		<tr>
			<td>3</td>
			<td>startChannelId</td>
			<td>25년12월24일-26년4월6일</td>
			<td>9</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>4</td>
			<td>startChannelId</td>
			<td>26년8월15일-26년9월14일</td>
			<td>5</td>
			<td>1</td>
			<td>0.002</td>
		</tr>
		<tr>
			<td>5</td>
			<td>startChannelId</td>
			<td>26년9월30일-26년10월2일</td>
			<td>2</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>6</td>
			<td>endChannelId</td>
			<td>25년12월24일-26년4월6일</td>
			<td>10</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>7</td>
			<td>endChannelId</td>
			<td>26년8월15일-26년9월14일</td>
			<td>4</td>
			<td>1</td>
			<td>0.002</td>
		</tr>
		<tr>
			<td>8</td>
			<td>endChannelId</td>
			<td>26년9월30일-26년10월2일</td>
			<td>2</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

실행 구조와 시간 구성도 기간에 따라 달라집니다. Process 합/거래 시간은 CPU 병렬도가 아니며, 1보다 크다는 이유만으로 장애라고 할 수 없습니다. 동일한 거래 지표를 두 노트북에서 봤다고 독립된 두 증거가 생긴 것은 아닙니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>업무군별 정상 구조와 실행 형태별 모델을 비교합니다. 구조 피처와 시간 피처의 drift를 함께 감시하고 업무/배포/수집 메타데이터와 대조합니다. 시계열 평가는 미관측 구간과 표본 적은 날짜를 별도로 표시합니다.
</callout>

---

## 8. 중복 정보와 입력 피처 선택

**핵심 결과**

균형 표본에서 실행 행 수–고유 Process 수 상관은 **1.000**, 거래 처리시간–병목 Process 시간 상관은 **0.986**입니다.

<image src="file-upload://3f125113-b9ae-813e-bd74-00b28686fbc6"></image>

*출처: `merge_analysis.ipynb` · 셀 26 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 가장 강한 관계: process_attempts–distinct_processes, ρ=1.000.
- 가장 강한 관계: duration_ms–bottleneck_duration_ms, ρ=0.986.
- 상수/유효값 부족으로 상관 계산 불가: 없음.

**이 결과로 알 수 있는 내용**

현재 재시도가 없어서 실행 수 두 표현은 같은 정보입니다. 거래 시간이 병목 시간과 강하게 연결되지만, 업무 혼합의 영향이나 오케스트레이션이 큰 일부 실행도 확인해야 합니다. 상관만으로 중요도를 확정할 수는 없습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>중복 수치 피처를 제거한 ablation과 원래 피처를 모두 쓰는 모델을 비교합니다. 거래 시간만 사용한 모델에 병목·오버헤드를 추가했을 때 실제 라벨 성능·설명 가능성·경보량이 개선되는지 확인합니다. 이 분석만으로 Merge 모델이 더 우수하다고 판단하지 않습니다.
</callout>

---

## 9. 결합 기준 모델·후보·PCA

**핵심 결과**

27개 피처, 학습 전체 **1,616,874행** 중 표본 **100,000행**, 검증 **280,526행**, 시험 **111,303행**입니다. 임계값 **0.7471764**, 시험 후보 **8,378건(약 7.527%)**입니다. 두 번째 기간 후보 8,324건, 최근 기간 후보 54건입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>시험 구간</td>
		<td>시험 거래</td>
		<td>후보 거래</td>
		<td>후보율</td>
	</tr>
	<tr>
		<td>②의 시험 부분</td>
		<td>84,035</td>
		<td>8,324</td>
		<td>9.9054%</td>
	</tr>
	<tr>
		<td>③</td>
		<td>27,268</td>
		<td>54</td>
		<td>0.1980%</td>
	</tr>
	<tr>
		<td>전체 시험</td>
		<td>111,303</td>
		<td>8,378</td>
		<td>약 7.527%</td>
	</tr>
</table>

**원본 출력의 확인 수치**

- 학습 전체 1,616,874건, 실제 모델 학습 표본 100,000건; 검증 280,526건, 시험 111,303건.

<details>
<summary>원본 수치표 펼치기 · 셀 28 · 3개</summary>
	**원본 표 1 · 셀 28**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>값</td>
		</tr>
		<tr>
			<td>training_rows</td>
			<td>1.616874e+06</td>
		</tr>
		<tr>
			<td>training_sample_rows</td>
			<td>1.000000e+05</td>
		</tr>
		<tr>
			<td>validation_rows</td>
			<td>2.805260e+05</td>
		</tr>
		<tr>
			<td>test_rows</td>
			<td>1.113030e+05</td>
		</tr>
		<tr>
			<td>feature_count</td>
			<td>2.700000e+01</td>
		</tr>
		<tr>
			<td>validation_quantile</td>
			<td>9.900000e-01</td>
		</tr>
		<tr>
			<td>threshold</td>
			<td>7.471764e-01</td>
		</tr>
	</table>

	**원본 표 2 · 셀 28**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>scored_rows</td>
			<td>mean_score</td>
			<td>candidates</td>
			<td>candidate_pct</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>split</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>train</td>
			<td>1616874</td>
			<td>1616874</td>
			<td>0.4404</td>
			<td>1998</td>
			<td>0.1236</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>validation</td>
			<td>151180</td>
			<td>151180</td>
			<td>0.4981</td>
			<td>255</td>
			<td>0.1687</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>test</td>
			<td>84035</td>
			<td>84035</td>
			<td>0.7319</td>
			<td>8324</td>
			<td>9.9054</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>validation</td>
			<td>129346</td>
			<td>129346</td>
			<td>0.7271</td>
			<td>2594</td>
			<td>2.0055</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>test</td>
			<td>27268</td>
			<td>27268</td>
			<td>0.6821</td>
			<td>54</td>
			<td>0.1980</td>
		</tr>
	</table>

	**원본 표 3 · 셀 28**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>first</td>
			<td>last</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>split</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>train</td>
			<td>1616874</td>
			<td>2025-12-24 10:25:31.632</td>
			<td>2026-03-20 17:04:21.488</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>validation</td>
			<td>151180</td>
			<td>2026-03-23 08:12:59.258</td>
			<td>2026-04-06 08:51:33.002</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>test</td>
			<td>84035</td>
			<td>2026-08-20 00:00:00.284</td>
			<td>2026-09-14 14:19:58.603</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>validation</td>
			<td>129346</td>
			<td>2026-08-15 00:00:00.257</td>
			<td>2026-08-19 23:59:55.035</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>test</td>
			<td>27268</td>
			<td>2026-09-30 09:49:26.483</td>
			<td>2026-10-02 15:36:59.444</td>
		</tr>
	</table>
</details>

<details>
<summary>원본 수치표 펼치기 · 셀 30 · 1개</summary>
	**원본 표 1 · 셀 30**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>startTime</td>
			<td>interfaceId</td>
			<td>status</td>
			<td>duration_ms</td>
			<td>process_attempts</td>
			<td>distinct_processes</td>
			<td>bottleneck_duration_ms</td>
			<td>count_mismatch_candidate</td>
			<td>status_mismatch_candidate</td>
			<td>boundary_candidate</td>
			<td>anomaly_score</td>
		</tr>
		<tr>
			<td>1839106</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260823063750023-01</td>
			<td>2026-08-23 06:37:50.027</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1069.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>41.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.777550</td>
		</tr>
		<tr>
			<td>1952809</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260821062139996-01</td>
			<td>2026-08-21 06:21:40.000</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1036.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>12.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.776827</td>
		</tr>
		<tr>
			<td>1953004</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260821065410019-01</td>
			<td>2026-08-21 06:54:10.024</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1036.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>13.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.776827</td>
		</tr>
		<tr>
			<td>1848044</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824072730017-01</td>
			<td>2026-08-24 07:27:30.022</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1055.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>31.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.772754</td>
		</tr>
		<tr>
			<td>1848204</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824075410027-01</td>
			<td>2026-08-24 07:54:10.031</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1068.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>41.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.772754</td>
		</tr>
		<tr>
			<td>1847657</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824062300028-01</td>
			<td>2026-08-24 06:23:00.032</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1076.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>51.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.772648</td>
		</tr>
		<tr>
			<td>1847766</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824064110031-01</td>
			<td>2026-08-24 06:41:10.035</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1058.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>31.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.772648</td>
		</tr>
		<tr>
			<td>1978803</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260824064110031-02</td>
			<td>2026-08-24 06:41:10.036</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1039.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>13.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.772648</td>
		</tr>
		<tr>
			<td>1952218</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260821044310031-02</td>
			<td>2026-08-21 04:43:10.036</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>727.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>15.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770906</td>
		</tr>
		<tr>
			<td>1969698</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260823052340030-02</td>
			<td>2026-08-23 05:23:40.033</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1046.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>16.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770757</td>
		</tr>
		<tr>
			<td>1970452</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260823072920024-02</td>
			<td>2026-08-23 07:29:20.029</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1052.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>20.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770757</td>
		</tr>
		<tr>
			<td>1970501</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260823073730023-01</td>
			<td>2026-08-23 07:37:30.027</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1044.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>18.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770757</td>
		</tr>
		<tr>
			<td>1847456</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824054930021-01</td>
			<td>2026-08-24 05:49:30.025</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1073.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>47.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770599</td>
		</tr>
		<tr>
			<td>1978061</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260824043730025-01</td>
			<td>2026-08-24 04:37:30.029</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1044.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>16.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770599</td>
		</tr>
		<tr>
			<td>1827450</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260821221510025-01</td>
			<td>2026-08-21 22:15:10.030</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1064.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>33.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770492</td>
		</tr>
		<tr>
			<td>1827629</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260821224500027-01</td>
			<td>2026-08-21 22:45:00.030</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1064.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>39.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770492</td>
		</tr>
		<tr>
			<td>1960697</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260822042330032-01</td>
			<td>2026-08-22 04:23:30.380</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>612.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>18.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770492</td>
		</tr>
		<tr>
			<td>1950938</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260821010950035-01</td>
			<td>2026-08-21 01:09:50.041</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>586.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>13.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770189</td>
		</tr>
		<tr>
			<td>1969376</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_REST_ERROR_RESPONSE-20260823043000025-02</td>
			<td>2026-08-23 04:30:00.029</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>F</td>
			<td>1047.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>18.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.770040</td>
		</tr>
		<tr>
			<td>1844854</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260823223550023-02</td>
			<td>2026-08-23 22:35:50.027</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>1062.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>38.0</td>
			<td>False</td>
			<td>False</td>
			<td>False</td>
			<td>0.769882</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8155-8021-00b2f959a120"></image>

*출처: `merge_analysis.ipynb` · 셀 31 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-81a6-b9f0-00b29ef172c9"></image>

*출처: `merge_analysis.ipynb` · 셀 31 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ② 26.08–09 / 시험 구간 모델 검토 후보: 8,324 / 84,035건 (9.905%).
- ③ 26.09.30–10.02 / 시험 구간 모델 검토 후보: 54 / 27,268건 (0.198%).

**원본 출력의 확인 수치**

- 시험 전체 111,303건 중 후보 8,378건 (7.53%). 임계값 0.7472; 검증 점수의 99% 분위수입니다.

**원본 출력의 확인 수치**

- 위 표의 각 행은 후보 또는 비후보 집단 내부의 상태/정합성 비율입니다. 두 집단의 건수는 서로 다릅니다.

<details>
<summary>원본 수치표 펼치기 · 셀 33 · 2개</summary>
	**원본 표 1 · 셀 33**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>status</td>
			<td>F</td>
			<td>H</td>
			<td>S</td>
			<td>Z</td>
		</tr>
		<tr>
			<td>anomaly_candidate</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>False</td>
			<td>64.54</td>
			<td>0.0</td>
			<td>35.42</td>
			<td>0.04</td>
		</tr>
		<tr>
			<td>True</td>
			<td>96.24</td>
			<td>0.0</td>
			<td>3.76</td>
			<td>0.00</td>
		</tr>
	</table>

	**원본 표 2 · 셀 33**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>integrity_candidate</td>
			<td>False</td>
			<td>True</td>
		</tr>
		<tr>
			<td>anomaly_candidate</td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>False</td>
			<td>82.09</td>
			<td>17.91</td>
		</tr>
		<tr>
			<td>True</td>
			<td>84.38</td>
			<td>15.62</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-815f-b083-00b2daae2285"></image>

*출처: `merge_analysis.ipynb` · 셀 35 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 각 축의 표본 0.5–99.5% 범위를 확대했습니다. 화면 밖 597건 중 후보 77건은 모델 계산에서 제외하지 않았습니다. 전체 투영 표본 후보 557건.

<details>
<summary>실제 모델 입력 피처 27개 · 셀 35</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td>번호</td>
			<td>피처</td>
		</tr>
		<tr>
			<td>1</td>
			<td>log_duration_ms</td>
		</tr>
		<tr>
			<td>2</td>
			<td>missing_duration_ms</td>
		</tr>
		<tr>
			<td>3</td>
			<td>log_processCount</td>
		</tr>
		<tr>
			<td>4</td>
			<td>missing_processCount</td>
		</tr>
		<tr>
			<td>5</td>
			<td>missing_retryCount</td>
		</tr>
		<tr>
			<td>6</td>
			<td>log_process_attempts</td>
		</tr>
		<tr>
			<td>7</td>
			<td>missing_process_attempts</td>
		</tr>
		<tr>
			<td>8</td>
			<td>log_distinct_processes</td>
		</tr>
		<tr>
			<td>9</td>
			<td>missing_distinct_processes</td>
		</tr>
		<tr>
			<td>10</td>
			<td>log_bottleneck_duration_ms</td>
		</tr>
		<tr>
			<td>11</td>
			<td>missing_bottleneck_duration_ms</td>
		</tr>
		<tr>
			<td>12</td>
			<td>log_orchestration_overhead_ms</td>
		</tr>
		<tr>
			<td>13</td>
			<td>missing_orchestration_overhead_ms</td>
		</tr>
		<tr>
			<td>14</td>
			<td>log_parallelism_ratio</td>
		</tr>
		<tr>
			<td>15</td>
			<td>missing_parallelism_ratio</td>
		</tr>
		<tr>
			<td>16</td>
			<td>hour_sin</td>
		</tr>
		<tr>
			<td>17</td>
			<td>hour_cos</td>
		</tr>
		<tr>
			<td>18</td>
			<td>weekday_sin</td>
		</tr>
		<tr>
			<td>19</td>
			<td>weekday_cos</td>
		</tr>
		<tr>
			<td>20</td>
			<td>rarity_interfaceId</td>
		</tr>
		<tr>
			<td>21</td>
			<td>unseen_interfaceId</td>
		</tr>
		<tr>
			<td>22</td>
			<td>rarity_startChannelId</td>
		</tr>
		<tr>
			<td>23</td>
			<td>unseen_startChannelId</td>
		</tr>
		<tr>
			<td>24</td>
			<td>rarity_endChannelId</td>
		</tr>
		<tr>
			<td>25</td>
			<td>unseen_endChannelId</td>
		</tr>
		<tr>
			<td>26</td>
			<td>rarity_interfaceType</td>
		</tr>
		<tr>
			<td>27</td>
			<td>unseen_interfaceType</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

후보율이 Transaction 모델보다 높지만 성능이 더 좋다는 뜻은 아닙니다. 각 모델의 점수 공간·분포·임계값은 다릅니다. 후보 집단의 실패 비율 96.24%도 precision이 아닙니다. PCA 표본 후보 557건과 전체 시험 후보 8,378건은 다른 집계입니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>Transaction과 Merge는 같은 시험 거래를 사용하므로 공통 키로 후보 교집합·차집합을 비교하는 다음 분석을 수행할 수 있습니다. 현재 노트북에 그 겹침 수는 없으므로 임의로 추정하지 않습니다. 구조 피처 추가의 효과는 공통 정답·동일 경보 예산에서 비교하고 사건 단위로 경보를 묶습니다.
</callout>

---

## 부록. 추가 원본 텍스트 출력

<details>
<summary>Series 집계·실행 규모 등 원본 값 펼치기</summary>
	**원본 텍스트 출력 · 셀 4**
	```text
	통합 Transaction: 2,008,703건 / Process 실행 로그: 3,714,199건
	```

	**원본 텍스트 출력 · 셀 8**
	```text
	최종 거래 스냅샷: 2,008,703건
	최종 스냅샷 선택/결측 ID로 제외된 거래 행: 0건
	Process 결측 거래 ID: 0건
	```

	**원본 텍스트 출력 · 셀 10**
	```text
	source_period      distinct_processes
	25년12월24일-26년4월6일  1.0                     87832
	                   2.0                   1637311
	                   3.0                     34877
	                   4.0                        18
	                   19.0                        1
	26년8월15일-26년9월14일  1.0                    213260
	                   2.0                        38
	                   3.0                        51
	                   4.0                         5
	26년9월30일-26년10월2일  1.0                     27151
	                   2.0                        55
	                   3.0                        48
	Name: transactions, dtype: int64
	```

	**원본 텍스트 출력 · 셀 20**
	```text
	source_period      interfaceId             sequence
	26년9월30일-26년10월2일  IF_TEST_HA_FAILOVER     SERVICE.EXECUTOR         9930
	26년8월15일-26년9월14일  IF_CLAUDE_CREATE_001    SERVICE.EXECUTOR         3997
	                   IF_REST_ERROR_RESPONSE  SERVICE.EXECUTOR         3831
	                   IF_CLAUDE_DELETE_001    SERVICE.EXECUTOR         2161
	25년12월24일-26년4월6일  IF_DB2DB_909            DB.READER → DB.WRITER     307
	                   IF_DB2DB_903            DB.READER → DB.WRITER     296
	                   IF_DB2DB_908            DB.READER → DB.WRITER     291
	                   IF_DB2DB_905            DB.READER → DB.WRITER     288
	                   IF_DB2DB_915            DB.READER → DB.WRITER     288
	                   IF_DB2DB_911            DB.READER → DB.WRITER     283
	                   IF_DB2DB_919            DB.READER → DB.WRITER     282
	                   IF_DB2DB_904            DB.READER → DB.WRITER     281
	                   IF_DB2DB_918            DB.READER → DB.WRITER     279
	                   IF_DB2DB_913            DB.READER → DB.WRITER     274
	                   IF_DB2DB_907            DB.READER → DB.WRITER     271
	                   IF_DB2DB_910            DB.READER → DB.WRITER     269
	                   IF_DB2DB_906            DB.READER → DB.WRITER     267
	                   IF_DB2DB_901            DB.READER → DB.WRITER     266
	                   IF_DB2DB_916            DB.READER → DB.WRITER     266
	                   IF_DB2DB_912            DB.READER → DB.WRITER     260
	                   IF_DB2DB_902            DB.READER → DB.WRITER     260
	                   IF_DB2DB_917            DB.READER → DB.WRITER     260
	                   IF_DB2DB_914            DB.READER → DB.WRITER     251
	                   IF_DB2DB_900            DB.READER → DB.WRITER     212
	                   IF_DB2DB_930            DB.READER → DB.WRITER     176
	Name: sample_transactions, dtype: int64
	```
</details>
