import type { Icon } from "@tabler/icons-react";
import type { Severity } from "@/types/realtime";

export type AnalysisCategory = "All" | Severity | "Open" | "Resolved";

export type CategoryTheme = {
  icon: Icon;
  label: string;
  group: "severity" | "workflow" | "all";
};

export type SortOption = "severity_desc" | "severity_asc" | "time_desc" | "time_asc";
