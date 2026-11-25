/**
 * AI 판정 근거 요약(evidenceSummary) 문장 틀·문구 — 화면에 보이는 글자는 모두 이 파일에만 둔다.
 * 문구·틀을 바꾸면 TEMPLATE_VERSION을 올린다(설명 작업의 다듬기 결과는 같은 버전일 때만 쓰인다).
 * 조립 순서·값 고르기는 evidenceSummary.ts, 값 표기·조사는 koreanText.ts.
 * 문장 틀 인자는 이미 표기된 문자열(천 단위 쉼표·단위 붙임 완료)이고, 조사는 여기서 붙인다.
 * v2 문장 모양: `{방법 이름}은/는 {관측 사실}{이유 어미} {판정}으로 판단했습니다.` — 비교 대상은 사실 안에 넣고(`기준 3,000 ms를 넘어`),
 * `~와 비교했고,` 구조·`이번`·`값` 같은 군말을 쓰지 않는다. 확인할 것은 끝에 실제 화면 칸 안내 `(화면: …)`를 붙인다.
 */
import { because, object, subject, toward, topic, withWord } from "./koreanText.js";

export const TEMPLATE_VERSION = "evidence-template-v2";
/** 블록 5 고정 안내(사용자 확정 글자). */
export const NOTICE = "AI 근거 요약은 참고용이며, 최종 판단은 담당자 확인이 필요합니다.";
/** 값을 구할 수 없는 자리(표·문장 공통). 값을 지어내지 않는다. */
export const NO_VALUE = "—";
/** 블록 2에 넣는 근거 방법 문장 최대 수(미판정·넘침 문장 제외). */
export const MAX_METHOD_SENTENCES = 3;
/** 블록 4 최대 줄 수. */
export const MAX_CHECKS = 2;

// ---- 블록 1 앞말 ----
export const RISK_PREFIX: Record<string, string> = { critical: "위험", warning: "주의", info: "참고" };
export const GRADE_PREFIX: Record<string, string> = { anomaly_sign: "이상 징후", anomaly: "이상" };
export const PREFIX_UNRATED = "위험도 미정";
export const PREFIX_NORMAL = "이상 신호 없음";
export const PREFIX_UNCONFIRMED = "정상 여부 미확인";

/**
 * 블록 2 판정 낱말 어휘(긴 것 먼저). stateWords = 문장에서 원천 값·시각·이름표를 가린 뒤 이 어휘가 나온 순서열(중복 포함).
 * 가리는 이름표 = FIELD_LABELS 이름 + STATUS_LABELS 표기 + MASKED_NAMES(`이상 규칙`의 `이상`은 판정 낱말이 아니다).
 */
export const STATE_WORDS = ["이상 신호 없음", "정상 여부 미확인", "이상 징후", "평가 불가", "이상", "정상"] as const;
/** 원천 값 목록(valueTokens) 상한: 개수·글자 수. 넘는 값은 담지 않는다. */
export const VALUE_TOKEN_LIMIT = 16;
export const VALUE_TOKEN_MAX_LENGTH = 120;

// ---- 이름표 ----
/**
 * 변수 이름표. name = 문단·확인할 것·표 이름(원천 열이 있는 건수 계열은 `한국어(원본 열 이름)`), short = 한 줄 요약 이름(한국어만),
 * unitUnknown = 건수 계열(단위 미확인, 문단에는 숫자를 쓰지 않음), unit = 값 뒤 단위(한 칸 띄움).
 */
