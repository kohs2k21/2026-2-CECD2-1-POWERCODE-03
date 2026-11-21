대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발

<mention-date start="2026-10-06"/> · `rule_and_model_strategy_analysis.ipynb`의 전량 실행 결과

Transaction 2,008,703행 / Process 3,714,199행 · 세 기간 전체 · ESB.SQL 읽기 검증 · 원본 데이터와 기존 분석 노트북 보존

<table_of_contents/>

<callout color="blue_bg">
	**권장 출발점**<br>통합 모델을 비교 기준으로 유지하고, 공통 역할·경로 그룹과 지원되는 업무 전문가를 추가합니다. **새 업무는 통합/공통 fallback으로 시작**하되, 통합 모델도 분포 밖 데이터에 취약할 수 있음을 표시합니다. 전문가 앙상블의 최적성은 아직 검증하지 않았습니다.
</callout>

## 6. 어떤 기준으로 모델을 나눌 것인가

1. **업무별:** Transaction interfaceId, Process processId. 의미가 명확하지만 새 ID마다 콜드스타트.
2. **범주 조합별:** Tx interfaceType+채널 경로, category_top+interfaceType+경로.
   Process adapterType+channelId, 필요하면 허브 추가. 새 업무라도 동일 역할·경로를 공유하면 재사용 가능.
3. **통합:** 공통 행동 수치·시간 피처. 새 업무의 세부 ID에 의존하지 않고 최후 fallback으로 사용.

categoryName의 NONE/MISSING도 유지하지만 정보량을 따로 확인합니다. processCount 같은 숫자나 실행 결과 Status는
기본 범주 라우팅 키로 쓰지 않습니다. 미래에 관측할 종료 피처로 진행 중 탐지를 라우팅하지 않습니다.

