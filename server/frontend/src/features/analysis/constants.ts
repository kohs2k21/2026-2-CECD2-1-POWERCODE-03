import {
  IconAlertTriangle,
  IconCircleCheck,
  IconFlame,
  IconFolderOpen,
  IconInbox,
  IconInfoCircle,
} from "@tabler/icons-react";
import type { Severity } from "@/types/realtime";
import type { AnalysisCategory, CategoryTheme } from "./types";

export const categoryOrder: AnalysisCategory[] = ["All", "Critical", "Warning", "Info", "Open", "Resolved"];

export const categoryThemeMap: Record<AnalysisCategory, CategoryTheme> = {
  All: { icon: IconInbox, label: "전체", group: "all" },
  Critical: { icon: IconFlame, label: "위험", group: "severity" },
  Warning: { icon: IconAlertTriangle, label: "주의", group: "severity" },
  Info: { icon: IconInfoCircle, label: "참고", group: "severity" },
  Open: { icon: IconFolderOpen, label: "보류", group: "workflow" },
  Resolved: { icon: IconCircleCheck, label: "완료", group: "workflow" },
};

/** 위험도 → 배지 색(토큰). */
export const severityBadgeVariant: Record<Severity, "critical" | "warning" | "info"> = {
  Critical: "critical",
  Warning: "warning",
  Info: "info",
};
