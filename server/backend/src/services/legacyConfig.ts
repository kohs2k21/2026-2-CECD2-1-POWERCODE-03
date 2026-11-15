import type { PoolClient } from 'pg';

/**
 * v03 단일 구성 경로(`ops_system_state` 적용·복원, 입력 구성 고정 `ops_input_controls.config_version_id`)가 읽을 수 있는 구성 schemaVersion.
 * v3 모델 버전은 정책·룰이 없어 v03 판독(`load_context`·`parse_policy`)이 legacy 기본값(parallel, 룰 없음)으로 조용히 읽으므로
 * 이 경로에 넣지 않고 활성 집합(v04 로더)으로만 운영한다(model-version.md 활성 집합 절).
 */
export const LEGACY_CONFIG_SCHEMA_VERSIONS: readonly unknown[] = [1, 2];
export const LEGACY_CONFIG_REFUSED = 'config_schema_not_supported_yet';

/** 구성이 있고 schemaVersion이 1·2가 아니면 true(422로 거절할 대상). 없는 구성은 호출부의 기존 검사가 다룬다. */
export async function legacyConfigRefused(db: Pick<PoolClient, 'query'>, configVersionId: string): Promise<boolean> {
  const { rows: [row] } = await db.query<{ schemaVersion: unknown }>(
    `SELECT configuration_spec->'schemaVersion' AS "schemaVersion" FROM ml_config_versions WHERE id=$1`, [configVersionId]);
  return row !== undefined && !LEGACY_CONFIG_SCHEMA_VERSIONS.includes(row.schemaVersion);
}
