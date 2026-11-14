import type { DetectionMenuId } from "@/app/routes";
import { HARD_TERM, PROTOCOL_TERM, RELATION_TERM, SOFT_TERM } from "./rules/ruleDraft";
import { RESPONSE_CODE_TERM } from "./rules/responsecodes/responseCodeLabels";

export type DetectionSection = { id: string; label: string };

/**
 * 탐지 설정 메뉴 첫 화면의 영역(사이드바 목차 단일 기준). id는 화면 영역 요소 id = 주소 해시(#id),
 * label은 화면 영역 제목 그대로다(각 영역이 같은 값을 제목으로 쓴다).
 */
export const detectionSections = {
  operationsStatus: { id: "operations-status", label: "운영 버전 상태" },
  operationsHistory: { id: "operations-history", label: "모델 교체 이력" },
  operationsJobs: { id: "operations-jobs", label: "작업" },
  rulesHard: { id: "rules-hard", label: HARD_TERM },
  rulesSoft: { id: "rules-soft", label: SOFT_TERM },
  rulesRelation: { id: "rules-relation", label: RELATION_TERM },
  rulesProtocols: { id: "rules-protocols", label: PROTOCOL_TERM },
  rulesResponseCodes: { id: "rules-response-codes", label: RESPONSE_CODE_TERM },
  rulesCandidates: { id: "rules-candidates", label: "규칙 추천" },
  rulesHistory: { id: "rules-history", label: "변경 기록" },
  modelsVersions: { id: "models-versions", label: "버전" },
  modelsTrain: { id: "models-train", label: "새 학습" },
  evaluationsList: { id: "evaluations-list", label: "비교 평가" },
  evaluationsNew: { id: "evaluations-new", label: "새 비교 평가" },
} as const satisfies Record<string, DetectionSection>;

const s = detectionSections;
/** 메뉴별 목차(화면 위→아래 순서). 조건부 영역(응답 코드 등)이 화면에 없으면 해당 항목을 눌러도 아무 일 없다. */
export const detectionToc: Record<DetectionMenuId, readonly DetectionSection[]> = {
  operations: [s.operationsStatus, s.operationsHistory, s.operationsJobs],
  rules: [s.rulesHard, s.rulesSoft, s.rulesRelation, s.rulesProtocols, s.rulesResponseCodes, s.rulesCandidates, s.rulesHistory],
  models: [s.modelsVersions, s.modelsTrain],
  evaluations: [s.evaluationsList, s.evaluationsNew],
};
