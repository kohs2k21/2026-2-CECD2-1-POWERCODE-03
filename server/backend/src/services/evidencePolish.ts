/**
 * AI 판정 근거 요약 블록 2 다듬기(설명 작업 `explain`) — 접수 입력·계보 키·결과 판독. DB를 읽지 않는 순수 함수다.
 * - 접수 입력: 판정 상세와 같은 조립 함수(evidenceSummary.ts)로 만든 코드 문장·판정 낱말·원천 값만 보낸다(워커는 근거를 읽지 않는다)
 * - 정본 JSON: 키 정렬, 구분자 `,`·`:`, 비ASCII 그대로(UTF-8). 입력 hash = 정본 입력 중 판정·구성·틀 버전·문장·판정 낱말·원천 값의 SHA-256(요청자·원천 제외)
 * - 재사용 키: 요청자 없이 판정마다 공유 — [판정, 틀 버전, 입력 hash, 모델 버전, 지시문 버전, 결과 형식 버전]의 정본 JSON SHA-256
 * - 결과 판독: 결과 형식 버전이 같고 판정·틀 버전이 같은 결과만 `polished`로 싣는다. 이전 형식(사실 고르기) 결과는 보이지 않는다
 */
import crypto from 'node:crypto';
import type { EvidenceSummary } from './evidenceSummary.js';

/** 접수 입력 형식·지시문·결과 형식 버전(워커와 같은 값이어야 한다). 바꾸면 재사용 키가 갈려 이전 결과와 섞이지 않는다. */
export const POLISH_EVIDENCE_VERSION = 'summary-sentences-v1';
export const POLISH_PROMPT_VERSION = 'evidence-polish-v1';
export const POLISH_OUTPUT_SCHEMA_VERSION = 'evidence-polish-result-v1';
/** 생성할 수 없어 실패한 작업은 끝난 뒤 이 시간 안에는 다시 만들지 않고 그 실패 작업을 돌려준다. */
export const POLISH_FAILURE_COOLDOWN_MINUTES = 10;
/** 이 종류 작업은 다시 시도하지 않는다(run_jobs.max_attempts). */
export const POLISH_MAX_ATTEMPTS = 1;
/** 접수 입력 상한(코드 포인트). */
export const POLISH_LIMITS = { sentences: 8, sentenceLength: 300, totalLength: 1200, valueTokens: 16, valueTokenLength: 120 } as const;

