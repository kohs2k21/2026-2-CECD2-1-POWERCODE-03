import { criteriaText } from "@/features/analysis/incidents/incidentLabels";
import type { DecisionBasis, DecisionNotification, IncidentNotification, NotificationItem, NotificationView, ProtocolNotification, RiskFloor } from "./notificationsApi";

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
  // 이상이 아닌 최종(재처리 대체 결과): 이상 분석의 기존 결과 문구(판정불가·이상 신호 없음·정상 여부 미확인)를 쓴다.
  if (decision.stage === "cleared")
    return `철회 · 최종 ${decision.completeness === "unavailable" ? "판정불가" : decision.finalSignal === "no" ? "이상 신호 없음" : "정상 여부 미확인"}`;
  const result = decision.grade === "anomaly_sign" ? "이상 징후" : "이상";
  const stage = { interim: "중간 판정", updated: "근거 추가", final: "최종", superseded: "이전 시도 대체됨" }[decision.stage];
  return `${result} · ${stage}${reason === "risk_raised" ? " · 위험도 상승" : ""}`;
};
const decisionSubject = ({ decision }: DecisionNotification) =>
  [decision.transactionId && `거래 ${decision.transactionId}`, decision.processId && `프로세스 ${decision.processId}`].filter(Boolean).join(" · ") || "PROCESS 판정";

const incidentReasonLabels: Record<IncidentNotification["reason"], string> = { opened: "새 사건", grew: "판정 추가", risk_raised: "위험도 상승" };
const incidentHeadline = ({ reason, incident }: IncidentNotification) => `사건 · ${criteriaText(incident.criteria)} · ${incidentReasonLabels[reason]}`;
const incidentSubject = ({ incident }: IncidentNotification) => `묶인 판정 ${incident.memberDecisions.toLocaleString("ko-KR")}건`;

/** 프로토콜 알림(결정 11/13 "처음 보는 경로·인터페이스 = 이상 + 알림"). 경로는 채널을 화살표로 잇는다(원래 값). */
const protocolHeadline = ({ protocol }: ProtocolNotification) =>
  `정상 프로토콜 확인 · ${protocol.kind === "interface_unregistered" ? "등록되지 않은 인터페이스" : "처음 보는 경로"}`;
const protocolSubject = ({ protocol }: ProtocolNotification) =>
  [`인터페이스 ${protocol.interfaceId}`, protocol.channels ? `경로 ${protocol.channels.join(" → ")}` : "경로를 만들 수 없음",
    `판정 ${protocol.decisions.toLocaleString("ko-KR")}건`].join(" · ");
export const protocolResolutionLabels: Record<"kept" | "added", string> = { kept: "이상 유지됨", added: "정상 프로토콜에 추가됨" };

export const notificationText = (item: NotificationItem) =>
  item.type === "decision" ? { headline: decisionHeadline(item), subject: decisionSubject(item) }
    : item.type === "incident" ? { headline: incidentHeadline(item), subject: incidentSubject(item) }
      : { headline: protocolHeadline(item), subject: protocolSubject(item) };
export const notificationRisk = (item: NotificationItem) =>
  item.type === "decision" ? item.decision : item.type === "incident" ? item.incident : { riskStatus: "unrated" as const, riskLevel: null, hasUnratedEvidence: false };
/** 철회 알림은 위험도 대신 철회만 보인다. 프로토콜 알림은 위험도 없음(판정 상세에서 확인). */
export const showsRisk = (item: NotificationItem) =>
  item.type !== "protocol" && !(item.type === "decision" && (item.decision.stage === "no_final" || item.decision.stage === "cleared"));