**실행 근거: 노트북 셀 33**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>kind</td>
		<td>grouping</td>
		<td>columns</td>
		<td>groups_all</td>
		<td>groups_ready</td>
		<td>train_reference_rows</td>
		<td>test_rows</td>
		<td>test_group_ready_pct</td>
		<td>test_unseen_pct</td>
		<td>train_log_variance_explained</td>
	</tr>
	<tr>
		<td>0</td>
		<td>transaction</td>
		<td>업무</td>
		<td>interfaceId</td>
		<td>406</td>
		<td>20</td>
		<td>1304416</td>
		<td>111303</td>
		<td>0.0000</td>
		<td>99.9398</td>
		<td>0.2581</td>
	</tr>
	<tr>
		<td>1</td>
		<td>transaction</td>
		<td>카테고리</td>
		<td>category_top + interfaceType</td>
		<td>17</td>
		<td>2</td>
		<td>1304416</td>
		<td>111303</td>
		<td>64.4727</td>
		<td>35.4743</td>
		<td>0.1816</td>
	</tr>
	<tr>
		<td>2</td>
		<td>transaction</td>
		<td>카테고리+경로</td>
		<td>category_top + interfaceType + startChannelId ...</td>
		<td>45</td>
		<td>2</td>
		<td>1304416</td>
		<td>111303</td>
		<td>39.9908</td>
		<td>35.4896</td>
		<td>0.1876</td>
	</tr>
	<tr>
		<td>3</td>
		<td>transaction</td>
		<td>경로</td>
		<td>interfaceType + startChannelId + endChannelId</td>
		<td>23</td>
		<td>2</td>
		<td>1304416</td>
		<td>111303</td>
		<td>75.4984</td>
		<td>0.0135</td>
		<td>0.0059</td>
	</tr>
	<tr>
		<td>4</td>
		<td>transaction</td>
		<td>통합</td>
		<td>공통 행동 피처</td>
		<td>1</td>
		<td>1</td>
		<td>1304416</td>
		<td>111303</td>
		<td>100.0000</td>
		<td>0.0000</td>
		<td>0.0000</td>
	</tr>
	<tr>
		<td>5</td>
		<td>process</td>
		<td>업무단계</td>
		<td>processId</td>
		<td>687</td>
		<td>40</td>
		<td>2608471</td>
		<td>111549</td>
		<td>0.0000</td>
		<td>99.8718</td>
		<td>0.9296</td>
	</tr>
	<tr>
		<td>6</td>
		<td>process</td>
		<td>어댑터</td>
		<td>adapterType</td>
		<td>24</td>
		<td>5</td>
		<td>2608471</td>
		<td>111549</td>
		<td>99.8315</td>
		<td>0.0215</td>
		<td>0.9204</td>
	</tr>
	<tr>
		<td>7</td>
		<td>process</td>
		<td>어댑터+채널</td>
		<td>adapterType + channelId</td>
		<td>62</td>
		<td>3</td>
		<td>2608471</td>
		<td>111549</td>
		<td>75.3893</td>
		<td>0.1165</td>
		<td>0.9205</td>
	</tr>
	<tr>
		<td>8</td>
		<td>process</td>
		<td>어댑터+채널+허브</td>
		<td>adapterType + channelId + processHubId</td>
		<td>68</td>
		<td>3</td>
		<td>2608471</td>
		<td>111549</td>
		<td>75.3893</td>
		<td>0.1165</td>
		<td>0.9205</td>
	</tr>
	<tr>
		<td>9</td>
		<td>process</td>
		<td>통합</td>
		<td>공통 행동 피처</td>
		<td>1</td>
		<td>1</td>
		<td>2608471</td>
		<td>111549</td>
		<td>100.0000</td>
		<td>0.0000</td>
		<td>0.0000</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>rows</td>
		<td>train_S</td>
		<td>validation_S</td>
		<td>train_days</td>
		<td>std</td>
		<td>median</td>
		<td>ready</td>
	</tr>
	<tr>
		<td>group</td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
	</tr>
	<tr>
		<td>["IF_CLAUDE_CREATE_001"]</td>
		<td>84366</td>
		<td>0</td>
		<td>35837</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["IF_REST_ERROR_RESPONSE"]</td>
		<td>82586</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_906"]</td>
		<td>50118</td>
		<td>39756</td>
		<td>7549</td>
		<td>6.0</td>
		<td>0.462</td>
		<td>8.730</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_905"]</td>
		<td>49706</td>
		<td>39591</td>
		<td>7707</td>
		<td>6.0</td>
		<td>0.460</td>
		<td>8.750</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_907"]</td>
		<td>49481</td>
		<td>39550</td>
		<td>7702</td>
		<td>6.0</td>
		<td>0.467</td>
		<td>8.735</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_915"]</td>
		<td>49470</td>
		<td>39318</td>
		<td>7683</td>
		<td>6.0</td>
		<td>0.461</td>
		<td>8.752</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_916"]</td>
		<td>49452</td>
		<td>39759</td>
		<td>7547</td>
		<td>6.0</td>
		<td>0.463</td>
		<td>8.735</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_902"]</td>
		<td>49202</td>
		<td>39872</td>
		<td>7396</td>
		<td>6.0</td>
		<td>0.459</td>
		<td>8.733</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_914"]</td>
		<td>48969</td>
		<td>39393</td>
		<td>7708</td>
		<td>6.0</td>
		<td>0.457</td>
		<td>8.747</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_917"]</td>
		<td>48926</td>
		<td>39586</td>
		<td>7697</td>
		<td>6.0</td>
		<td>0.468</td>
		<td>8.735</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_904"]</td>
		<td>48922</td>
		<td>39529</td>
		<td>7700</td>
		<td>6.0</td>
		<td>0.458</td>
		<td>8.742</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_908"]</td>
		<td>48904</td>
		<td>39908</td>
		<td>7520</td>
		<td>6.0</td>
		<td>0.467</td>
		<td>8.726</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_901"]</td>
		<td>48805</td>
		<td>39785</td>
		<td>7236</td>
		<td>7.0</td>
		<td>0.463</td>
		<td>8.731</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_919"]</td>
		<td>48796</td>
		<td>39596</td>
		<td>7701</td>
		<td>6.0</td>
		<td>0.472</td>
		<td>8.741</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["IF_DB2DB_909"]</td>
		<td>48685</td>
		<td>39423</td>
		<td>7714</td>
		<td>6.0</td>
		<td>0.499</td>
		<td>8.737</td>
		<td>True</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>rows</td>
		<td>train_S</td>
		<td>validation_S</td>
		<td>train_days</td>
		<td>std</td>
		<td>median</td>
		<td>ready</td>
	</tr>
	<tr>
		<td>group</td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
		<td></td>
	</tr>
	<tr>
		<td>["DB.READER", "TSC"]</td>
		<td>1638563</td>
		<td>1279522</td>
		<td>145425</td>
		<td>26.0</td>
		<td>0.312</td>
		<td>6.084</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["DB.WRITER", "SOL"]</td>
		<td>1614679</td>
		<td>1279433</td>
		<td>145343</td>
		<td>25.0</td>
		<td>0.417</td>
		<td>8.739</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["SERVICE.EXECUTOR", "HUB"]</td>
		<td>232928</td>
		<td>2595</td>
		<td>71805</td>
		<td>12.0</td>
		<td>1.802</td>
		<td>8.859</td>
		<td>True</td>
	</tr>
	<tr>
		<td>["GATEWAY.INVOKER", "SOL"]</td>
		<td>57253</td>
		<td>0</td>
		<td>0</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["SERVICE.EXECUTOR", "SOL"]</td>
		<td>49981</td>
		<td>45</td>
		<td>8</td>
		<td>8.0</td>
		<td>1.114</td>
		<td>4.663</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["FILE.WRITER", "SOL"]</td>
		<td>25154</td>
		<td>22341</td>
		<td>79</td>
		<td>11.0</td>
		<td>0.231</td>
		<td>6.810</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["FILE.READER", "TSC"]</td>
		<td>25121</td>
		<td>24386</td>
		<td>73</td>
		<td>13.0</td>
		<td>0.776</td>
		<td>6.120</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["DB.EXECUTOR", "SOL"]</td>
		<td>22784</td>
		<td>1</td>
		<td>5</td>
		<td>1.0</td>
		<td>NaN</td>
		<td>5.011</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["DB.WRITER", "TSC"]</td>
		<td>13120</td>
		<td>2</td>
		<td>28</td>
		<td>2.0</td>
		<td>0.441</td>
		<td>5.849</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["QUEUE.SUBSCRIBER", "HUB"]</td>
		<td>13048</td>
		<td>4</td>
		<td>10</td>
		<td>3.0</td>
		<td>1.993</td>
		<td>10.806</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["QUEUE.EXECUTOR", "TSC"]</td>
		<td>13041</td>
		<td>8</td>
		<td>7</td>
		<td>3.0</td>
		<td>2.373</td>
		<td>6.543</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["DB.EXECUTOR", "HUB"]</td>
		<td>4468</td>
		<td>0</td>
		<td>4461</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>NaN</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["DB.READER", "SOL"]</td>
		<td>1231</td>
		<td>32</td>
		<td>103</td>
		<td>11.0</td>
		<td>1.379</td>
		<td>5.371</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["QUEUE.PUBLISHER", "HUB"]</td>
		<td>1145</td>
		<td>2</td>
		<td>2</td>
		<td>2.0</td>
		<td>0.149</td>
		<td>7.239</td>
		<td>False</td>
	</tr>
	<tr>
		<td>["FILE.READER", "SOL"]</td>
		<td>355</td>
		<td>17</td>
		<td>81</td>
		<td>5.0</td>
		<td>0.881</td>
		<td>8.454</td>
		<td>False</td>
	</tr>
