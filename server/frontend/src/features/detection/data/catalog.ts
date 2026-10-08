import type { FeatureDefinition, RawSource, ValueType } from "./types";
const rawNames: Record<RawSource, string[]> = {
  transaction: [
    "TRANSACTION_ID",
    "INTERFACE_ID",
    "PROCESS_HUB_ID",
    "START_CHANNEL_ID",
    "END_CHANNEL_ID",
    "PROCESS_COUNT",
    "STATUS",
    "RESPONSE_CODE",
    "RESPONSE_MESSAGE",
    "INTERFACE_TYPE",
    "CATEGORY_NAME",
    "START_TIME",
    "END_TIME",
    "PROCESS_TIME",
    "RETRY_COUNT",
  ],
  process: [
    "TRANSACTION_ID",
    "PROCESS_ID",
    "DEPEND_PROCESS_ID",
    "PROCESS_HUB_ID",
    "ADAPTER_TYPE",
    "CHANNEL_ID",
    "STATUS",
    "START_TIME",
    "END_TIME",
    "SUCCESS_COUNT",
    "ERROR_COUNT",
    "RESPONSE_CODE",
    "RESPONSE_MESSAGE",
    "TOTAL_COUNT",
    "RETRY_COUNT",
  ],
};
const rawType = (name: string): ValueType =>
  name.endsWith("_TIME") && name !== "PROCESS_TIME"
    ? "timestamp"
    : name.endsWith("_COUNT") || name === "PROCESS_TIME"
      ? "number"
      : name.endsWith("_ID")
        ? "identifier"
        : name === "RESPONSE_MESSAGE"
          ? "text"
          : "category";
