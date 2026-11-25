/**
 * AI 판정 근거 요약(evidenceSummary)의 값 표기·조사 유틸. 저장값만 쓰고 새 수치를 계산하지 않는다(표기 변환만).
 * - 지수 표기 펼치기, 정수 부분 천 단위 쉼표, 0인 음수의 부호 없음, 10진 문자열 half-up 반올림(0에서 먼 쪽), 비율 정수 %
 * - 조사: 앞 낱말 마지막 글자 받침(끝 괄호 덧말은 떼고, 숫자는 읽는 소리, 영문은 끝 글자 L·M·N·NG만 받침, 단위가 붙으면 단위 읽는 소리)
 * 예외를 던지지 않는다(숫자로 읽을 수 없으면 원문 그대로).
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
/** 을/를(조사만) */
export const objectParticle = (word: string) => (lastSound(word).batchim ? "을" : "를");
/** 으로/로(조사만, 받침 없거나 ㄹ이면 로) */
export const towardParticle = (word: string) => {
  const s = lastSound(word);
  return s.batchim && !s.rieul ? "으로" : "로";
};
/** 이/가(조사만) */
export const subjectParticle = (word: string) => (lastSound(word).batchim ? "이" : "가");
/** 과/와(조사만) */
export const withParticle = (word: string) => (lastSound(word).batchim ? "과" : "와");
export const toward = (word: string) => `${word}${towardParticle(word)}`;
export const object = (word: string) => `${word}${objectParticle(word)}`;
export const subject = (word: string) => `${word}${subjectParticle(word)}`;
export const withWord = (word: string) => `${word}${withParticle(word)}`;

// ---- 표시 자릿수 ----
/** 단위가 ms인 값의 소수 자릿수. */
export const MS_DECIMALS = 1;
/** 그 밖의 값(비율 등)의 유효숫자 수. 정수 부분은 줄이지 않는다. */
export const SIGNIFICANT_DIGITS = 4;
const trimZeros = (text: string) => (text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text);
/**
 * 표·문장 공통 표시 숫자(지수 펼침 → 자릿수 → 천 단위 쉼표). 원래 값이 자릿수보다 짧으면 그대로 둔다.
 * - 단위 ms: 소수 MS_DECIMALS자리 half-up(끝의 0·점 제거, 정수면 정수)
 * - 그 밖: 유효숫자 SIGNIFICANT_DIGITS자리 half-up(정수 부분은 줄이지 않음, 끝의 0 제거)
 * 10진 표기가 아니면 원문 그대로.
 */
export const displayNumber = (raw: string, unit: string | null = null): string => {
  if (raw.length > NUMBER_TEXT_LIMIT) return raw;
  const plain = expandExponent(raw.trim());
  const m = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(plain);
  if (!m) return groupDigits(raw);
  const int = m[2].replace(/^0+(?=\d)/, "");
  const frac = m[3] ?? "";
  let decimals: number;
  if (unit === "ms") decimals = MS_DECIMALS;
  else if (int !== "0") decimals = Math.max(0, SIGNIFICANT_DIGITS - int.length);
  else {
    const lead = /^0*/.exec(frac)![0].length; // 0.000123 → 첫 유효숫자 앞 0 개수
    decimals = lead === frac.length ? frac.length : lead + SIGNIFICANT_DIGITS;
  }
  if (frac.length <= decimals) return groupDigits(plain);
  return groupDigits(trimZeros(roundHalfUp(plain, decimals)));
};