export type FieldLabel = { name: string; short: string; unitUnknown: boolean; unit: string | null };
export const FIELD_LABELS: Record<string, FieldLabel> = {
  total_count: { name: "처리량(TOTAL_COUNT)", short: "처리량", unitUnknown: true, unit: null },
  success_count: { name: "성공 건수(SUCCESS_COUNT)", short: "성공 건수", unitUnknown: true, unit: null },
  error_count: { name: "오류 건수(ERROR_COUNT)", short: "오류 건수", unitUnknown: true, unit: null },
  retry_count: { name: "재시도 횟수(RETRY_COUNT)", short: "재시도 횟수", unitUnknown: true, unit: null },
  elapsed_ms: { name: "처리 시간", short: "처리 시간", unitUnknown: false, unit: "ms" },
  error_ratio: { name: "오류 비율", short: "오류 비율", unitUnknown: false, unit: null },
};
const F = FIELD_LABELS;
/** 처리 상태 코드 뜻(화면 `statusCodeLabels`와 같은 값). 표기 = `F(실패)`, 목록 밖 코드는 코드만. */
export const STATUS_MEANINGS: Record<string, string> = { S: "성공", F: "실패", P: "처리중", N: "미처리", H: "부분성공", Z: "데이터없음" };
export const statusText = (code: string) => (Object.hasOwn(STATUS_MEANINGS, code) ? `${code}(${STATUS_MEANINGS[code]})` : code);
/** 판정 낱말을 뽑기 전에 가리는 상태 표기(`S(성공)` … `Z(데이터없음)`). */
export const STATUS_LABELS = Object.keys(STATUS_MEANINGS).map(statusText);
const SUCCESS = statusText("S");
const FAILED = statusText("F");
/** 방법 표시 이름(문장 주어·미판정 문장). */
export const METHOD_NAMES = { hard: "이상 규칙", relation: "관계 규칙", soft: "이상 징후 규칙", ml: "머신러닝 모델" } as const;
/** 판정 낱말을 뽑기 전에 가리는 이름(방법 표시 이름). */
export const MASKED_NAMES = ["이상 규칙", "이상 징후 규칙", "관계 규칙", "머신러닝 모델", "정상 프로토콜"] as const;
/** 표 이번 값 칸의 이름(값 앞에 붙는 이름). */
export const OBSERVED_NAMES = { status: "상태", totalCount: F.total_count.name, successCount: F.success_count.name, errorCount: F.error_count.name } as const;
/** 표 결과 칸. */
export const RESULT_WORDS = { anomaly: "이상", sign: "이상 징후", unresolved: "평가 불가" } as const;
/** 소프트룰 비교 기호(표 조건 칸·대체 문장). 문장 비교 낱말은 SOFT_COMPARE. */
export const OPERATOR_SIGNS: Record<string, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
/** scale 1 구성원(학습 중앙값이 여러 어댑터가 섞인 값)의 평소 값 칸 글자(숫자 대신). 값 자체가 없는 NO_VALUE와 구분된다. */
export const SCALE_UNIT_USUAL = "평소와 다름";
/** ML 표가 비었을 때 표 아래 한 줄(기여도 상태 낱말). */
export const CONTRIBUTION_NOTES = { absent: "기여도 미계산", skipped: "기여도 계산 안 함", failed: "기여도 계산 실패", unreadable: "기여도 표시 불가" } as const;
/** 응답 코드 뜻을 확인할 것 줄에 쓸 때 상한(코드 포인트). 넘으면 앞 글자만 + `…`(표 칸에는 전체). */
export const RESPONSE_MEANING_CHECK_LIMIT = 40;
const ELLIPSIS = "…";
export const shortMeaning = (meaning: string) => {
  const chars = [...meaning];
  return chars.length > RESPONSE_MEANING_CHECK_LIMIT ? `${chars.slice(0, RESPONSE_MEANING_CHECK_LIMIT).join("")}${ELLIPSIS}` : meaning;
};
/** 응답 코드 + 사전 뜻 표기(`E101(뜻)`). */
export const codeWithMeaning = (code: string, meaning: string) => `${code}(${meaning})`;

/** 문장 안 `{낱말} {값}` 자리: 값이 없으면 낱말만 두어 조사가 낱말에 붙는다(`정해진 앞 단계의 기록이`). 한 줄 요약·표·확인할 것은 값 없음 글자 그대로. */
const slot = (noun: string, value: string) => (value === NO_VALUE ? noun : `${noun} ${value}`);
/** 확인할 것 끝 화면 위치 안내(실제 판정 상세 칸 이름만). */
const SCREEN = {
  processStatus: "Process Flow › 선택 노드 원본 정보 STATUS",
  dependsOn: "Process Flow › 선택 노드 원본 정보 DEPENDS_ON",
  messageId: "Process Flow › 로그 구성의 Message 노드 MESSAGE_ID",
  logView: "Process Flow › 로그 구성",
  executionOrder: "Process Flow › 실행 순서",
  interface: "판정 요약 › 인터페이스 · 채널",
  observed: "위 표 이번 값",
  table: "위 표",
} as const;
/** `{문장 끝 마침표 앞}(화면: …).` */
const withScreen = (text: string, place: string) => `${text}(화면: ${place}).`;
const HARD = topic(METHOD_NAMES.hard);
const RELATION = topic(METHOD_NAMES.relation);
const SOFT = topic(METHOD_NAMES.soft);