</table>

### 6.1 세밀한 모델의 이득과 지원 부족을 같이 보기

`train_log_variance_explained`는 학습 후보의 log1p 지연 분산 중 그룹 간 평균 차이가 설명하는 비중입니다.
높으면 그룹마다 시간 규모가 다르다는 근거입니다. 그룹을 무한히 잘게 나누면 높아지므로 성능 점수로 쓰지 않습니다.

`test_group_ready_pct`는 설정한 학습·검증·관측일 최소량을 충족한 그룹에 속하는 시험 행 비중입니다.
그룹 AI를 학습했다는 뜻도, 정상 검출 정확도도 아닙니다. 시험 표본 자체는 모든 Status를 포함합니다.

`test_unseen_pct`는 해당 그룹의 적격 학습 S 로그가 0인 시험 행 비율입니다. 완전히 새 ID뿐 아니라 과거 관측은 있지만 학습 기준 후보가 없는 그룹도 포함합니다.

**현재 출력에서 읽는 분할 방향:** 업무 ID별 지원 그룹의 시험 커버리지는 Transaction·Process 모두 0%입니다. 새 업무를 처리하려면 fallback이 필수입니다.
Process는 `adapterType`만 나누면 시험 지원율 99.83%, 학습 log-지연 분산 설명 비중 92.04%입니다. 채널을 추가하면 지원율은 75.39%로 내려가고 분산 설명은 거의 늘지 않아, 어댑터별 공통 모델부터 비교하고 채널/업무 전문가를 충분한 근거가 있을 때 추가하는 실험이 합리적입니다.
Transaction 경로 그룹은 지원율 75.50%지만 분산 설명 비중은 약 0.59%입니다. category_top+interfaceType은 약 18.16%를 설명하되 지원율은 64.47%이므로, 경로의 높은 지원율만으로 최적 분할이라고 결정하면 안 됩니다. 두 후보와 통합 모델을 같은 시간 분할에서 비교합니다.
수치는 아래 셀의 default 설정 실행 결과이며, 규칙·분할 설정을 바꾸면 다시 계산해야 합니다.

