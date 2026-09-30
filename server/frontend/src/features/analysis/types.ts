import type { Icon } from "@tabler/icons-react";
import type {
  AnomalyStatus,
  MessageBodyPreview,
  MessageSnapshot,
  ProcessSnapshot,
  Severity,
  TransactionSnapshot,
} from "../../types/domain";

export type SeverityFilter = Severity;
export type AnalysisStatus = AnomalyStatus;
export type AnalysisCategory = "All" | SeverityFilter | "Open" | "Resolved";
export type StatusOverrides = Record<string, AnalysisStatus>;

export type CategoryTheme = {
  icon: Icon;
  label: string;
  group: "severity" | "workflow" | "all";
  className: string;
};

export type SortOption = "severity_desc" | "severity_asc" | "time_desc" | "time_asc";
export type TypingPhase = "summary" | "cause" | "action" | "done";

export type AnalysisNode =
  | {
      type: "focusProcess" | "contextProcess";
      id: string;
      label: string;
      data: ProcessSnapshot;
    }
  | {
      type: "transactionContext";
      id: string;
      label: string;
      data: TransactionSnapshot;
    }
  | {
      type: "message";
      id: string;
      label: string;
      data: MessageSnapshot;
    }
  | {
      type: "body";
      id: string;
      label: string;
      data: MessageBodyPreview;
    };
