import type { HardRule, RuleSetRequest, RuleSetState, Severity, SeverityByCode } from "../ruleSetApi";
import { requestFromState, severityLabels } from "../ruleDraft";
import type { CodeSeverity } from "./responseCodesApi";

/**
 * 응답 코드 화면 문구·저장 요청(contracts/response-codes-ui.md 4절). 근거:
 * - 사용자 11/12 "응답코드도 룰 설정화면에서 수정할 수 있으면 좋겠어. 기본값은 이파일대로" — 긴 목록은 목록 → 상세 하위 페이지
 * - 위험도 = 내장 하드룰 `hard:transaction-failed`(거래 실패)의 `{byCode, default}`. 저장은 룰 세트 저장(버전·되돌리기 그대로)
 * - '버전'(룰 세트 리비전 화면 이름, 사용자 11/04). 새 문장은 response-codes-ui.md 4절 목록에 함께 적는다.
 */
export const RESPONSE_CODE_TERM = "응답 코드";
export const TRANSACTION_FAILED_RULE = "hard:transaction-failed";
export const RESPONSE_CODE_LIST_LEAD = "거래 실패 규칙이 응답 코드별로 쓰는 위험도와 코드 설명입니다. 위험도를 바꾸면 룰 세트 새 버전으로 저장됩니다.";
export const RULE_OFF_NOTE = "거래 실패 규칙이 꺼져 있어 이 위험도는 지금 판정에 쓰이지 않습니다. 룰 설정에서 켤 수 있습니다.";
export const DICTIONARY_EMPTY = "응답 코드 사전이 비어 있습니다. 운영자가 가져오기 도구로 기본값을 넣어야 합니다.";
export const NOT_IN_DICTIONARY = "사전에 없음";
export const NOT_IN_DICTIONARY_NOTE = "룰 세트 위험도에만 등록된 코드입니다. 메시지를 저장하면 사전에 추가됩니다.";
export const FOLLOW_DEFAULT = "기본값 따름";
export const SEVERITY_APPLIES = "저장하면 룰 세트 새 버전으로 바로 운영에 적용됩니다(그 뒤 판정부터).";
export const MESSAGE_SAVED = "메시지를 저장했습니다.";
export const MESSAGE_CONFLICT = "다른 변경이 먼저 저장되어 최신 메시지를 다시 불러왔습니다. 확인한 뒤 다시 저장해 주세요.";
export const originLabels: Record<"import" | "edited", string> = { import: "가져온 값", edited: "화면에서 수정" };
export const changeKindLabels: Record<"import" | "edit", string> = { import: "가져오기", edit: "화면에서 수정" };

/** `위험` / `주의(기본값)`. */
export const codeSeverityText = (severity: CodeSeverity) =>
  `${severityLabels[severity.level]}${severity.source === "default" ? "(기본값)" : ""}`;

/** 현재 룰 세트의 거래 실패 규칙. 모양이 `{byCode, default}`가 아니면 null(편집하지 않음). */
export const failedRule = (state: RuleSetState) => {
  const rule = state.hardRules.find((item) => item.id === TRANSACTION_FAILED_RULE);
  const severity = rule?.severity;
  if (!rule || typeof severity !== "object" || severity === null || !severity.default) return null;
  return { rule, severity: severity as Required<SeverityByCode> };
};
export const codeSeverityIn = (severity: Required<SeverityByCode>, code: string): CodeSeverity =>
  Object.hasOwn(severity.byCode, code) ? { level: severity.byCode[code], source: "code" } : { level: severity.default, source: "default" };

/** 그 코드의 위험도만 바꾼 전체 저장 요청. level null = byCode에서 빼서 기본값을 따른다. 다른 규칙·코드는 현재 값 그대로. */
export const severityRequest = (state: RuleSetState, code: string, level: Severity | null, confirm: boolean): RuleSetRequest => {
  const request = requestFromState(state, confirm);
  return {
    ...request,
    hardRules: request.hardRules.map((item) => {
      if (item.id !== TRANSACTION_FAILED_RULE || typeof item.severity !== "object" || item.severity === null) return item;
      const byCode = { ...item.severity.byCode };
      if (level === null) delete byCode[code];
      else byCode[code] = level;
      return { ...item, severity: { ...item.severity, byCode } };
    }),
  };
};
/** 룰 설정 요약 카드: 코드별 등록 수·그 밖의 코드 단계. */
export const summaryText = (rule: HardRule, severity: Required<SeverityByCode>) =>
  `거래 실패 규칙의 코드별 위험도 · 코드별 ${Object.keys(severity.byCode).length}개 · 그 밖의 코드 ${severityLabels[severity.default]}${rule.enabled ? "" : " · 규칙 꺼짐"}`;