// ---- 블록 하나(한 줄 요약·문장·표·확인할 것) ----
export type Lines = { line: string; sentence: string; check: string };

/** A.1 하드룰(이상 규칙). 인자는 표기된 값, 없으면 NO_VALUE. 상태 인자는 statusText 표기(`F(실패)`). */
export const hard = {
  processStatus: {
    condition: "STATUS ≠ S",
    lines: (x: string): Lines => x === NO_VALUE ? {
      line: `처리 상태가 ${SUCCESS}가 아닌 값으로 기록되었습니다`,
      sentence: `${HARD} 처리 상태가 ${SUCCESS}가 아니어서 이상으로 판단했습니다.`,
      check: withScreen("원본 로그에서 이 처리의 상태 기록을 확인하세요", SCREEN.processStatus),
    } : {
      line: `처리 상태가 ${toward(x)} 기록되었습니다`,
      sentence: `${HARD} 처리 상태가 ${because(x)} 이상으로 판단했습니다.`,
      check: withScreen(`원본 로그에서 이 처리의 상태 ${x} 기록을 확인하세요`, SCREEN.processStatus),
    },
  },
  negativeDuration: {
    condition: "START_TIME > END_TIME",
    /** 시각은 표에만 원문으로 둔다(문단에 넣지 않음). */
    lines: (): Lines => ({
      line: "시작 시각이 종료 시각보다 늦게 기록되었습니다",
      sentence: `${HARD} 시작 시각이 종료 시각보다 늦어 이상으로 판단했습니다.`,
      check: withScreen("원본 로그의 시작·종료 시각 기록(시간대 포함)을 확인하세요", SCREEN.observed),
    }),
  },
  successWithError: {
    condition: "STATUS = S, ERROR_COUNT > 0",
    success: SUCCESS,
    lines: (): Lines => ({
      line: `성공 상태인데 ${subject(F.error_count.short)} 기록되었습니다`,
      sentence: `${HARD} 처리 상태가 ${SUCCESS}인데 ${subject(F.error_count.name)} 0보다 커서 이상으로 판단했습니다.`,
      check: withScreen(`해당 업무 시스템에서 성공 처리에 ${subject(F.error_count.name)} 기록된 경위를 확인하세요`, SCREEN.observed),
    }),
  },
  countConservation: {
    condition: "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT",
    lines: (): Lines => ({
      line: `${subject(F.total_count.short)} ${withWord(F.success_count.short)} ${F.error_count.short}의 합과 다릅니다`,
      sentence: `${HARD} ${subject(F.total_count.name)} ${withWord(F.success_count.name)} ${F.error_count.name}의 합과 달라 이상으로 판단했습니다.`,
      check: withScreen(`해당 업무 시스템의 ${F.total_count.name}·${F.success_count.name}·${F.error_count.name} 집계 방식을 확인하세요`, SCREEN.observed),
    }),
  },
  transactionFailed: {
    condition: "거래 STATUS = F, 응답 코드 ∉ {0000, S, 없음}",
    failed: FAILED,
    /** meaning = 응답 코드 사전 뜻(확인할 것 줄 상한을 적용한 글자), 사전에 없으면 null(v1 확인할 것 문장). */
    lines: (c: string, meaning: string | null): Lines => ({
      line: `거래가 ${FAILED}, 응답 코드 ${toward(c)} 기록되었습니다`,
      sentence: `${HARD} 거래 상태가 ${FAILED}이고 ${subject(slot("응답 코드", c))} 성공 코드가 아니어서 이상으로 판단했습니다.`,
      check: meaning === null || c === NO_VALUE ? `응답 코드 ${c}의 의미를 응답 코드 설정에서 확인하세요.`
        : withScreen(`해당 업무 시스템에서 응답 코드 ${subject(codeWithMeaning(c, meaning))} 나온 경위를 확인하세요`, SCREEN.observed),
    }),
  },
  unregisteredInterface: {
    condition: "프로토콜 설정에 없는 인터페이스",
    lines: (i: string): Lines => ({
      line: `프로토콜 설정에 없는 인터페이스 ${i}의 거래입니다`,
      sentence: `${HARD} ${subject(slot("인터페이스", i))} 프로토콜 설정에 없어 이상으로 판단했습니다.`,
      check: withScreen(`인터페이스 ${subject(i)} 새로 추가된 것인지 프로토콜 설정에서 확인하세요`, SCREEN.interface),
    }),
  },
  protocolPath: {
    condition: "허용 경로 중 하나와 일치",
    /** 표 이번 값(문단에는 경로를 넣지 않는다): 맞지 않은 경로만 채널을 ` → `로, 여러 개면 ` / `로 잇고 PATH_LIMIT개 넘으면 `외 {n}개`. */
    PATH_LIMIT: 3,
    path: (channels: string[]) => channels.join(" → "),
    paths: (paths: string[], more: number) => `${paths.join(" / ")}${more > 0 ? ` 외 ${more}개` : ""}`,
    lines: (i: string): Lines => ({
      line: "처리 경로가 허용 경로에 없습니다",
      sentence: `${HARD} 처리 경로가 ${slot("인터페이스", i)}의 허용 경로에 없어 이상으로 판단했습니다.`,
      check: withScreen("처리 경로가 허용 경로에서 빠진 것인지 프로토콜 설정에서 확인하세요", SCREEN.executionOrder),
    }),
  },
};

