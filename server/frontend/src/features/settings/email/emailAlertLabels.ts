import type { DeliveryKind, DeliveryStatus, RecipientProblem, SmtpSecurity, TestSendResult, TransportStatus, VerifyResult } from "./emailAlertsApi";

/** 설정 › 연동 관리 › 메일 알림 문구(사용자 승인 11/21, 승인표 밖 문구 없음). */
export const emailText = {
  enable: "메일 알림 켜기",
  host: "SMTP 호스트",
  port: "포트",
  security: "보안",
  username: "발신 계정",
  password: "비밀번호",
  passwordSaved: "저장됨 · 바꿀 때만 입력",
  clearPassword: "비밀번호 지우기",
  fromAddress: "보내는 주소",
  publicBaseUrl: "대시보드 주소",
  publicBaseUrlHelp: "메일 링크 앞부분 (예: http://서버:8080)",
  kinds: "보낼 알림",
  recipients: "받는 사람",
  add: "추가",
  remove: "삭제",
  save: "저장",
  saveError: "메일 알림 설정을 저장하지 못했습니다",
  keyError: "암호화 키를 쓸 수 없어 비밀번호를 저장하지 못했습니다.",
  testSend: "시험 발송",
  testSendGo: "보내기",
  testSendCancel: "취소",
  recent: "최근 발송",
  recentColumns: ["시각", "종류", "받는 사람", "결과"] as const,
  recentEmpty: "보낸 메일이 없습니다.",
  processingLag: "사건 묶음 처리 대기 중",
  adminOnly: "메일 알림 설정은 관리자만 바꿀 수 있습니다.",
} as const;

export const securityLabels: Record<SmtpSecurity, string> = { tls: "TLS 접속", starttls: "STARTTLS 필수" };
export const kindLabels = { incident: "사건", decision: "묶이지 않은 판정" } as const;
export const transportLabels: Record<TransportStatus, string> = {
  configured: "설정됨",
  not_configured: "미설정",
  invalid: "설정 오류",
  auth_failed: "인증 실패",
  error: "연결 오류",
};
export const transportVariant: Record<TransportStatus, "success" | "secondary" | "warning" | "critical"> = {
  configured: "success",
  not_configured: "secondary",
  invalid: "warning",
  auth_failed: "critical",
  error: "critical",
};
export const deliveryKindLabels: Record<DeliveryKind, string> = { incident: "사건", decision: "판정", test: "시험" };
export const deliveryStatusLabels: Record<DeliveryStatus, string> = {
  queued: "대기",
  sending: "보내는 중",
  sent: "보냄",
  retrying: "다시 시도 대기",
  failed: "실패",
  unknown: "확인 불가",
  suppressed: "발송 제한으로 생략",
};
export const recipientProblemLabels: Record<RecipientProblem, string> = {
  format: "메일 주소 형식이 아닙니다.",
  duplicate: "이미 있는 주소입니다.",
  max: "최대 10개까지 넣을 수 있습니다.",
};

const withCode = (text: string, code: string | null | undefined) => (code ? `${text} · ${code}` : text);
/** 전송 상태 옆 줄: `마지막 확인 <시각>`, 코드가 있으면 ` · <code>`. 둘 다 없으면 빈 문자열. */
export const transportDetail = (checkedAt: string | null, code: string | null, stamp: (iso: string) => string) =>
  [checkedAt && `마지막 확인 ${stamp(checkedAt)}`, code].filter(Boolean).join(" · ");
/** 저장 결과(응답 verify): 연결 확인 성공 / 실패 · 코드 / 확인 안 함(SMTP 값 그대로). */
export const verifyText = ({ result, code }: VerifyResult) =>
  result === "ok" ? "저장됨 · 연결 확인 성공" : result === "skipped" ? "저장됨" : withCode("저장됨 · 연결 확인 실패", code);
/** 시험 발송 결과: 보냄 = 완료, 실패 = 실패 · 코드, 결과 모름(unknown) = 발송 상태 `확인 불가` 그대로(실패로 바꾸지 않음). */
export const testResultText = (result: TestSendResult) =>
  result.status === "sent" ? "시험 발송 완료" : result.status === "unknown" ? deliveryStatusLabels.unknown : withCode("시험 발송 실패", result.errorCode);
/** 시험 발송 요청 자체의 오류(404 받는 사람 아님·409 보낼 수 없는 설정·429 30초에 1번·저장소 오류 등). */
export const testErrorText = (code: string) => withCode("시험 발송 실패", code);
export const testConfirmText = (address: string) => `${address}로 시험 메일을 보냅니다.`;
export const removeLabel = (address: string) => `${address} 삭제`;
export const recipientCount = (count: number, max: number) => `${count}/${max}`;
export const saveErrorDetail = (code: string) => (code === "email_secret_key_unavailable" || code === "email_secret_key_invalid" ? emailText.keyError : code);