**실행 근거: 노트북 셀 35**

<image src="file-upload://3f125113-b9ae-8197-9057-00b2da9f15ad"></image>

그림 출처: `rule_and_model_strategy_analysis.ipynb` 셀 35 · 원본 실행 출력. 색/분모/표본 기준은 아래 해석을 함께 확인합니다.

**분할 후보의 실제 지원량**

ready 판정은 학습 S≥500, 검증 S≥100, 학습 관측일≥3의 default입니다. 사용자 설정에 따라 바뀝니다.

**의미:** 범주 조합이 업무 ID보다 신규 그룹 비율을 낮출 수 있지만, 하나의 그룹 안에 서로 다른 실행 모드가 섞일 수 있습니다.

**설계 반영:** 지원량·내부 지연 분산·운영 의미를 함께 보고 그룹을 승인합니다. 현 자료에서 가장 정확한 모델은 아직 결정할 수 없습니다.

### 6.2 ready 기준 민감도: 최소량을 작게 잡는다고 충분한 모델이 되는 것은 아님

**실행 근거: 노트북 셀 37**

<details>
<summary>셀 37 · 근거 표 1 상세</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>kind</td>
			<td>grouping</td>
			<td>train_min</td>
			<td>validation_min</td>
			<td>days_min</td>
			<td>ready_groups</td>
			<td>groups</td>
		</tr>
		<tr>
			<td>0</td>
			<td>Transaction</td>
			<td>업무</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>20</td>
			<td>406</td>
		</tr>
		<tr>
			<td>1</td>
			<td>Transaction</td>
			<td>업무</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>20</td>
			<td>406</td>
		</tr>
		<tr>
			<td>2</td>
			<td>Transaction</td>
			<td>업무</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>20</td>
			<td>406</td>
		</tr>
		<tr>
			<td>3</td>
			<td>Transaction</td>
			<td>카테고리</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>2</td>
			<td>17</td>
		</tr>
		<tr>
			<td>4</td>
			<td>Transaction</td>
			<td>카테고리</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>2</td>
			<td>17</td>
		</tr>
		<tr>
			<td>5</td>
			<td>Transaction</td>
			<td>카테고리</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>2</td>
			<td>17</td>
		</tr>
		<tr>
			<td>6</td>
			<td>Transaction</td>
			<td>카테고리+경로</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>3</td>
			<td>45</td>
		</tr>
		<tr>
			<td>7</td>
			<td>Transaction</td>
			<td>카테고리+경로</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>2</td>
			<td>45</td>
		</tr>
		<tr>
			<td>8</td>
			<td>Transaction</td>
			<td>카테고리+경로</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>2</td>
			<td>45</td>
		</tr>
		<tr>
			<td>9</td>
			<td>Transaction</td>
			<td>경로</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>2</td>
			<td>23</td>
		</tr>
		<tr>
			<td>10</td>
			<td>Transaction</td>
			<td>경로</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>2</td>
			<td>23</td>
		</tr>
		<tr>
			<td>11</td>
			<td>Transaction</td>
			<td>경로</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>2</td>
			<td>23</td>
		</tr>
		<tr>
			<td>12</td>
			<td>Transaction</td>
			<td>통합</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>13</td>
			<td>Transaction</td>
			<td>통합</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>14</td>
			<td>Transaction</td>
			<td>통합</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>15</td>
			<td>Process</td>
			<td>업무단계</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>40</td>
			<td>687</td>
		</tr>
		<tr>
			<td>16</td>
			<td>Process</td>
			<td>업무단계</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>40</td>
			<td>687</td>
		</tr>
		<tr>
			<td>17</td>
			<td>Process</td>
			<td>업무단계</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>40</td>
			<td>687</td>
		</tr>
		<tr>
			<td>18</td>
			<td>Process</td>
			<td>어댑터</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>5</td>
			<td>24</td>
		</tr>
		<tr>
			<td>19</td>
			<td>Process</td>
			<td>어댑터</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>5</td>
			<td>24</td>
		</tr>
		<tr>
			<td>20</td>
			<td>Process</td>
			<td>어댑터</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>3</td>
			<td>24</td>
		</tr>
		<tr>
			<td>21</td>
			<td>Process</td>
			<td>어댑터+채널</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>5</td>
			<td>62</td>
		</tr>
		<tr>
			<td>22</td>
			<td>Process</td>
			<td>어댑터+채널</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>3</td>
			<td>62</td>
		</tr>
		<tr>
			<td>23</td>
			<td>Process</td>
			<td>어댑터+채널</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>3</td>
			<td>62</td>
		</tr>
		<tr>
			<td>24</td>
			<td>Process</td>
			<td>어댑터+채널+허브</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>3</td>
			<td>68</td>
		</tr>
		<tr>
			<td>25</td>
			<td>Process</td>
			<td>어댑터+채널+허브</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>3</td>
			<td>68</td>
		</tr>
		<tr>
			<td>26</td>
			<td>Process</td>
			<td>어댑터+채널+허브</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>3</td>
			<td>68</td>
		</tr>
		<tr>
			<td>27</td>
			<td>Process</td>
			<td>통합</td>
			<td>100</td>
			<td>30</td>
			<td>2</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>28</td>
			<td>Process</td>
			<td>통합</td>
			<td>500</td>
			<td>100</td>
			<td>3</td>
			<td>1</td>
			<td>1</td>
		</tr>
		<tr>
			<td>29</td>
			<td>Process</td>
			<td>통합</td>
			<td>2000</td>
			<td>300</td>
			<td>5</td>
			<td>1</td>
			<td>1</td>
		</tr>
	</table>
