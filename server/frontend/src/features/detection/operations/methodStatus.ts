import type { MethodStatus } from "./operationsApi";

/**
 * 방법별 작동 상태 배지(사용자 11/02 9.7·9.8, 와이어프레임 statusOf). 서버는 센 값만 주고 기준은 화면이 정한다.
 * - 실행 0: 적용 뒤 firstRunWaitMs 안이면 '적용됨 · 첫 실행 대기', 지나면(또는 켜진 시각을 모르면) '점검 필요 · 실행 없음'
 * - 미판정 비율 10% 이상 '점검 필요', 2% 이상 또는 기한 초과 2% 이상 '주의', 그 밖 '정상'
 */
export const ATTENTION_RATIO = 0.02;
export const INSPECT_RATIO = 0.1;
export type StatusTone = "success" | "warning" | "critical" | "secondary";
export type MethodBadge = { label: string; tone: StatusTone; why: string };

export const undecided = (method: Pick<MethodStatus, "notReady" | "failed">) => method.notReady + method.failed;

export const ratioText = (ratio: number) => `${(ratio * 100).toFixed(1).replace(/\.0$/, "")}%`;

export function methodBadge(method: MethodStatus, firstRunWaitMs: number, now: number): MethodBadge {
  if (method.runs === 0) {
    const since = method.enabledSince ? Date.parse(method.enabledSince) : Number.NaN;
    return Number.isFinite(since) && now - since < firstRunWaitMs
      ? { label: "적용됨 · 첫 실행 대기", tone: "secondary", why: "" }
      : { label: "점검 필요", tone: "critical", why: "실행 없음" };
  }
  const ratio = undecided(method) / method.runs;
  if (ratio >= INSPECT_RATIO) return { label: "점검 필요", tone: "critical", why: `미판정 ${ratioText(ratio)}` };
  if (ratio >= ATTENTION_RATIO || method.timeouts / method.runs >= ATTENTION_RATIO) return { label: "주의", tone: "warning", why: `미판정 ${ratioText(ratio)}` };
  return { label: "정상", tone: "success", why: "" };
}

/** 미판정 사유 표시 이름(와이어프레임 CAUSE_LABEL). 그 밖의 코드는 코드 그대로. 코드는 툴팁. */
export const CAUSE_LABELS: Record<string, string> = { input_missing: "입력 없음", input_invalid: "입력 형식 오류", timeout: "기한 초과", failure: "실행 실패" };

export const hoursText = (ms: number) => {
  const hours = ms / 3_600_000;
  return Number.isInteger(hours) ? `${hours}시간` : `${Math.round(ms / 60_000)}분`;
};