export const rawFeatures: FeatureDefinition[] = Object.entries(
  rawNames,
).flatMap(([source, names]) =>
  names.map((name) => {
    const type = rawType(name);
    const supported = type === "number";
    return {
      id: `${source}.${name}`,
      name,
      source: source as RawSource,
      type,
      unit:
        type === "timestamp"
          ? "시간대 확인 필요"
          : name === "PROCESS_TIME"
            ? "원천 단위 확인 필요"
            : type === "number"
              ? "건"
              : "—",
      expression: `${source}.${name}`,
      inputs: [],
      readiness: supported ? "ready" : "unsupported",
      reason: supported
        ? "수치 입력; 데이터셋별 범위·결측 검증 필요"
        : type === "identifier"
          ? "문자열 ID 보존; 숫자형 직접 입력 제외"
          : type === "text"
            ? "자유 텍스트 직접 입력 및 원문 미리보기 제외"
            : "전처리 또는 범주 어댑터 설정 필요",
      defaultSelected: true,
      aliases: [],
      fitRequired: false,
      collected: null,
      missingRate: null,
      distinctCount: null,
      availableAt:
        source === "transaction"
          ? "PROCESS 판정 시점의 부모 값 가용성 확인 필요"
          : name === "END_TIME" || type === "number"
            ? "완료 PROCESS"
            : "PROCESS 시작",
    } satisfies FeatureDefinition;
  }),
);
type CandidateSpec = [string, string, string, string[], boolean, string];
const candidates: CandidateSpec[] = [
  [
    "F01",
    "startTimeHourSin",
    "sin(2πh/24)",
    ["START_TIME"],
    false,
    "시간대·정수/소수시 기준 확인",
  ],
  [
    "F02",
    "startTimeHourCos",
    "cos(2πh/24)",
    ["START_TIME"],
    false,
    "시간 인코딩 쌍·시간대 기준 필요",
  ],
  [
    "F03",
    "startTimeIsWeekend",
    "START_TIME의 토·일 여부",
    ["START_TIME"],
    false,
    "지역·시간대 확인",
  ],
  [
    "F04",
    "startHourMeanRowCountLog1p",
    "log1p(시간대별 학습 평균 저장행수)",
    ["START_TIME"],
    true,
    "집계 범위·분모·수집 공백 처리 기준 필요",
  ],
  [
    "F05",
    "successRatePerTotal",
    "SUCCESS_COUNT / TOTAL_COUNT",
    ["SUCCESS_COUNT", "TOTAL_COUNT"],
    false,
    "분모>0·동일 단위, 초과값 clip 금지",
  ],
  [
    "F06",
    "errorRatePerTotal",
    "ERROR_COUNT / TOTAL_COUNT",
    ["ERROR_COUNT", "TOTAL_COUNT"],
    false,
    "분모>0·동일 단위",
  ],
  [
    "F07",
    "totalCountMinusSuccessErrorCount",
    "TOTAL_COUNT - (SUCCESS_COUNT + ERROR_COUNT)",
    ["TOTAL_COUNT", "SUCCESS_COUNT", "ERROR_COUNT"],
    false,
    "합계 계약·음수 원천값 검증 필요",
  ],
  [
    "F08",
    "countMismatchFlag",
    "F07 != 0",
    ["TOTAL_COUNT", "SUCCESS_COUNT", "ERROR_COUNT"],
    false,
    "결측은 NULL, 수치 비교 오차 기준 확인",
  ],
  [
    "F09",
    "hasError",
    "ERROR_COUNT > 0",
    ["ERROR_COUNT"],
    false,
    "결측·음수를 false로 간주 금지",
  ],
  [
    "F10",
    "successCountIsZeroOrMissing",
    "SUCCESS_COUNT가 0 또는 원천 NULL",
    ["SUCCESS_COUNT"],
    false,
    "컬럼 미수집과 원천 NULL 구분",
  ],
  [
    "F11",
    "totalCountIsZeroOrMissing",
    "TOTAL_COUNT가 0 또는 원천 NULL",
    ["TOTAL_COUNT"],
    false,
    "0/NULL/미수집 구분",
  ],
  [
    "F12",
    "durationLog1p",
    "log1p(durationMs / 1ms)",
    ["START_TIME", "END_TIME"],
    false,
    "기존 시간 단위·음수 정책 확인",
  ],
  [
    "F13",
    "endTimeIsMissing",
    "END_TIME의 원천 NULL 여부",
    ["END_TIME"],
    false,
    "판정 시점·수집 누락 구분",
  ],
  [
    "F14",
    "isRootProcess",
    "검증된 의존 프로세스 없음",
    ["DEPEND_PROCESS_ID"],
    false,
    "의존관계의 NULL·빈값 처리 기준 필요",
  ],
  [
    "F15",
    "step",
    "PROCESS_ID 승인된 접미 패턴 추출",
    ["PROCESS_ID"],
    false,
    "프로세스 단계 추출 기준 필요; 문자열 보존",
  ],
  [
    "F16",
    "totalCountRobustZByStartHour",
    "RZ_hour(TOTAL_COUNT)",
    ["START_TIME", "TOTAL_COUNT"],
    true,
    "train 중앙값·MAD, 최소 표본·MAD=0 정책 필요",
  ],
  [
    "F17",
    "errorCountRobustZByStartHour",
    "RZ_hour(ERROR_COUNT)",
    ["START_TIME", "ERROR_COUNT"],
    true,
    "train 중앙값·MAD·0분산 정책 필요",
  ],
  [
    "F18",
    "totalCountRobustZByStep",
    "RZ_step(TOTAL_COUNT)",
    ["PROCESS_ID", "TOTAL_COUNT"],
    true,
    "step·새 범주·표본·단위 확인",
  ],
  [
    "F19",
    "errorCountRobustZByStep",
    "RZ_step(ERROR_COUNT)",
    ["PROCESS_ID", "ERROR_COUNT"],
    true,
    "step·0분산 처리 확인",
  ],
  [
    "F20",
    "stepDurationLog1pMedian",
    "median_train,step(log1p(durationMs / 1ms))",
    ["PROCESS_ID", "START_TIME", "END_TIME"],
    true,
    "로그 변환·중앙값 처리 순서 확인 필요",
  ],
  [
    "F21",
    "durationDiffStepMedian",
    "durationMs - median_train,step(durationMs)",
    ["PROCESS_ID", "START_TIME", "END_TIME"],
    true,
    "처리시간 기준 데이터 정의 필요",
  ],
  [
    "F22",
    "durationRobustZByStep",
    "RZ_step(durationMs)",
    ["PROCESS_ID", "START_TIME", "END_TIME"],
    true,
    "처리시간 변환·편차 정규화 기준 필요",
  ],
  [
    "F23",
    "countLog1pError",
    "log1p(ERROR_COUNT)",
    ["ERROR_COUNT"],
    false,
    "비음수·단위 검증",
  ],
  [
    "F24",
    "countLog1pTotal",
    "log1p(TOTAL_COUNT)",
    ["TOTAL_COUNT"],
    false,
    "비음수·단위 검증",
  ],
  [
    "A00",
    "durationMs",
    "END_TIME - START_TIME (ms)",
    ["START_TIME", "END_TIME"],
    false,
    "동일 시간대·유효 시각; 음수 계산 보류",
  ],
  [
    "A01",
    "startTimeMonth",
    "START_TIME의 월",
    ["START_TIME"],
    false,
    "월 기준·시간대 확인 필요",
  ],
  [
    "A02",
    "startTimeDayOfWeek",
    "START_TIME의 요일",
    ["START_TIME"],
    false,
    "요일 기준·시간대 확인 필요",
  ],
  [
    "A03",
    "startTimeWeekOfYear",
    "ISO 주차 및 주차 연도",
    ["START_TIME"],
    false,
    "주차·주차 연도·시간대 기준 필요",
  ],
  [
    "A04",
    "startTimeWeekOfMonth",
    "월내 주차",
    ["START_TIME"],
    false,
    "주 시작일·월내 주차 기준 필요",
  ],
  [
    "A05",
    "startTimeHour",
    "START_TIME의 시간",
    ["START_TIME"],
    false,
    "시간대 확인",
  ],
  [
    "A06",
    "startTimeIsHoliday",
    "공휴일 달력 lookup",
    ["START_TIME"],
    false,
    "국가·지역·달력 버전·지원 기간 필요",
  ],
  [
    "A07",
    "hasDependencyProcess",
    "의존 ID 존재와 검증된 관계 구분",
    ["DEPEND_PROCESS_ID"],
    false,
    "의존 ID와 실제 의존관계 구분 필요",
  ],
  [
    "A08",
    "durationMedianByAdapterType",
    "median_train,adapter(durationMs)",
    ["ADAPTER_TYPE", "START_TIME", "END_TIME"],
    true,
    "기준 데이터·단위 확인 필요",
  ],
  [
    "A09",
    "durationMedianByProcessHubId",
    "median_train,hub(durationMs)",
    ["PROCESS_HUB_ID", "START_TIME", "END_TIME"],
    true,
    "기준 데이터·단위 확인 필요",
  ],
  [
    "A10",
    "durationMedianByChannelId",
    "median_train,channel(durationMs)",
    ["CHANNEL_ID", "START_TIME", "END_TIME"],
    true,
    "기준 데이터·단위 확인 필요",
  ],
  [
    "A11",
    "durationMedianByStep",
    "median_train,step(durationMs)",
    ["PROCESS_ID", "START_TIME", "END_TIME"],
    true,
    "처리시간 기준 데이터 정의 필요",
  ],
  [
    "A12",
    "countLog1pSuccess",
    "log1p(SUCCESS_COUNT)",
    ["SUCCESS_COUNT"],
    false,
    "비음수·단위 검증",
  ],
  [
    "A13",
    "frequencyEncode(field)",
    "train category 빈도 또는 비율",
    ["ADAPTER_TYPE"],
    true,
    "분모·missing/unknown·smoothing 정책 필요",
  ],
  [
    "A14",
    "endMinusErrorTimeLog1p",
    "log1p(END_TIME - ERROR_TIME)",
    ["END_TIME", "ERROR_TIME"],
    false,
    "필수 원천 속성 ERROR_TIME 없음 · 계산 보류",
  ],
];
const aliases: Record<string, string[]> = {
  F03: ["isWeekend"],
  F07: ["totalCountMinusSuccessErrrorCount"],
  F08: ["countMissMatchFlag"],
  F17: ["errorCountRobusZByStartHour"],
  F18: ["totalCountRobustByStep"],
  F19: ["errorCountRobustByStep"],
};
export const derivedFeatures: FeatureDefinition[] = candidates.map(
  ([id, name, expression, inputs, fitRequired, reason]) => ({
    id,
    name,
    source: "derived",
    type: "number",
    unit:
      name.includes("duration") &&
      !name.includes("Log") &&
      !name.includes("Robust")
        ? "ms"
        : "—",
    expression,
    inputs: inputs.map((input) => `process.${input}`),
    readiness:
      id === "A14"
        ? "deferred"
        : fitRequired
          ? "fit-required"
          : ["F14", "F15", "A04", "A06", "A07"].includes(id)
            ? "unconfirmed"
            : "ready",
    reason,
    defaultSelected: id !== "A14",
    aliases: aliases[id] ?? [],
    fitRequired,
    collected: null,
    missingRate: null,
    distinctCount: null,
    availableAt: "의존 원천값 가용 시점 검증 필요",
  }),
);
export const featureCatalog = [...rawFeatures, ...derivedFeatures];
