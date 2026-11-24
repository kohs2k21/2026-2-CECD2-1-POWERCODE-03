import type { AttributionPlayer, AttributionSucceeded } from "./data/attribution";
import { SCALE_UNIT_BASELINE, UNIT_UNKNOWN, attributionFeatureLabel, medianText } from "./attributionLabels";

/**
 * 기여도 문장 만들기(detection-decision.md "6.1 ML 기여도" 절 표시 규칙 + analysis-ui.md 판정 상세 기여도 보충 규칙).
 * 저장값만 쓰고 새 수치를 계산하지 않는다(반올림·% 변환·지수 표기 펼치기·천 단위 쉼표만).
 * 설명 작업의 기여도 문장(worker `ml_attribution_text.py`)과 문장 틀·값 표기(지수 펼치기·천 단위 쉼표·`800 ms`·0인 음수의 부호 없음)가 같다.
 * 의도된 차이는 비율 없음 안내의 위치(설명은 요약 문장 끝, 화면은 별도 안내 줄)뿐이다. 계약상 오지 않는 입력(share null이 일부 참가자만)에서는
 * 화면이 그 참가자만 비율을 빼고 설명은 모두 뺀다.
 * - 원점수·판정선·보이는 반사실 점수: 소수 셋째 자리 half-up. 셋째 자리에서 원점수나 보이는 반사실 점수가 판정선과 같은 글자면 함께 자릿수를 늘린다(최대 여섯째)
 * - 판정선과 |차| ≤ tol인 보이는 점수는 숫자 대신 `판정선과 같음`(자릿수 늘리기 대상에서 뺀다)
 * - 비율: share × 100 정수 half-up. 표시 비율 합은 보정하지 않는다
 * - 조사: 앞 낱말 마지막 글자 받침(끝 괄호 덧말은 떼고, 숫자는 읽는 소리, 영문은 끝 글자 L·M·N·NG만 받침)
 */

// ---- 10진 문자열(반올림 half-up, 0에서 먼 쪽) ----
/** 펼칠 지수의 절댓값 상한. 넘으면 원문 그대로 둔다(화면에 수백 자리 0을 만들지 않음). */
export const EXPONENT_LIMIT = 400;
/** 이보다 긴 숫자 문자열은 표기 변환 없이 원문 그대로 둔다(과도한 자릿수 방어). */
export const NUMBER_TEXT_LIMIT = 1000;
/**
 * 지수 표기 10진 문자열(`9.5E+3`·`1E-7`·`0E-10`·`1e-05`)을 일반 표기로 펼친다. 문자열로만 자리를 옮기며 반올림·자릿수 변경은 없다.
 * 지수 표기가 아니거나, 숫자로 해석할 수 없거나, 지수가 상한을 넘으면 원문 그대로 돌려준다. 예외를 던지지 않는다.
 */
export const expandExponent = (value: string): string => {
  if (value.length > NUMBER_TEXT_LIMIT) return value;
  const m = /^([+-]?)(\d+)(?:\.(\d*))?[eE]([+-]?\d+)$/.exec(value.trim());
  if (!m) return value;
  const exponent = Number(m[4]);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > EXPONENT_LIMIT) return value;
  const sign = m[1] === "-" ? "-" : "";
  const digits = m[2] + (m[3] ?? "");
  const point = m[2].length + exponent; // digits 앞에서 소수점까지의 자리 수
  let int: string;
  let frac: string;
  if (point <= 0) [int, frac] = ["0", "0".repeat(-point) + digits];
  else if (point >= digits.length) [int, frac] = [digits + "0".repeat(point - digits.length), ""];
  else [int, frac] = [digits.slice(0, point), digits.slice(point)];
  int = int.replace(/^0+(?=\d)/, "");
  return `${sign}${int}${frac ? `.${frac}` : ""}`;
};
/** number → 지수 표기 없는 10진 문자열(JS 최단 표기 그대로 펼침). */
export const plainNumber = (n: number): string => expandExponent(String(n));
/**
 * 10진 문자열을 소수 `digits`자리로 half-up 반올림(지수 표기는 먼저 펼침). `shift`만큼 소수점을 오른쪽으로 옮긴 뒤 반올림한다(비율 %용).
 * 10진 표기가 아니면 원문 그대로.
 */