</details>

## 7. 권장 구현: 계층 fallback + 검증된 전문가 앙상블

**규칙 평가 → (격리 / 결과 알림 / 품질 검토를 보존) → 유효 로그의 모델 채점** 순서입니다.
학습 제외와 채점 제외는 다릅니다. F/H는 규칙 경보가 이미 있어도 모델 설명을 얻기 위해 유효할 때 채점할 수 있습니다.

### 7.1 모델별 역할

| 모델 | 기본 분할 키 | 공통 입력 후보 | 적용 조건 |
|---|---|---|---|
| 업무 전문가 | Tx interfaceId / Process processId | 지연·처리량·시간·병목/누락 피처 | 학습량·기간·검증량과 운영 검토 통과 |
| 공통 전문가 | Tx category_top+interfaceType 또는 interfaceType+시작/종료 채널 비교 / Process adapterType 우선, 채널 조합은 추가 후보 | 동일 역할의 행동 수치 | 같은 범주의 신규 업무에도 fallback; 후보별 지원량과 검증 비교 |
| 통합 모델 | 전체 유효 업무 | 공통 행동 피처·결측 플래그·주기 피처 | 모든 업무의 비교 기준과 최후 fallback |

통합 모델의 콜드스타트 버전에서는 interfaceId/processId의 희귀도만으로 새 업무를 장애라고 판정하지 않습니다.
학습에서 항상 0인 unseen 플래그는 트리 모델이 새 업무의 의미를 학습했다는 증거가 아니므로 별도 라우팅 메타데이터로 관리합니다.

