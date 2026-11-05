import {
  IconAlertCircle,
  IconAlertOctagon,
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleX,
  IconClock,
  IconFlame,
  IconFolderOpen,
  IconHelpCircle,
  IconInbox,
  IconInfoCircle,
  IconQuestionMark,
  IconRefresh,
  type Icon,
} from "@tabler/icons-react";
import type { Category, RiskLevel, WorkflowState } from "./data/analysisApi";

/**
 * 이상 분석 장식 아이콘. 위험도·분류함·처리 상태는 #100 이전 화면(`constants.ts` categoryThemeMap·`AnalysisDetailView` 처리 상태 버튼)과 같은 Tabler 아이콘이고,
 * 이전 대응이 없던 판정 결과별 분류와 위험도 미정은 사이드바가 어긋나지 않게 뜻이 맞는 아이콘을 더했다(사용자 11/05).
 */
export const categoryIcons: Record<Category, Icon> = {
  all: IconInbox,
  anomaly: IconAlertOctagon,
  anomaly_sign: IconAlertCircle,
  in_progress: IconClock,
  undetermined: IconQuestionMark,
  failed_replaced: IconCircleX,
  risk_all: IconInbox,
  risk_critical: IconFlame,
  risk_warning: IconAlertTriangle,
  risk_info: IconInfoCircle,
  risk_unrated: IconHelpCircle,
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