/** 정본 JSON: 객체 키를 코드 포인트 순으로 정렬, 공백 없음, 비ASCII 그대로(JSON.stringify와 같은 이스케이프). */
export const canonicalJson = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
    // 코드 포인트 순(UTF-16 단위 비교가 아니라 Python sort_keys와 같은 순서)
    entries.sort(([a], [b]) => {
      const x = [...a], y = [...b];
      for (let i = 0; i < Math.min(x.length, y.length); i += 1) if (x[i] !== y[i]) return x[i].codePointAt(0)! - y[i].codePointAt(0)!;
      return x.length - y.length;
    });
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(',')}}`;
  }
  return JSON.stringify(value);
};
const sha256 = (text: string) => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
const length = (text: string) => [...text].length;

export type PolishInput = {
  kind: 'explain';
  sourceId: string;
  decisionId: string;
  configVersionId: string | null;
  templateVersion: string;
  sentences: string[];
  stateWords: string[];
  valueTokens: string[];
};
/**
 * 워커 입력 검사(설명 워커 `parse_input`)가 거부하는 문장·원천 값을 접수 전에 거른다(같은 조건 또는 더 엄격). 한 곳에서만 정의한다.
 * - 문장: 1~8개, 공백만인 문장 없음, 각 300자·합 1,200자(코드 포인트), 줄바꿈류 문자(\n \r \v \f \x1c–\x1e \x85 \u2028 \u2029) 없음,
 *   사설 영역 문자(U+E000–U+F8FF, 워커 가림 자리표 영역) 없음
 * - 원천 값: 0~16개, 각 1~120자, 각각 어떤 한 문장 안에 그대로 있음(문장 사이 이음 공백에 걸친 값은 안 됨)
 */
const LINE_BREAK = /[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/;
const PRIVATE_USE = /[\uE000-\uF8FF]/;
export const polishInputProblem = (sentences: string[], valueTokens: string[]): string | null => {
  const L = POLISH_LIMITS;
  if (!sentences.length || sentences.length > L.sentences) return 'sentence_count';
  if (sentences.some((s) => !s.trim() || length(s) > L.sentenceLength)) return 'sentence_length';
  if (sentences.reduce((sum, s) => sum + length(s), 0) > L.totalLength) return 'total_length';
  if (sentences.some((s) => LINE_BREAK.test(s) || PRIVATE_USE.test(s))) return 'sentence_character';
  if (valueTokens.length > L.valueTokens) return 'value_token_count';
  if (valueTokens.some((t) => !t || length(t) > L.valueTokenLength || !sentences.some((s) => s.includes(t)))) return 'value_token';
  return null;
};
/**
 * 접수 입력. 워커가 거부할 입력이면 null(접수하지 않음, polishInputProblem).
 * allowedNumbers는 보내지 않는다(선택 키 — 워커가 문장에서 직접 뽑는다).
 */
export const buildPolishInput = (decision: { sourceId: string; decisionId: string; configVersionId: string | null }, summary: EvidenceSummary): PolishInput | null => {
  const { sentences, stateWords, valueTokens, templateVersion } = summary;
  if (polishInputProblem(sentences, valueTokens)) return null;
  return { kind: 'explain', sourceId: decision.sourceId, decisionId: decision.decisionId, configVersionId: decision.configVersionId, templateVersion,
    sentences: [...sentences], stateWords: [...stateWords], valueTokens: [...valueTokens] };
};
/** 입력 hash(요청자·원천 제외). */
export const polishEvidenceHash = (input: Pick<PolishInput, 'decisionId' | 'configVersionId' | 'templateVersion' | 'sentences' | 'stateWords' | 'valueTokens'>) =>
  sha256(canonicalJson({ decisionId: input.decisionId, configVersionId: input.configVersionId, templateVersion: input.templateVersion,
    sentences: input.sentences, stateWords: input.stateWords, valueTokens: input.valueTokens }));
export type PolishLlmSpec = { evidence_version: string; input_evidence_hash: string; model_version: string; prompt_version: string; output_schema_version: string; reuse_key: string };
/** 작업 계보(llm_spec). modelVersion = 접수 때 생성 서비스 상태가 알린 다듬기 모델 id. */
export const polishLlmSpec = (input: PolishInput, modelVersion: string): PolishLlmSpec => {
  const hash = polishEvidenceHash(input);
  return {
    evidence_version: POLISH_EVIDENCE_VERSION,
    input_evidence_hash: hash,
    model_version: modelVersion,
    prompt_version: POLISH_PROMPT_VERSION,
    output_schema_version: POLISH_OUTPUT_SCHEMA_VERSION,
    reuse_key: sha256(canonicalJson([input.decisionId, input.templateVersion, hash, modelVersion, POLISH_PROMPT_VERSION, POLISH_OUTPUT_SCHEMA_VERSION])),
  };
};

export type Polished = { text: string; source: 'llm' | 'code'; check: { status: string; code: string | null } | null; modelVersion: string | null };
export type PolishState = 'pending' | 'done' | 'none';
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
/** 결과(run_llm_results.output) → polished. 새 결과 형식이고 같은 판정·틀 버전이며 문단이 문자열일 때만. */
export const readPolished = (output: unknown, expected: { decisionId: string; templateVersion: string }, modelVersion: unknown = null): Polished | null => {
  if (!record(output) || output.outputSchemaVersion !== POLISH_OUTPUT_SCHEMA_VERSION) return null;
  if (output.decisionId !== expected.decisionId || output.templateVersion !== expected.templateVersion) return null;
  const paragraph = record(output.paragraph) ? output.paragraph : null;
  if (!paragraph || typeof paragraph.text !== 'string' || !paragraph.text.trim() || (paragraph.source !== 'llm' && paragraph.source !== 'code')) return null;
  const check = record(output.check) && typeof output.check.status === 'string'
    ? { status: output.check.status, code: typeof output.check.code === 'string' ? output.check.code : null } : null;
  const model = typeof output.modelVersion === 'string' ? output.modelVersion : typeof modelVersion === 'string' ? modelVersion : null;
  return { text: paragraph.text, source: paragraph.source, check, modelVersion: model };
};
/** 다듬기 대상 판정 항목(resolveItem 결과에서 쓰는 키). */
export type PolishItem = { kind: string; signal: string; decisionId: string | null; sourceId: string; configVersionId: string | null; evidence: unknown };
const isDemo = (evidence: unknown) => record(evidence) && evidence.demo !== undefined && evidence.demo !== null;
/**
 * 판정 → 다듬기 접수 입력(접수·판정 상세가 같이 쓴다). 최종 판정 + 이상 신호 + 시연 판정 아님 + 입력을 만들 수 있을 때만, 아니면 null.
 */
export const polishInputFor = (item: PolishItem, summary: EvidenceSummary | null): PolishInput | null => {
  if (!summary || item.kind !== 'final' || item.signal !== 'yes' || !item.decisionId || isDemo(item.evidence)) return null;
  return buildPolishInput({ sourceId: item.sourceId, decisionId: item.decisionId, configVersionId: item.configVersionId ?? null }, summary);
};
/** 화면이 지금 자동 요청해도 되는지: 다듬을 수 있는 판정 + 결과·진행 중 작업 없음(none) + 생성 서비스가 다듬기 모델을 알림(ready). */
export const polishRequestableOf = (input: PolishInput | null, state: PolishState, ready: boolean) => input !== null && state === 'none' && ready;
/** 완료 결과가 있으면 done, 같은 입력의 진행 중 작업이 있으면 pending, 그 밖 none(모델 버전은 보지 않는다). */
export const polishStateOf = (polished: Polished | null, pending: boolean): PolishState => (polished ? 'done' : pending ? 'pending' : 'none');