### 7.2 콜드스타트 단계

1. **신규 ID:** 업무 전문가 가중치=0. 지원되는 공통 전문가와 통합 모델로 채점하고 `new_business=True`를 별도 표시합니다.
2. **범주도 신규:** 통합 모델+불변 규칙+승인 SLA로 시작합니다. 통합 모델도 OOD/분포 외삽일 수 있어 기준선 부족 상태를 노출합니다.
3. **이력 축적:** 시간에 따라 별도의 학습/검증을 확보합니다. 첫 관측을 즉시 정상 학습에 넣지 않습니다.
4. **shadow 검증:** 새 업무 모델의 경보량·실패/성공 교차·실제 사건 검토를 통합/공통 모델과 비교합니다.
5. **승격:** 지원량·검증·드리프트 확인 후 업무 전문가를 활성화합니다. 이력이 있어도 검증이 없으면 자동 승격하지 않습니다.

### 7.3 앙상블: raw 점수를 그대로 더하지 않기

모델마다 점수 척도가 다르므로 각 모델의 **과거 별도 calibration 구간 점수 분포**에서 percentile로 변환한 뒤 결합합니다.
percentile은 이상 확률이 아닙니다. IF/LOF/딥러닝의 raw 점수 평균은 의미가 일치하지 않을 수 있습니다.

예시: `score = w_business × rank_business + w_common × rank_common + w_global × rank_global`.
가용 모델만 가중치를 다시 정규화합니다. 신규 업무에서는 w_business=0입니다.
가중치는 데이터량만으로 확정하지 않고 검증 안정성·운영 검토로 결정합니다.
최종 결합 임계값은 또 다른 시간 holdout에서 정해야 하므로 여기서는 임의 가중치의 성능을 주장하지 않습니다.

하드 규칙을 만족한 경보는 낮은 모델 점수 때문에 취소하지 않습니다. 반대로 통계 후보를 하드 차단으로 승격하지 않습니다.

