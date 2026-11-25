/**
 * AI 판정 근거 요약(evidenceSummary) 문장 틀·문구 — 화면에 보이는 글자는 모두 이 파일에만 둔다.
 * 문구·틀을 바꾸면 TEMPLATE_VERSION을 올린다(설명 작업의 다듬기 결과는 같은 버전일 때만 쓰인다).
 * 조립 순서·값 고르기는 evidenceSummary.ts, 값 표기·조사는 koreanText.ts.
 * 문장 틀 인자는 이미 표기된 문자열(천 단위 쉼표·단위 붙임 완료)이고, 조사는 여기서 붙인다.
 */
import { object, subject, toward, withWord } from "./koreanText.js";

export const TEMPLATE_VERSION = "evidence-template-v1";
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
 * 가리는 이름표 = FIELD_LABELS 이름 + MASKED_NAMES(`이상 규칙`의 `이상`은 판정 낱말이 아니다).
 */
export const STATE_WORDS = ["이상 신호 없음", "정상 여부 미확인", "이상 징후", "평가 불가", "이상", "정상"] as const;
/** 원천 값 목록(valueTokens) 상한: 개수·글자 수. 넘는 값은 담지 않는다. */
export const VALUE_TOKEN_LIMIT = 16;
export const VALUE_TOKEN_MAX_LENGTH = 120;

// ---- 이름표 ----
/** 변수 이름표. unitUnknown = 건수 계열(단위 미확인, 문단에는 숫자를 쓰지 않음), unit = 값 뒤 단위(한 칸 띄움). */
export type FieldLabel = { name: string; unitUnknown: boolean; unit: string | null };
export const FIELD_LABELS: Record<string, FieldLabel> = {
  total_count: { name: "처리량(TOTAL_COUNT)", unitUnknown: true, unit: null },
  success_count: { name: "SUCCESS_COUNT", unitUnknown: true, unit: null },
  error_count: { name: "ERROR_COUNT", unitUnknown: true, unit: null },
  retry_count: { name: "RETRY_COUNT", unitUnknown: true, unit: null },
  elapsed_ms: { name: "처리 시간", unitUnknown: false, unit: "ms" },
  error_ratio: { name: "오류 비율 값", unitUnknown: false, unit: null },
};
/** 방법 표시 이름(문장 주어·미판정 문장). */
export const METHOD_NAMES = { hard: "이상 규칙", relation: "관계 규칙", soft: "이상 징후 규칙", ml: "머신러닝 모델" } as const;
/** 판정 낱말을 뽑기 전에 가리는 이름(방법 표시 이름). */
export const MASKED_NAMES = ["이상 규칙", "이상 징후 규칙", "관계 규칙", "머신러닝 모델", "정상 프로토콜"] as const;
/** 표 이번 값 칸의 이름(값 앞에 붙는 이름). */
export const OBSERVED_NAMES = { status: "상태", totalCount: "TOTAL_COUNT", successCount: "SUCCESS_COUNT", errorCount: "ERROR_COUNT" } as const;
/** 표 결과 칸. */
export const RESULT_WORDS = { anomaly: "이상", sign: "이상 징후", unresolved: "평가 불가" } as const;
/** 소프트룰 비교 기호(낱말 `이상`·`미만`은 판정 낱말과 겹쳐 쓰지 않는다). */
export const OPERATOR_SIGNS: Record<string, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
/** scale 1 구성원(학습 중앙값이 여러 어댑터가 섞인 값)의 평소 값 칸 글자(숫자 대신). 값 자체가 없는 NO_VALUE와 구분된다. */
export const SCALE_UNIT_USUAL = "평소와 다름";
/** ML 표가 비었을 때 표 아래 한 줄(기여도 상태 낱말). */
export const CONTRIBUTION_NOTES = { absent: "기여도 미계산", skipped: "기여도 계산 안 함", failed: "기여도 계산 실패", unreadable: "기여도 표시 불가" } as const;

/** 문장 안 `{낱말} {값}` 자리: 값이 없으면 낱말만 두어 조사가 낱말에 붙는다(`시작 시각과 종료 시각을`). 한 줄 요약·표·확인할 것은 값 없음 글자 그대로. */
const slot = (noun: string, value: string) => (value === NO_VALUE ? noun : `${noun} ${value}`);

