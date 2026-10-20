// 학습·탐지 운영 화면 조회를 조회별로 반영한다. 한 조회 실패가 다른 조회 반영을 막거나 빈 결과로 보이지 않게 한다.
export const refreshKeys = ["sources", "jobs", "active", "metrics", "drift", "applied", "decisions", "windows", "monitors"] as const;
export type RefreshKey = (typeof refreshKeys)[number];
export type RefreshStatus = {
  loaded: Partial<Record<RefreshKey, true>>;
  failures: Partial<Record<RefreshKey, string>>;
};
export type RefreshOutcome<V extends Partial<Record<RefreshKey, unknown>>> = {
  values: Partial<V>;
  failures: Partial<Record<keyof V & RefreshKey, string>>;
};

export const initialRefreshStatus: RefreshStatus = { loaded: {}, failures: {} };

// 조회마다 끝나는 즉시 반영(applier)하고 record로 상태를 알린다. 한 조회가 멈추거나 실패해도 다른 조회 반영을 막지 않는다.
// 조회 실패·applier 예외(응답 형태 이상)는 그 식별자의 실패로 기록하고, 반영하지 않았으므로 직전 성공값이 유지된다.
// 반환 Promise는 모든 조회가 끝나면 합친 결과로 resolve되며 reject하지 않는다.
export async function trackRefresh<V extends Partial<Record<RefreshKey, unknown>>>(
  loaders: { [K in keyof V]: () => Promise<V[K]> }, appliers: { [K in keyof V]: (value: V[K]) => void },
  describe: (reason: unknown) => string, record: (outcome: RefreshOutcome<V>) => void,
): Promise<RefreshOutcome<V>> {
  const keys = Object.keys(loaders) as (keyof V & RefreshKey)[];
  const total: RefreshOutcome<V> = { values: {}, failures: {} };
  await Promise.all(keys.map(async (key) => {
    const outcome: RefreshOutcome<V> = { values: {}, failures: {} };
    try {
      const value = await loaders[key]();
      appliers[key](value);
      outcome.values[key] = total.values[key] = value;
    } catch (reason) {
      outcome.failures[key] = total.failures[key] = describe(reason);
    }
    record(outcome);
  }));
  return total;
}

// 배열 응답을 복사한다. 배열이 아니면(항목 누락 등) 기본 TypeError로 실패해 해당 조회만 실패로 기록된다.
export const rows = <T,>(value: readonly T[]): T[] => [...value];

// 이번에 조회한 식별자만 갱신한다. 성공하면 실패 표시를 지우고, 조회하지 않은 식별자는 그대로 둔다.
export function mergeRefreshStatus<V extends Partial<Record<RefreshKey, unknown>>>(
  previous: RefreshStatus, outcome: RefreshOutcome<V>,
): RefreshStatus {
  const loaded = { ...previous.loaded };
  const failures = { ...previous.failures };
  for (const key of Object.keys(outcome.values) as RefreshKey[]) { loaded[key] = true; delete failures[key]; }
  for (const [key, text] of Object.entries(outcome.failures) as [RefreshKey, string][]) failures[key] = text;
  return { loaded, failures };
}

// 한 번도 성공 조회되지 않았거나 현재 실패 중이면 '없음' 표시를 하지 않는다.
export const emptyResultVisible = (status: RefreshStatus, key: RefreshKey) =>
  Boolean(status.loaded[key]) && !status.failures[key];

// 기존 오류 영역에 조회 실패(식별자: 오류)와 사용자 동작 오류를 함께 표시한다.
export function refreshAlertText(status: RefreshStatus, actionError: string | null): string | null {
  const lines = refreshKeys.filter((key) => status.failures[key]).map((key) => `${key}: ${status.failures[key]}`);
  if (actionError) lines.push(actionError);
  return lines.length ? lines.join(" · ") : null;
}