/** A.1 관계 규칙. */
const fixedFrom = (from: string) => slot("정해진 앞 단계", from);
export const relation = {
  precedes: {
    condition: (from: string, to: string) => `${from} → ${to}`,
    /** 표 이번 값 칸(위반 낱말). 모르는 위반 값이면 NO_VALUE. */
    violationWords: { start_recorded: "시작 단계로 기록", other_predecessor: "다른 앞 단계", predecessor_missing: "앞 단계 기록 없음" } as Record<string, string>,
    /** 위반별 문장(모르는 위반 값이면 unknown). from은 값 없음이면 NO_VALUE. */
    violationSentences: {
      start_recorded: (from: string) => `${RELATION} 이 단계가 ${fixedFrom(from)} 없이 시작 단계로 기록되어 이상으로 판단했습니다.`,
      other_predecessor: (from: string) => `${RELATION} 이 단계의 앞 단계가 ${withWord(fixedFrom(from))} 달라 이상으로 판단했습니다.`,
      predecessor_missing: (from: string) => `${RELATION} ${fixedFrom(from)}의 기록이 없어 이상으로 판단했습니다.`,
    } as Record<string, (from: string) => string>,
    unknownSentence: (from: string) => `${RELATION} 이 단계의 앞 단계가 ${withWord(fixedFrom(from))} 맞지 않아 이상으로 판단했습니다.`,
    lines: (from: string, sentence: string): Lines => ({
      line: "정해진 앞 단계와 다르게 처리되었습니다",
      sentence,
      check: withScreen(`${fixedFrom(from)}의 기록이 원본 로그에 있는지 확인하세요`, SCREEN.dependsOn),
    }),
  },
  messageHandoff: {
    condition: (from: string, to: string) => `${from} → ${to} 메시지 일치`,
    observed: "불일치",
    lines: (from: string): Lines => ({
      line: "앞 단계와 메시지 연결이 맞지 않습니다",
      sentence: `${RELATION} ${subject(from === NO_VALUE ? "앞 단계" : from)} 보낸 메시지와 이 단계가 받은 메시지가 맞지 않아 이상으로 판단했습니다.`,
      check: withScreen("원본 로그에서 두 단계의 메시지 ID 기록을 확인하세요", SCREEN.messageId),
    }),
  },
  requiredStep: {
    condition: (i: string, step: string) => `인터페이스 ${i}에 ${step} 필요`,
    observed: "없음",
    lines: (step: string): Lines => ({
      line: `필수 단계 ${subject(step)} 실행되지 않았습니다`,
      sentence: `${RELATION} 이 거래에 ${subject(slot("필수 단계", step))} 없어 이상으로 판단했습니다.`,
      check: withScreen(`${subject(slot("필수 단계", step))} 이 거래에서 실행되었는지 원본 로그에서 확인하세요`, SCREEN.logView),
    }),
  },
};

