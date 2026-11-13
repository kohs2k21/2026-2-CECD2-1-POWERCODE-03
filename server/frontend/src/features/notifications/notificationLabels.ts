import { criteriaText } from "@/features/analysis/incidents/incidentLabels";
import type { DecisionBasis, DecisionNotification, IncidentNotification, NotificationItem, NotificationView, RiskFloor } from "./notificationsApi";

/** 알림 패널·알림 설정 문구(와이어프레임 #93 bell·notifSettings, 화면 흐름 결정 2.2). */
export const viewLabels: Record<NotificationView, string> = { unread: "읽지 않음", all: "전체" };
export const emptyLabels: Record<NotificationView, string> = { unread: "읽지 않은 알림이 없습니다.", all: "알림이 없습니다." };
export const decisionBasisLabels: Record<DecisionBasis, string> = { interim: "중간 판정부터", final_any: "최종(부분 포함)", final_complete: "최종 완료만" };
export const riskFloorLabels: Record<RiskFloor, string> = { critical: "위험", warning: "주의 이상", info: "참고 이상" };
const riskLabels: Record<RiskFloor, string> = { critical: "위험", warning: "주의", info: "참고" };

/** 배지 숫자(99 초과는 99+, 와이어프레임 setBadge). */
export const badgeText = (count: number) => (count > 99 ? "99+" : String(count));
export const bellLabel = (count: number | undefined) => (count ? `알림 · 읽지 않음 ${count}건` : "알림");
export const savedSettingsMessage = (role: "admin" | "user") =>
  `${role === "admin" ? "관리자" : "일반 사용자"} 계정 알림 설정 반영 · 알림 목록·배지에 바로 적용`;

export const riskText = (item: { riskStatus: "rated" | "unrated"; riskLevel: RiskFloor | null }) =>
  item.riskStatus === "rated" && item.riskLevel ? riskLabels[item.riskLevel] : "위험도 미정";

/** 판정 알림 첫 줄: 결과 · 단계(와이어프레임 stageLabel, 철회 문구 N-5·N-6). */
const decisionHeadline = ({ reason, decision }: DecisionNotification) => {
  if (decision.stage === "no_final") return "철회 · 판정 실패(최종 없음)";
  if (decision.stage === "cleared") return "철회 · 최종 판정 이상 아님";
  const result = decision.grade === "anomaly_sign" ? "이상 징후" : "이상";
  const stage = { interim: "중간 판정", updated: "근거 추가", final: "최종", superseded: "이전 시도 대체됨" }[decision.stage];
  return `${result} · ${stage}${reason === "risk_raised" ? " · 위험도 상승" : ""}`;
};
const decisionSubject = ({ decision }: DecisionNotification) =>
  [decision.transactionId && `거래 ${decision.transactionId}`, decision.processId && `프로세스 ${decision.processId}`].filter(Boolean).join(" · ") || "PROCESS 판정";

const incidentReasonLabels: Record<IncidentNotification["reason"], string> = { opened: "새 사건", grew: "판정 추가", risk_raised: "위험도 상승" };
const incidentHeadline = ({ reason, incident }: IncidentNotification) => `사건 · ${criteriaText(incident.criteria)} · ${incidentReasonLabels[reason]}`;
const incidentSubject = ({ incident }: IncidentNotification) => `묶인 판정 ${incident.memberDecisions.toLocaleString("ko-KR")}건`;

export const notificationText = (item: NotificationItem) =>
  item.type === "decision" ? { headline: decisionHeadline(item), subject: decisionSubject(item) } : { headline: incidentHeadline(item), subject: incidentSubject(item) };
export const notificationRisk = (item: NotificationItem) => (item.type === "decision" ? item.decision : item.incident);
/** 철회 알림은 위험도 대신 철회만 보인다. */
export const showsRisk = (item: NotificationItem) => !(item.type === "decision" && (item.decision.stage === "no_final" || item.decision.stage === "cleared"));