/** `{이름} 값`(이름이 이미 `값`으로 끝나면 덧붙이지 않는다 — `오류 비율 값 값` 방지). */
export const valueWord = (name: string) => (name.endsWith("값") ? name : `${name} 값`);

// ---- 블록 하나(한 줄 요약·문장·표·확인할 것) ----
export type Lines = { line: string; sentence: string; check: string };

/** A.1 하드룰(이상 규칙). 인자는 표기된 값, 없으면 NO_VALUE. */
export const hard = {
  processStatus: {
    condition: "STATUS ≠ S",
    lines: (x: string): Lines => ({
      line: `처리 상태가 S가 아닌 ${toward(x)} 기록되었습니다`,
      sentence: `이상 규칙이 이번 ${object(slot("처리 상태", x))} 성공 상태 S와 비교했고, 달라 이상으로 판단했습니다.`,
      check: `원천 시스템에서 이 처리의 상태 ${x} 기록 내용을 확인하세요.`,
    }),
  },
  negativeDuration: {
    condition: "START_TIME > END_TIME",
    lines: (s: string, e: string): Lines => ({
      line: "시작 시각이 종료 시각보다 늦게 기록되었습니다",
      sentence: `이상 규칙이 ${withWord(slot("시작 시각", s))} ${object(slot("종료 시각", e))} 비교했고, 시작이 더 늦어 이상으로 판단했습니다.`,
      check: "원천 시스템의 시작·종료 시각 기록(시간대 포함)을 확인하세요.",
    }),
  },
  successWithError: {
    condition: "STATUS = S, ERROR_COUNT > 0",
    lines: (): Lines => ({
      line: "성공 상태인데 ERROR_COUNT가 기록되었습니다",
      sentence: "이상 규칙이 처리 상태 S와 ERROR_COUNT를 함께 확인했고, 성공 상태인데 ERROR_COUNT가 0보다 커서 이상으로 판단했습니다.",
      check: "원천 시스템에서 성공 처리에 ERROR_COUNT가 기록된 경위를 확인하세요.",
    }),
  },
  countConservation: {
    condition: "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT",
    lines: (): Lines => ({
      line: "처리량(TOTAL_COUNT)이 SUCCESS_COUNT와 ERROR_COUNT의 합과 다릅니다",
      sentence: "이상 규칙이 처리량(TOTAL_COUNT)을 SUCCESS_COUNT와 ERROR_COUNT의 합과 비교했고, 두 값이 달라 이상으로 판단했습니다.",
      check: "원천 시스템의 TOTAL_COUNT·SUCCESS_COUNT·ERROR_COUNT 집계 방식을 확인하세요.",
    }),
  },
  transactionFailed: {
    condition: "거래 STATUS = F, 응답 코드 ∉ {0000, S, 없음}",
    lines: (c: string): Lines => ({
      line: `거래가 실패 상태(F), 응답 코드 ${toward(c)} 기록되었습니다`,
      sentence: `이상 규칙이 거래 상태 F와 ${object(slot("응답 코드", c))} 성공 코드(0000·S)와 비교했고, 성공 코드가 아니어서 이상으로 판단했습니다.`,
      check: `응답 코드 ${c}의 의미를 응답 코드 설정에서 확인하세요.`,
    }),
  },
  unregisteredInterface: {
    condition: "프로토콜 설정에 없는 인터페이스",
    lines: (i: string): Lines => ({
      line: `프로토콜 설정에 없는 인터페이스 ${i}의 거래입니다`,
      sentence: `이상 규칙이 이번 거래의 ${object(slot("인터페이스", i))} 프로토콜 설정 목록과 비교했고, 목록에 없어 이상으로 판단했습니다.`,
      check: `인터페이스 ${subject(i)} 새로 추가된 것인지 프로토콜 설정에서 확인하세요.`,
    }),
  },
  protocolPath: {
    condition: "허용 경로 중 하나와 일치",
    /** 표 이번 값(문단에는 경로를 넣지 않는다): 맞지 않은 경로만 채널을 ` → `로, 여러 개면 ` / `로 잇고 PATH_LIMIT개 넘으면 `외 {n}개`. */
    PATH_LIMIT: 3,
    path: (channels: string[]) => channels.join(" → "),
    paths: (paths: string[], more: number) => `${paths.join(" / ")}${more > 0 ? ` 외 ${more}개` : ""}`,
    lines: (i: string): Lines => ({
      line: "처리 경로가 허용 경로와 다릅니다",
      sentence: `이상 규칙이 이번 처리 경로를 ${slot("인터페이스", i)}의 허용 경로와 비교했고, 맞는 경로가 없어 이상으로 판단했습니다.`,
      check: "이번 경로가 허용 경로에서 빠진 것인지 프로토콜 설정에서 확인하세요.",
    }),
  },
};