/**
 * A.2 소프트룰(이상 징후 규칙). v·t는 단위까지 붙인 표기. compare = 저장값 이번 값 대 기준의 관계로 고른 비교 낱말
 * (over 넘어 · reach 이르러 · under 못 미쳐), null이면 대체 문장(이번 값 없음·연산자 모름·값과 연산자가 모순 — 기호를 그대로 쓴다).
 */
export type SoftCompare = "over" | "reach" | "under";
const SOFT_COMPARE = {
  /** 숫자 기준: `기준 {t}를 넘어` / `기준 {t}에 이르러` / `기준 {t}에 못 미쳐`. */
  sentence: { over: (t: string) => `기준 ${object(t)} 넘어`, reach: (t: string) => `기준 ${t}에 이르러`, under: (t: string) => `기준 ${t}에 못 미쳐` },
  line: { over: (t: string) => `기준 ${object(t)} 넘었습니다`, reach: (t: string) => `기준 ${t}에 이르렀습니다`, under: (t: string) => `기준 ${t}에 못 미칩니다` },
  /** 건수 계열(숫자 없음). */
  countSentence: { over: "기준을 넘어", reach: "기준에 이르러", under: "기준에 못 미쳐" },
  countLine: { over: "기준을 넘었습니다", reach: "기준에 이르렀습니다", under: "기준에 못 미칩니다" },
} as const;
/** 대체 자리: 연산자·기준을 아는 만큼(`기준(≥ 3,000 ms)`·`기준(≥)`·연산자를 모르면 `기준`). */
const fallbackBasis = (op: string, t: string | null) => (op === NO_VALUE ? "기준" : t === null || t === NO_VALUE ? `기준(${op})` : `기준(${op} ${t})`);
const SOFT_CHECK = (name: string) =>
  withScreen(`해당 업무 시스템에서 ${subject(name)} 나온 경위를 먼저 보고, 그다음 기준이 지금 운영에 맞는지 규칙 설정에서 확인하세요`, SCREEN.observed);
export const soft = {
  condition: (name: string, op: string, t: string) => `${name} ${op} ${t}`,
  /** 숫자 값(처리 시간·오류 비율 등): `{이름}이 {v}로 기준 {t}를 넘어`. compare가 null이면 대체 문장. */
  value: (label: FieldLabel, op: string, v: string, t: string, compare: SoftCompare | null): Lines => (compare ? {
    line: `${subject(label.short)} ${SOFT_COMPARE.line[compare](t)}`,
    sentence: `${SOFT} ${subject(label.name)} ${toward(v)} ${SOFT_COMPARE.sentence[compare](t)} 이상 징후로 판단했습니다.`,
    check: SOFT_CHECK(label.name),
  } : {
    line: `${subject(label.short)} ${fallbackBasis(op, t)}에 해당합니다`,
    sentence: `${SOFT} ${subject(label.name)} ${fallbackBasis(op, t)}에 해당해 이상 징후로 판단했습니다.`,
    check: SOFT_CHECK(label.name),
  }),
  /** 건수 계열: 한 줄 요약·문장에 숫자를 쓰지 않는다(표에만). */
  count: (label: FieldLabel, op: string, compare: SoftCompare | null): Lines => (compare ? {
    line: `${subject(label.short)} ${SOFT_COMPARE.countLine[compare]}`,
    sentence: `${SOFT} ${subject(label.name)} ${SOFT_COMPARE.countSentence[compare]} 이상 징후로 판단했습니다.`,
    check: SOFT_CHECK(label.name),
  } : {
    line: `${subject(label.short)} ${fallbackBasis(op, null)}에 해당합니다`,
    sentence: `${SOFT} ${subject(label.name)} ${fallbackBasis(op, null)}에 해당해 이상 징후로 판단했습니다.`,
    check: SOFT_CHECK(label.name),
  }),
};