export const roundHalfUp = (raw: string, digits: number, shift = 0): string => {
  if (raw.length > NUMBER_TEXT_LIMIT) return raw;
  const m = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(expandExponent(raw).trim());
  if (!m || (m[2] === "" && !m[3])) return raw;
  const sign = m[1] === "-" ? "-" : "";
  let int = m[2] || "0";
  let frac = m[3] ?? "";
  if (shift > 0) {
    frac = frac.padEnd(shift, "0");
    int += frac.slice(0, shift);
    frac = frac.slice(shift);
  }
  const kept = BigInt(int + frac.padEnd(digits, "0").slice(0, digits));
  const rounded = (frac[digits] ?? "0") >= "5" ? kept + 1n : kept;
  const text = rounded.toString().padStart(digits + 1, "0");
  const body = digits > 0 ? `${text.slice(0, -digits)}.${text.slice(-digits)}` : text;
  return rounded === 0n ? body : `${sign}${body}`;
};
/**
 * 정수 부분에 천 단위 쉼표(화면 숫자 표기와 같게 소수 부분은 그대로, 자리 값은 바꾸지 않음). 지수 표기는 먼저 펼친다.
 * 값이 0인 음수 표기(`-0`·`-0.0`)는 부호 없이. 10진 표기가 아니면 원문 그대로.
 */
export const groupDigits = (raw: string): string => {
  if (raw.length > NUMBER_TEXT_LIMIT) return raw;
  const value = expandExponent(raw);
  const m = /^([+-]?)(\d+)(\.\d+)?$/.exec(value);
  const zero = m !== null && /^[0.]*$/.test(`${m[2]}${m[3] ?? ""}`);
  return m ? `${m[1] === "-" && !zero ? "-" : ""}${m[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${m[3] ?? ""}` : raw;
};
export const percentText = (share: number) => `${roundHalfUp(plainNumber(share), 0, 2)}%`;

/** 화면에 보이는 반사실 점수: 해소 k면 k번째(+ k ≥ 2이면 1번째), 유지면 마지막. topK가 없으면 없음. */
export const shownCounterfactualScores = (a: Pick<AttributionSucceeded, "counterfactual" | "clearedAtK">): number[] => {
  const after = a.counterfactual.map((c) => c.scoreAfter);
  if (a.clearedAtK) return [after[a.clearedAtK - 1], ...(a.clearedAtK > 1 ? [after[0]] : [])];
  return after.slice(-1);
};
/** 보이는 점수가 판정선과 같은지: |점수 − 판정선| ≤ tol(판정선 문자열을 숫자로 바꾼 값). */
export const sameAsThreshold = (a: Pick<AttributionSucceeded, "threshold" | "methodScore" | "scoreActual">, score: number) =>
  Math.abs(score - Number(expandExponent(a.threshold))) <= attributionTolerance(a);
/** 원점수·판정선·보이는 반사실 점수 공통 자릿수(3~6). 판정선과 같은 점수(`판정선과 같음`으로 쓰는 점수)는 판단에서 뺀다. */
export const scoreDigits = (a: Pick<AttributionSucceeded, "scoreActual" | "threshold" | "counterfactual" | "clearedAtK" | "methodScore">): number => {
  const scores = [a.scoreActual, ...shownCounterfactualScores(a)].filter((s) => !sameAsThreshold(a, s)).map(plainNumber);
  for (let digits = 3; digits < 6; digits += 1) {
    const line = roundHalfUp(a.threshold, digits);
    if (scores.every((s) => roundHalfUp(s, digits) !== line)) return digits;
  }
  return 6;
};
/**
 * 음수·동률 판단 tol(detection-decision.md "6.1 ML 기여도" 절): max(1e-9, 1e-9·|score|), score = 같은 ML 방법 결과의 `score`
 * (없거나 숫자가 아니면 `attribution.scoreActual`). φ < −tol이면 음수, |φ| ≤ tol이면 0. 순서는 저장된 topK·players[] 그대로(다시 정렬하지 않음).
 */
export const attributionTolerance = (a: Pick<AttributionSucceeded, "methodScore" | "scoreActual">) =>
  Math.max(1e-9, 1e-9 * Math.abs(a.methodScore ?? a.scoreActual));