/** A.1 관계 규칙. */
export const relation = {
  precedes: {
    condition: (from: string, to: string) => `${from} → ${to}`,
    /** 표 이번 값 칸(위반 낱말). 모르는 위반 값이면 NO_VALUE. */
    violationWords: { start_recorded: "시작 단계로 기록", other_predecessor: "다른 앞 단계", predecessor_missing: "앞 단계 기록 없음" } as Record<string, string>,
    /** 문장 안 이유(모르는 위반 값이면 null — 이유 없이 `…비교했고, 이상으로 판단했습니다.`). */
    violationReasons: { start_recorded: "시작 단계로 기록되어", other_predecessor: "다른 앞 단계여서", predecessor_missing: "앞 단계 기록이 없어" } as Record<string, string>,
    lines: (from: string, reason: string | null): Lines => ({
      line: "정해진 앞 단계 없이 처리되었습니다",
      sentence: `관계 규칙이 이 단계의 앞 단계 기록을 ${withWord(slot("정해진 앞 단계", from))} 비교했고, ${reason ? `${reason} ` : ""}이상으로 판단했습니다.`,
      check: `${from} 단계 기록이 원천 시스템에 있는지 확인하세요.`,
    }),
  },
  messageHandoff: {
    condition: (from: string, to: string) => `${from} → ${to} 메시지 일치`,
    observed: "불일치",
    lines: (from: string): Lines => ({
      line: "앞 단계와 메시지 연결이 맞지 않습니다",
      sentence: `관계 규칙이 ${from === NO_VALUE ? "앞 단계" : from}의 보낸 메시지와 이 단계의 받은 메시지를 비교했고, 맞지 않아 이상으로 판단했습니다.`,
      check: "두 단계의 메시지 ID 기록을 확인하세요.",
    }),
  },
  requiredStep: {
    condition: (i: string, step: string) => `인터페이스 ${i}에 ${step} 필요`,
    observed: "없음",
    lines: (step: string): Lines => ({
      line: `필수 단계 ${subject(step)} 실행되지 않았습니다`,
      sentence: `관계 규칙이 이 거래의 실행 단계를 ${withWord(slot("필수 단계", step))} 비교했고, 없어 이상으로 판단했습니다.`,
      check: `${step} 단계가 이 거래에서 실행되었는지 원천 시스템에서 확인하세요.`,
    }),
  },
};

/** A.2 소프트룰(이상 징후 규칙). v·t는 단위까지 붙인 표기. */
export const soft = {
  condition: (name: string, op: string, t: string) => `${name} ${op} ${t}`,
  /** 처리 시간: 한 줄 요약에 기준 값(ms)을 쓴다. */
  elapsed: (name: string, op: string, v: string, t: string): Lines => ({
    line: `${subject(name)} 기준 ${op} ${t}에 해당합니다`,
    sentence: `이상 징후 규칙이 이번 ${object(slot(name, v))} ${withWord(slot("기준", t))} 비교했고, 기준(${op})에 해당해 이상 징후로 판단했습니다.`,
    check: `${name} 기준 값이 지금 운영에 맞는지 규칙 설정에서 확인하세요.`,
  }),
  /** 건수 계열: 한 줄 요약·문장에 숫자를 쓰지 않는다(표에만). */
  count: (name: string, op: string): Lines => ({
    line: `${subject(name)} 기준(${op})에 해당합니다`,
    sentence: `이상 징후 규칙이 이번 ${object(valueWord(name))} 기준 값과 비교했고, 기준(${op})에 해당해 이상 징후로 판단했습니다.`,
    check: `${name} 기준 값이 지금 운영에 맞는지 규칙 설정에서 확인하세요.`,
  }),
  /** 그 밖(오류 비율 값 등 단위 확정 값). */
  value: (name: string, op: string, v: string, t: string): Lines => ({
    line: `${subject(name)} 기준 ${op} ${t}에 해당합니다`,
    sentence: `이상 징후 규칙이 이번 ${object(slot(name, v))} ${withWord(slot("기준", t))} 비교했고, 기준(${op})에 해당해 이상 징후로 판단했습니다.`,
    check: `${name} 기준 값이 지금 운영에 맞는지 규칙 설정에서 확인하세요.`,
  }),
};

