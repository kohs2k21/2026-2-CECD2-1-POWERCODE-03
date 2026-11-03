import {
  IconAlertTriangle,
  IconCircleCheck,
  IconFlame,
  IconFolderOpen,
  IconInbox,
  IconInfoCircle,
  IconRefresh,
  type Icon,
} from "@tabler/icons-react";
import type { Category, RiskLevel, WorkflowState } from "./data/analysisApi";

/**
 * 이상 분석 장식 아이콘(#100 이전 화면 `constants.ts` categoryThemeMap·`AnalysisDetailView` 처리 상태 버튼과 같은 Tabler 아이콘).
 * 이전 화면에 대응 항목이 있던 분류만 둔다(판정 결과별 이상·이상 징후·판정 중·미확인·실패, 위험도 미정은 대응 없음 → 아이콘 없음).
 */
export const categoryIcons: Partial<Record<Category, Icon>> = {
  all: IconInbox,
  risk_all: IconInbox,
  risk_critical: IconFlame,
  risk_warning: IconAlertTriangle,
  risk_info: IconInfoCircle,
  workflow_open: IconFolderOpen,
  workflow_resolved: IconCircleCheck,
};

/** 위험도 단계 아이콘(이전 화면 심각도 Critical·Warning·Info). */
export const riskLevelIcons: Record<RiskLevel, Icon> = {
  critical: IconFlame,
  warning: IconAlertTriangle,
  info: IconInfoCircle,
};
/** 위험도 단계 아이콘 색: 현재 위험도 배지(riskLevelVariant critical·warning·secondary)와 같은 글자 색 토큰. */
export const riskLevelIconTone: Record<RiskLevel, string> = {
  critical: "text-destructive-text",
  warning: "text-warning-text",
  info: "text-secondary-foreground",
};

/** 처리 상태 버튼(다음 상태 기준): 원래 상태로 복원 = IconRefresh, 처리 보류 = IconFolderOpen, 처리 완료 = IconCircleCheck. */
export const workflowIcons: Record<WorkflowState, Icon> = {
  detected: IconRefresh,
  open: IconFolderOpen,
  resolved: IconCircleCheck,
};