// ---- 조사 ----
type Sound = { batchim: boolean; rieul: boolean };
const sound = (batchim: boolean, rieul = false): Sound => ({ batchim, rieul });
// 영 일 이 삼 사 오 육 칠 팔 구
const digitSounds: Sound[] = [sound(true), sound(true, true), sound(false), sound(true), sound(false), sound(false), sound(true), sound(true, true), sound(true, true), sound(false)];
/** 끝의 괄호 덧말(`처리량(TOTAL_COUNT)`·`(단위 미확인)`)을 떼어 조사가 붙을 낱말만 남긴다(괄호 안 괄호 포함). */
const stripTrailingParens = (word: string): string => {
  let text = word.trimEnd();
  while (text.endsWith(")")) {
    let depth = 0;
    let index = text.length - 1;
    for (; index >= 0; index -= 1) {
      depth += text[index] === ")" ? 1 : text[index] === "(" ? -1 : 0;
      if (depth === 0) break;
    }
    if (depth !== 0 || index <= 0) return text;
    text = text.slice(0, index).trimEnd();
  }
  return text;
};
const numberSound = (token: string): Sound => {
  if (token.includes(".")) return digitSounds[Number(token.at(-1))] ?? sound(false);
  const digits = token.replace(/^0+/, "") || "0";
  if (digits === "0") return digitSounds[0];
  const zeros = digits.length - digits.replace(/0+$/, "").length;
  if (zeros === 0) return digitSounds[Number(digits.at(-1))];
  return sound(zeros < 12); // 십(ㅂ)·백(ㄱ)·천·만(ㄴ)·억(ㄱ) 받침, 조 이상은 없음
};
export const lastSound = (word: string): Sound => {
  const text = stripTrailingParens(word);
  const ch = text.at(-1);
  if (!ch) return sound(false);
  const code = ch.charCodeAt(0);
  if (code >= 0xac00 && code <= 0xd7a3) {
    const final = (code - 0xac00) % 28;
    return sound(final !== 0, final === 8);
  }
  if (/\d/.test(ch)) return numberSound(/[\d.]*$/.exec(text)?.[0] ?? ch);
  const upper = text.toUpperCase();
  if (upper.endsWith("NG")) return sound(true);
  if ("LMN".includes(upper.at(-1) as string)) return sound(true, upper.at(-1) === "L");
  return sound(false);
};
/** 은/는(조사만) */
export const topicParticle = (word: string) => (lastSound(word).batchim ? "은" : "는");
/** 을/를(조사만) */
export const objectParticle = (word: string) => (lastSound(word).batchim ? "을" : "를");
/** 으로/로(조사만, 받침 없거나 ㄹ이면 로) */
export const towardParticle = (word: string) => {
  const s = lastSound(word);
  return s.batchim && !s.rieul ? "으로" : "로";
};
export const topic = (word: string) => `${word}${topicParticle(word)}`;
export const toward = (word: string) => `${word}${towardParticle(word)}`;

// ---- 이름·값 ----
/** 변수 이름(값이 함께 나오지 않는 자리 = 이름만). */
export const featureName = (feature: string) => attributionFeatureLabel(feature).name;
const unitNote = (feature: string) => (attributionFeatureLabel(feature).unitUnknown ? UNIT_UNKNOWN : "");
/** 값 표시: 지수 펼침 + 천 단위 쉼표 + 단위(있으면). */
export const valueText = (feature: string, value: string | number) => {
  const unit = attributionFeatureLabel(feature).unit;
  const text = groupDigits(typeof value === "number" ? plainNumber(value) : value);
  return unit ? `${text} ${unit}` : text;
};
/** 실제값 + `(단위 미확인)`(묶음 괄호·표). */
export const actualWithUnitNote = (feature: string, actual: string) => `${valueText(feature, actual)}${unitNote(feature)}`;
const scaleUnit = (player: AttributionPlayer, feature: string) => player.scaleIsUnit[feature] === true;
const isGroup = (player: AttributionPlayer) => player.features.length > 1;
/** 참가자 이름: 단독은 변수 이름, 묶음은 구성원 이름을 `·`로 잇기. */
export const playerName = (player: AttributionPlayer) => player.features.map(featureName).join("·");
/** 묶음 괄호 안: `처리량(TOTAL_COUNT) 120(단위 미확인) · 중앙값 100, …`, scale 1 구성원은 `중앙값 …` 자리에 `평소 값과 다름`. */
export const groupMembersText = (player: AttributionPlayer) =>
  player.features
    .map((f) => `${featureName(f)} ${actualWithUnitNote(f, player.actual[f])} · ${scaleUnit(player, f) ? SCALE_UNIT_BASELINE : medianText(valueText(f, player.baseline[f]))}`)
    .join(", ");