/** A.3 머신러닝 모델(기여도 계산 성공, 1위 참가자 종류별). impact = `62%` 또는 null(영향 생략). */
const impactNote = (impact: string | null) => (impact ? `(영향 ${impact})` : "");
const ML_ELAPSED_CHECK = "같은 프로세스의 최근 처리 시간을 함께 확인하세요.";
const mlValueCheck = (name: string) => `원천 시스템에서 ${subject(valueWord(name))} 평소와 다른 이유를 확인하세요.`;
export const ml = {
  elapsedLonger: (a: string, b: string, impact: string | null): Lines => ({
    line: "평소 처리 시간과 비교하여 지연이 확인됩니다",
    sentence: `머신러닝 모델이 이번 처리 시간 ${object(a)} 평소 값 ${withWord(b)} 비교했고, 더 길어 판단에 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: ML_ELAPSED_CHECK,
  }),
  elapsedShorter: (a: string, b: string, impact: string | null): Lines => ({
    line: "평소 처리 시간과 비교하여 단축이 확인됩니다",
    sentence: `머신러닝 모델이 이번 처리 시간 ${object(a)} 평소 값 ${withWord(b)} 비교했고, 더 짧아 판단에 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: ML_ELAPSED_CHECK,
  }),
  errorRatio: (a: string, b: string, higher: boolean, impact: string | null): Lines => ({
    line: "평소와 다른 오류 비율 값이 확인됩니다",
    sentence: `머신러닝 모델이 이번 오류 비율 값 ${object(a)} 평소 값 ${withWord(b)} 비교했고, 더 ${higher ? "높아" : "낮아"} 판단에 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: mlValueCheck("오류 비율 값"),
  }),
  /** 건수 계열 단독·묶음·그 밖(평소 값 숫자를 쓰지 않는 경우). 처리 시간이면 처리 시간 확인 줄. */
  value: (name: string, impact: string | null, elapsed = false): Lines => ({
    line: `평소와 다른 ${subject(valueWord(name))} 확인됩니다`,
    sentence: `머신러닝 모델이 이번 ${object(valueWord(name))} 평소 값과 비교했고, 달라 판단에 가장 크게 영향을 주었습니다${impactNote(impact)}.`,
    check: elapsed ? ML_ELAPSED_CHECK : mlValueCheck(name),
  }),
  /** 2위 한 문장(3위 이하는 표만). */
  second: (name: string, impact: string | null) => `다음으로 ${subject(name)} 평소와 달라 영향을 주었습니다${impactNote(impact)}.`,
  /** 표 묶음 이름·참가자 이름: 구성원 이름을 `·`로 잇는다. */
  playerName: (names: string[]) => names.join("·"),
};

/** A.4 기여도 없음·미판정·정상. */
export const mlNoContribution = {
  line: "머신러닝 모델이 평소와 다른 값을 확인했습니다",
  sentence: "머신러닝 모델이 이번 값들을 학습 데이터의 평소 값과 비교했고, 판정선을 벗어나 이상 징후로 판단했습니다.",
};
export const unresolved = {
  /** 미판정 방법 표시 이름을 `·`로 잇는다. */
  names: (names: string[]) => names.join("·"),
  sentence: (names: string) => `일부 검사(${names})는 결과를 내지 못해 평가 불가입니다.`,
  check: (names: string) => `결과를 내지 못한 검사(${names})의 입력 수집 상태를 확인하세요.`,
};
export const normal = {
  complete: { line: "켠 검사에서 평소와 다른 점이 확인되지 않았습니다", sentence: "켠 검사가 모두 정상으로 끝났습니다." },
  unconfirmed: { line: "일부 검사 결과가 없어 정상 여부를 확인하지 못했습니다" },
};
/** 근거 방법 문장이 최대 수를 넘을 때(n = 문장에 넣지 못한, 표에 행이 있는 근거 방법 수). */
export const overflowSentence = (n: number) => `그 밖의 근거 ${n}개는 아래 표에 있습니다.`;
