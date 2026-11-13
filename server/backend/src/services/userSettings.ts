import type { Pool, PoolClient } from 'pg';

/**
 * 사용자별 설정(022, contracts/notifications.md "API" 절 `/api/me/settings` 줄). 계정은 API 파일 저장소에 있으므로 인증 계정 id 문자열(actor_id)로 저장한다.
 * - 알림 설정: 판정 기준 3택·위험도 하한·위험도 미정 포함(Detector 위험도 틀 Q-N1·Q-N1-1). 행이 없으면 기본값(interim·info·true).
 * - 위젯 배치: 운영 현황 개요(operations_overview) 배치 문서 1개. 형식 검사는 여기서, 화면별 위젯 이름·크기 제한은 화면이 다시 검사한다.
 */
type Queryable = Pick<Pool, 'query'>;

export class SettingsRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}

export const DECISION_BASES = ['interim', 'final_any', 'final_complete'] as const;
export const RISK_LEVELS = ['info', 'warning', 'critical'] as const;
export type DecisionBasis = typeof DECISION_BASES[number];
export type RiskLevel = typeof RISK_LEVELS[number];
export type NotificationSettings = { decisionBasis: DecisionBasis; minRiskLevel: RiskLevel; includeUnrated: boolean };
/** 기본값 = 확정된 알림 방식(D8·Q6: 첫 이상에서 알림, 근거가 늘면 갱신). */
export const DEFAULT_NOTIFICATION_SETTINGS: NotificationSettings = { decisionBasis: 'interim', minRiskLevel: 'info', includeUnrated: true };
export const RISK_RANK: Record<RiskLevel, number> = { info: 1, warning: 2, critical: 3 };

/** 위젯 배치 문서: {version: 1~99, items: [{i, x, y, w, h}]}. 화면 위젯 이름은 영문 소문자 시작 1~32자, 정수 좌표·크기. */
export const LAYOUT_LIMITS = { items: 32, coordinate: 999, size: 99, version: 99 } as const;
const WIDGET_ID = /^[a-z][a-z0-9_]{0,31}$/;
export type WidgetLayout = { version: number; items: { i: string; x: number; y: number; w: number; h: number }[] };

const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
const plainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) => {
  const present = Object.keys(value).sort();
  const wanted = [...keys].sort();
  return present.length === wanted.length && present.every((key, index) => key === wanted[index]);
};
const integer = (value: unknown, min: number, max: number) =>
  typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;

export function parseNotificationSettings(value: unknown): NotificationSettings {
  if (!plainObject(value) || !exactKeys(value, ['decisionBasis', 'minRiskLevel', 'includeUnrated'])) throw new SettingsRequestError('invalid_notification_settings');
  const { decisionBasis, minRiskLevel, includeUnrated } = value;
  if (typeof decisionBasis !== 'string' || !(DECISION_BASES as readonly string[]).includes(decisionBasis)
    || typeof minRiskLevel !== 'string' || !(RISK_LEVELS as readonly string[]).includes(minRiskLevel)
    || typeof includeUnrated !== 'boolean') throw new SettingsRequestError('invalid_notification_settings');
  return { decisionBasis: decisionBasis as DecisionBasis, minRiskLevel: minRiskLevel as RiskLevel, includeUnrated };
}

export function parseWidgetLayout(value: unknown): WidgetLayout {
  if (!plainObject(value) || !exactKeys(value, ['version', 'items']) || !integer(value.version, 1, LAYOUT_LIMITS.version)
    || !Array.isArray(value.items) || value.items.length > LAYOUT_LIMITS.items) throw new SettingsRequestError('invalid_widget_layout');
  const seen = new Set<string>();
  const items = value.items.map((entry) => {
    if (!plainObject(entry) || !exactKeys(entry, ['i', 'x', 'y', 'w', 'h']) || typeof entry.i !== 'string' || !WIDGET_ID.test(entry.i) || seen.has(entry.i)
      || !integer(entry.x, 0, LAYOUT_LIMITS.coordinate) || !integer(entry.y, 0, LAYOUT_LIMITS.coordinate)
      || !integer(entry.w, 1, LAYOUT_LIMITS.size) || !integer(entry.h, 1, LAYOUT_LIMITS.size)) throw new SettingsRequestError('invalid_widget_layout');
    seen.add(entry.i);
    return { i: entry.i, x: entry.x as number, y: entry.y as number, w: entry.w as number, h: entry.h as number };
  });
  return { version: value.version as number, items };
}

