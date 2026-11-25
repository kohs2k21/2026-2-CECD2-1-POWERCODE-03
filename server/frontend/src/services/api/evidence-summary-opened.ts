/**
 * 이 브라우저 탭 세션에서 [AI 근거 생성 요청]을 누른 판정(analysis-ui.md 펼치기). sessionStorage(탭을 닫으면 사라짐 — 새로고침·다른 화면
 * 다녀오기에도 다시 누르지 않게)에 최근 MAX개만 둔다. 저장소를 못 쓰면(사생활 보호 창·차단·깨진 값) 메모리만 쓴다.
 * 계정이 바뀌면 지운다(services/auth/session.ts, 작업 멱등키와 같은 자리).
 */
const storageKey = "esb.evidenceSummary.opened";
export const SUMMARY_OPENED_MAX = 200;
const memory = new Set<string>();

const readStored = (): string[] => {
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : [];
  } catch {
    return [];
  }
};

export const wasSummaryOpened = (id: string) => memory.has(id) || readStored().includes(id);

export const rememberSummaryOpened = (id: string) => {
  memory.add(id);
  try {
    const ids = [...readStored().filter((value) => value !== id), id].slice(-SUMMARY_OPENED_MAX);
    window.sessionStorage.setItem(storageKey, JSON.stringify(ids));
  } catch {
    // 저장 실패는 메모리 기록으로 대신한다.
  }
};

export const clearSummaryOpened = () => {
  memory.clear();
  try {
    window.sessionStorage.removeItem(storageKey);
  } catch {
    // 저장소를 못 쓰면 메모리만 비운다.
  }
};