/** 반사실·요약 속 이름: 묶음은 구성원 괄호를 함께, 단독은 이름만. */
const mention = (player: AttributionPlayer) => (isGroup(player) ? `${playerName(player)}(${groupMembersText(player)})` : playerName(player));
/**
 * 반사실(k=1) 앞부분 `{top1}만 학습 데이터 중앙값({baseline})으로 바꾸면`.
 * - 묶음: `{묶음}({구성원 나열})만 학습 데이터 중앙값으로 바꾸면`
 * - 단독 scale 1: 괄호 생략, 값이 없으니 `(단위 미확인)`도 없음
 * - 단독: 이름 + `(단위 미확인)` + `학습 데이터 중앙값({기준값})`
 */
export const replacedAlone = (player: AttributionPlayer) => {
  if (isGroup(player)) return `${mention(player)}만 학습 데이터 중앙값으로 바꾸면`;
  const feature = player.features[0];
  if (scaleUnit(player, feature)) return `${playerName(player)}만 학습 데이터 중앙값으로 바꾸면`;
  return `${playerName(player)}${unitNote(feature)}만 학습 데이터 중앙값(${valueText(feature, player.baseline[feature])})으로 바꾸면`;
};

// ---- 문장 ----
export const SAME_AS_THRESHOLD = "판정선과 같음";
export const NO_TOP_SENTENCE = "원점수를 이상 쪽으로 움직인 변수를 찾지 못했습니다.";
export const SHARE_NULL_SENTENCE = "학습 데이터 중앙값과 원점수 차이가 거의 없어 비율은 계산하지 않았습니다.";
export const BASELINE_WARNING_SENTENCE = "학습 데이터 중앙값에서도 원점수가 판정선을 넘어 비율을 해석할 때 주의가 필요합니다.";
export const LIMITATION_SENTENCE = "기여도는 모델이 점수를 매긴 방식에 대한 분석이며 실제 장애 원인이 아닙니다. 원인은 원천 시스템 운영 로그로 확인하세요.";
const directionWords = { higher_is_anomaly: { push: "올린", cross: "낮아져" }, lower_is_anomaly: { push: "내린", cross: "높아져" } } as const;

export type AttributionSentences = {
  summary: string;
  shareNull: string | null;
  negative: string | null;
  counterfactual: string | null;
  groups: string[];
  baselineWarning: string | null;
  limitation: string;
};