/** PUT 본문: notifications·widgetLayout 중 하나 이상(그 밖 키·빈 객체 거절). 보낸 항목만 바꾼다. */
export function parseSettingsUpdate(body: unknown): { notifications?: NotificationSettings; widgetLayout?: WidgetLayout } {
  if (!plainObject(body)) throw new SettingsRequestError('invalid_settings_request');
  const keys = Object.keys(body);
  if (!keys.length || keys.some((key) => key !== 'notifications' && key !== 'widgetLayout')) throw new SettingsRequestError('invalid_settings_request');
  return {
    ...(keys.includes('notifications') ? { notifications: parseNotificationSettings(body.notifications) } : {}),
    ...(keys.includes('widgetLayout') ? { widgetLayout: parseWidgetLayout(body.widgetLayout) } : {}),
  };
}

/** 알림 조회가 쓰는 현재 알림 설정(행 없으면 기본값). */
export async function loadNotificationSettings(db: Queryable, actorId: string): Promise<NotificationSettings> {
  const { rows: [row] } = await db.query(`SELECT decision_basis AS "decisionBasis",min_risk_level AS "minRiskLevel",include_unrated AS "includeUnrated"
    FROM ops_user_notification_settings WHERE actor_id=$1`, [actorId]);
  return row ? row as NotificationSettings : { ...DEFAULT_NOTIFICATION_SETTINGS };
}

/** GET /api/me/settings 응답. */
export async function readSettings(db: Queryable, actorId: string) {
  const { rows: [row] } = await db.query(`SELECT
      (SELECT jsonb_build_object('decisionBasis',decision_basis,'minRiskLevel',min_risk_level,'includeUnrated',include_unrated,
        'updatedAt',${ISO('updated_at')}) FROM ops_user_notification_settings WHERE actor_id=$1) AS notifications,
      (SELECT jsonb_build_object('layout',layout,'updatedAt',${ISO('updated_at')}) FROM ops_user_widget_layouts
        WHERE actor_id=$1 AND screen='operations_overview') AS layout`, [actorId]);
  const stored = row.notifications as (NotificationSettings & { updatedAt: string }) | null;
  return {
    notifications: stored
      ? { decisionBasis: stored.decisionBasis, minRiskLevel: stored.minRiskLevel, includeUnrated: stored.includeUnrated, isDefault: false, updatedAt: stored.updatedAt }
      : { ...DEFAULT_NOTIFICATION_SETTINGS, isDefault: true, updatedAt: null },
    widgetLayout: row.layout
      ? { layout: row.layout.layout as WidgetLayout, updatedAt: row.layout.updatedAt as string }
      : { layout: null, updatedAt: null },
  };
}

/** PUT /api/me/settings: 보낸 항목을 한 트랜잭션에서 바꾸고(마지막 저장이 이김) 저장된 전체 설정을 돌려준다. */
export async function updateSettings(pool: Pool, actorId: string, update: ReturnType<typeof parseSettingsUpdate>) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    if (update.notifications) {
      const { decisionBasis, minRiskLevel, includeUnrated } = update.notifications;
      await client.query(`INSERT INTO ops_user_notification_settings(actor_id,decision_basis,min_risk_level,include_unrated) VALUES($1,$2,$3,$4)
        ON CONFLICT (actor_id) DO UPDATE SET decision_basis=EXCLUDED.decision_basis,min_risk_level=EXCLUDED.min_risk_level,
          include_unrated=EXCLUDED.include_unrated,updated_at=clock_timestamp()`, [actorId, decisionBasis, minRiskLevel, includeUnrated]);
    }
    if (update.widgetLayout) {
      await client.query(`INSERT INTO ops_user_widget_layouts(actor_id,screen,layout) VALUES($1,'operations_overview',$2::jsonb)
        ON CONFLICT (actor_id,screen) DO UPDATE SET layout=EXCLUDED.layout,updated_at=clock_timestamp()`, [actorId, JSON.stringify(update.widgetLayout)]);
    }
    const result = await readSettings(client, actorId);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}