**실행 근거: 노트북 셀 39**

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>kind</td>
		<td>grouping</td>
		<td>groups_ready</td>
		<td>test_group_ready_pct</td>
		<td>test_unseen_pct</td>
	</tr>
	<tr>
		<td>0</td>
		<td>transaction</td>
		<td>업무</td>
		<td>20</td>
		<td>0.000</td>
		<td>99.940</td>
	</tr>
	<tr>
		<td>1</td>
		<td>transaction</td>
		<td>카테고리</td>
		<td>2</td>
		<td>64.473</td>
		<td>35.474</td>
	</tr>
	<tr>
		<td>2</td>
		<td>transaction</td>
		<td>카테고리+경로</td>
		<td>2</td>
		<td>39.991</td>
		<td>35.490</td>
	</tr>
	<tr>
		<td>3</td>
		<td>transaction</td>
		<td>경로</td>
		<td>2</td>
		<td>75.498</td>
		<td>0.013</td>
	</tr>
	<tr>
		<td>4</td>
		<td>transaction</td>
		<td>통합</td>
		<td>1</td>
		<td>100.000</td>
		<td>0.000</td>
	</tr>
	<tr>
		<td>5</td>
		<td>process</td>
		<td>업무단계</td>
		<td>40</td>
		<td>0.000</td>
		<td>99.872</td>
	</tr>
	<tr>
		<td>6</td>
		<td>process</td>
		<td>어댑터</td>
		<td>5</td>
		<td>99.831</td>
		<td>0.022</td>
	</tr>
	<tr>
		<td>7</td>
		<td>process</td>
		<td>어댑터+채널</td>
		<td>3</td>
		<td>75.389</td>
		<td>0.117</td>
	</tr>
	<tr>
		<td>8</td>
		<td>process</td>
		<td>어댑터+채널+허브</td>
		<td>3</td>
		<td>75.389</td>
		<td>0.117</td>
	</tr>
	<tr>
		<td>9</td>
		<td>process</td>
		<td>통합</td>
		<td>1</td>
		<td>100.000</td>
		<td>0.000</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td></td>
		<td>상황</td>
		<td>사용 모델/경로</td>
		<td>결합</td>
		<td>주의</td>
	</tr>
	<tr>
		<td>0</td>
		<td>기존 업무·검증 전문가 있음</td>
		<td>업무 + 공통 + 통합</td>
		<td>calibrated ensemble</td>
		<td>업무 모델 우선, 통합도 함께 비교</td>
	</tr>
	<tr>
		<td>1</td>
		<td>새 업무·공통 그룹 지원됨</td>
		<td>공통 + 통합</td>
		<td>business weight=0</td>
		<td>신규 업무 표시는 이상 정답 아님</td>
	</tr>
	<tr>
		<td>2</td>
		<td>새 업무·새 범주</td>
		<td>통합 + 불변/승인 정책 규칙</td>
		<td>OOD / 기준선 부족 표시</td>
		<td>전문가를 새로 학습한 것처럼 처리하지 않음</td>
	</tr>
	<tr>
		<td>3</td>
		<td>키/시각/상태 사전 위반</td>
		<td>격리 + 품질 경보</td>
		<td>모델 입력 제한</td>
		<td>원본 삭제 금지</td>
	</tr>
</table>

### 7.4 추후 클러스터링·딥러닝 확장

- **클러스터링:** 학습 구간의 공통 행동 수치를 log/강건 스케일링한 뒤 반복 실행 모드를 나눕니다. Status를 클러스터 정답으로 쓰지 않습니다. 신규 로그는 기존 군집과의 거리/지원량을 확인하고 멀면 unknown으로 보류합니다. 새 데이터마다 전체 군집을 재계산하지 않습니다.
- **딥러닝:** 오토인코더/Deep SVDD 같은 공통 표현 기반 탐지를 비교할 수 있습니다. 데이터가 많다는 이유만으로 초기 IF보다 유리하다고 단정하지 않습니다. 초반에는 수집 품질·그룹 의미·평가 라벨 확보가 우선입니다.
- **실시간:** 완료 후 duration/병목 피처와 진행 중 elapsed/과거 창 피처의 모델을 분리합니다. endTime과 최종 Status를 미래 시점에 사용하는 실험은 실시간 성능이 아닙니다.