export const attributionSentences = (a: AttributionSucceeded): AttributionSentences => {
  const digits = scoreDigits(a);
  const score = (n: number) => roundHalfUp(plainNumber(n), digits);
  const same = (n: number) => sameAsThreshold(a, n);
  const shownValue = (n: number) => (same(n) ? SAME_AS_THRESHOLD : `값 ${score(n)}`);
  const words = directionWords[a.direction];
  const byId = new Map(a.players.map((p) => [p.playerId, p]));
  const top = a.topK.map((id) => byId.get(id) as AttributionPlayer);
  const shareNull = top.length > 1 && top.some((p) => p.share === null);

  // 요약: 묶음은 구성원 괄호, 단독 scale 1은 `이름 평소 값과 다름`, 비율은 맨 뒤(share null인 참가자는 비율 생략)
  const lead = `머신러닝 모델 원점수(${shownValue(a.scoreActual)}, 판정선 ${roundHalfUp(a.threshold, digits)})를 이상 쪽으로 ${words.push} 변수는 `;
  const item = (p: AttributionPlayer) => {
    const head = !isGroup(p) && scaleUnit(p, p.features[0]) ? `${playerName(p)} ${SCALE_UNIT_BASELINE}` : mention(p);
    return p.share !== null ? `${head} ${percentText(p.share)}` : head;
  };
  let summary = NO_TOP_SENTENCE;
  if (top.length === 1) {
    const only = top[0];
    summary = !isGroup(only) && scaleUnit(only, only.features[0]) ? `${lead}${playerName(only)}뿐입니다(${SCALE_UNIT_BASELINE}).` : `${lead}${mention(only)}뿐입니다.`;
  } else if (top.length > 1) summary = `${lead}${top.map(item).join(", ")} 순입니다.`;

  // 음수 기여: φ < −tol인 참가자, 여럿이면 쉼표로 잇는다(`·`는 묶음 구성원 구분)
  const tol = attributionTolerance(a);
  const negatives = a.players.filter((p) => p.phi < -tol);
  const negativeNames = negatives.map(playerName).join(", ");
  const negative = negatives.length ? `${topic(negativeNames)} 원점수를 정상 쪽으로 움직였습니다. 그래서 다른 변수 비율 합이 100%를 넘을 수 있습니다.` : null;

  let counterfactual: string | null = null;
  // 문장 갈래는 저장된 warningAfterReplacement(clearedAtK)를 따르고, 판정선과 같은 점수는 값 자리만 `판정선과 같아져`로 바꾼다
  const lowered = (n: number) => (same(n) ? "원점수가 판정선과 같아져" : `원점수가 판정선보다 ${words.cross}(${shownValue(n)})`);
  const cleared = (k: number) => `${lowered(a.counterfactual[k - 1].scoreAfter)} 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.`;
  if (a.clearedAtK === 1) counterfactual = `${replacedAlone(top[0])} ${cleared(1)}`;
  else if (a.clearedAtK !== null) {
    const k = a.clearedAtK;
    const together = top.slice(0, k).map(mention).join("·");
    const firstScore = a.counterfactual[0].scoreAfter;
    const alone = same(firstScore)
      ? `${playerName(top[0])}만 바꾸면 원점수가 판정선과 같아 여전히 판정선 기준 이상 범위에 남습니다.`
      : `${playerName(top[0])}만 바꾸면 원점수는 ${toward(score(firstScore))} 여전히 판정선 기준 이상 범위에 남습니다.`;
    counterfactual = `${together}${objectParticle(together)} 함께 학습 데이터 중앙값으로 바꾸면 ${cleared(k)} ${alone}`;
  } else if (top.length) {
    const k = top.length;
    const last = a.counterfactual[k - 1].scoreAfter;
    counterfactual = same(last)
      ? `상위 ${k}개 변수를 학습 데이터 중앙값으로 바꾸면 원점수가 판정선과 같아져 여전히 판정선 기준 이상 범위에 남습니다.`
      : `상위 ${k}개 변수를 학습 데이터 중앙값으로 바꿔도 원점수가 판정선 기준 이상 범위에 남습니다(${shownValue(last)}).`;
  }

  return {
    summary,
    shareNull: shareNull ? SHARE_NULL_SENTENCE : null,
    negative,
    counterfactual,
    groups: a.players.filter(isGroup).map((p) => `${topic(playerName(p))} 같은 원천 값을 쓰는 변수라 한 묶음으로 계산했습니다.`),
    baselineWarning: a.baselineWarning ? BASELINE_WARNING_SENTENCE : null,
    limitation: LIMITATION_SENTENCE,
  };
};

/** 표 행(topK 순): 구성원별 이름·실제값·기준값 칸, 비율. 기준값 칸은 열 이름이 `학습 데이터 중앙값`이라 값만, scale 1 구성원은 `평소 값과 다름`. */
/** 행 key = playerId(유일), 구성원 key = 변수 이름(참가자 안에서 유일). */
export type AttributionTableRow = { playerId: string; members: { feature: string; name: string; actual: string; baseline: string }[]; share: string | null };
export const attributionTableRows = (a: AttributionSucceeded): AttributionTableRow[] => {
  const byId = new Map(a.players.map((p) => [p.playerId, p]));
  return a.topK.map((id) => {
    const p = byId.get(id) as AttributionPlayer;
    return {
      playerId: p.playerId,
      members: p.features.map((f) => ({ feature: f, name: featureName(f), actual: actualWithUnitNote(f, p.actual[f]),
        baseline: scaleUnit(p, f) ? SCALE_UNIT_BASELINE : valueText(f, p.baseline[f]) })),
      share: p.share === null ? null : percentText(p.share),
    };
  });
};