/** A.3 머신러닝 모델(기여도 계산 성공, 1위 참가자 종류별). impact = `62%` 또는 null(영향 생략). */
const impactNote = (impact: string | null) => (impact ? `(${impact})` : "");
const ML_ELAPSED_CHECK = "같은 프로세스의 최근 처리 시간을 함께 확인하세요.";
const mlValueCheck = (name: string) => withScreen(`해당 업무 시스템에서 ${subject(name)} 평소와 다른 이유를 확인하세요`, SCREEN.table);
const ML = `${METHOD_NAMES.ml} 판단에는`;
export const ml = {
  elapsedLonger: (a: string, b: string, impact: string | null): Lines => ({
    line: `${subject(F.elapsed_ms.short)} 평소보다 깁니다`,
    sentence: `${ML} 평소 ${b}보다 긴 ${F.elapsed_ms.name} ${subject(a)} 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: ML_ELAPSED_CHECK,
  }),
  elapsedShorter: (a: string, b: string, impact: string | null): Lines => ({
    line: `${subject(F.elapsed_ms.short)} 평소보다 짧습니다`,
    sentence: `${ML} 평소 ${b}보다 짧은 ${F.elapsed_ms.name} ${subject(a)} 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: ML_ELAPSED_CHECK,
  }),
  errorRatio: (a: string, b: string, higher: boolean, impact: string | null): Lines => ({
    line: `${subject(F.error_ratio.short)} 평소보다 ${higher ? "높습니다" : "낮습니다"}`,
    sentence: `${ML} 평소 ${b}보다 ${higher ? "높은" : "낮은"} ${F.error_ratio.name} ${subject(a)} 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: mlValueCheck(F.error_ratio.name),
  }),
  /** 건수 계열 단독·묶음·그 밖(평소 값 숫자를 쓰지 않는 경우). name = 문단 이름, short = 한 줄 요약 이름. 처리 시간이면 처리 시간 확인 줄. */
  value: (name: string, short: string, impact: string | null, elapsed = false): Lines => ({
    line: `${subject(short)} 평소와 다릅니다`,
    sentence: `${ML} 평소와 다른 ${subject(name)} 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: elapsed ? ML_ELAPSED_CHECK : mlValueCheck(name),
  }),
  /** 2위 한 문장(3위 이하는 표만). */
  second: (name: string, impact: string | null) => `다음으로 평소와 다른 ${subject(name)} 영향을 주었습니다${impactNote(impact)}.`,
  /** 표 묶음 이름·참가자 이름: 구성원 이름을 `·`로 잇는다. */
  playerName: (names: string[]) => names.join("·"),
};

/** A.4 기여도 없음·미판정·정상. */
export const mlNoContribution = {
  line: "머신러닝 모델이 평소와 다른 값을 확인했습니다",
  sentence: `${topic(METHOD_NAMES.ml)} 학습 데이터 기준 원점수가 판정선을 벗어나 이상 징후로 판단했습니다.`,
};
export const unresolved = {
  /** 미판정 방법 표시 이름을 `·`로 잇는다. */
  names: (names: string[]) => names.join("·"),
  sentence: (names: string) => `${topic(names)} 결과를 내지 못해 평가 불가입니다.`,
  check: (names: string) => `결과를 내지 못한 ${names}의 입력 수집 상태를 확인하세요.`,
};
export const normal = {
  complete: { line: "켠 검사에서 평소와 다른 점이 확인되지 않았습니다", sentence: "켠 검사가 모두 정상으로 끝났습니다." },
  unconfirmed: { line: "일부 검사 결과가 없어 정상 여부를 확인하지 못했습니다" },
};
/** 근거 방법 문장이 최대 수를 넘을 때(n = 문장에 넣지 못한, 표에 행이 있는 근거 방법 수). */
export const overflowSentence = (n: number) => `그 밖의 근거 ${n}개는 아래 표에 있습니다.`;