방법 참고: [scikit-learn의 outlier/novelty detection 구분](https://scikit-learn.org/stable/modules/outlier_detection.html),
[Deep One-Class Classification 원 논문](https://proceedings.mlr.press/v80/ruff18a.html).
공통 fallback·가중치·최소 지원량은 이 데이터에 대한 설계 제안이며 위 자료가 최적성을 보장하지 않습니다.

## 8. 구현 순서와 승인할 규칙

1. **지금 적용 가능한 입력 검증:** SQL 키 결측/충돌, 미정의 상태, 음수 시각 차이, 카운트 도메인 위반. 시계/단위/정수 계약도 문서화합니다.
2. **사용자 설정으로 적용:** F/H 결과 알림, 특정 업무·패턴·시간대·SLA. 금지 목록과 스케줄은 실제 운영자가 제공해야 합니다.
3. **계약 확인 후 강화:** count 보존·상태 전파·선행 관계·거래 시간 경계. 조건부 실행/미처리/부분성공/수집 지연을 구분합니다.
4. **통계 검토:** 학습 구간 p99/p99.9 고정 기준을 배포 버전으로 관리합니다. 입력 SLA 없이 ‘상위 0.1%=확정 장애’라고 하지 않습니다.
5. **모델 비교:** 통합 → 공통 범주 → 지원되는 업무 모델 순으로 개발하고 시간 holdout 및 새 업무 holdout에서 비교합니다.

평가에는 규칙/모델 후보뿐 아니라 비후보 표본도 포함합니다. confirmed labels가 생기면 precision/recall,
사건별 탐지 지연, 경보 수/시간, 업무별 커버리지, 신규 업무 오탐을 함께 측정합니다.

**이 노트북의 결과:** 실제 전량 규칙 건수, Status별 지연/결측, 같은 업무 S/F 비교,
과거 고정 분위수 시험 후보, 그룹 지원량/콜드스타트 근거. **미실행:** 새로운 그룹 AI 학습, 앙상블 성능, 클러스터링/딥러닝 성능.

## 콜드스타트 비율의 정확한 정의

표의 `test_unseen_pct`는 **해당 그룹의 적격 학습 S 로그가 0인 시험 행 비율**입니다. 완전히 새 ID뿐 아니라 초기 관측은 있었으나 정상 기준 후보가 없는 그룹도 포함합니다. 과거 ‘초기 기간에서 못 본 interfaceId 행 비중’과 분모·기준이 다릅니다.

## 3단계 비교 실험 설계

<table fit-page-width="true" header-row="true">
	<tr>
		<td>실험</td>
		<td>모델/라우팅</td>
		<td>공정한 비교 조건</td>
		<td>판단할 결과</td>
	</tr>
	<tr>
		<td>A</td>
		<td>통합 행동 모델</td>
		<td>시간 분리·같은 규칙/피처 가용 시점</td>
		<td>기준 경보량·업무별 커버리지</td>
	</tr>
	<tr>
		<td>B</td>
		<td>업무 / 범주 조합 모델 + 미지원 fallback</td>
		<td>학습·검증·최소일수 gate를 학습/검증에서 결정</td>
		<td>그룹 지원량·신규 업무 검토량</td>
	</tr>
	<tr>
		<td>C</td>
		<td>B + 통합 모델의 calibrated ensemble</td>
		<td>서로 다른 점수를 과거 calibration rank로 변환</td>
		<td>검증된 사건 검출·경보 중복·드리프트 민감도</td>
	</tr>
</table>

현재 노트북은 이 실험을 위한 데이터 분할/지원량 근거와 규칙 평가를 실행했습니다. A/B/C의 새 AI 모델은 아직 학습하지 않았고, 클러스터링·딥러닝 성능도 측정하지 않았습니다.
