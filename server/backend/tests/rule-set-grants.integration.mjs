// 룰 세트 API의 실제 읽기·쓰기 경로를 런타임 역할 powercode_api로 실행해 권한을 확인한다(015 포함).
// 로컬·CI의 qa_rule_set* DB만 허용. 역할 스크립트는 다른 시험과 같이 superuser로 적용한다.
// 확인: API 역할로 조회·미리보기·저장·이력·수식 추천 성공, 이력 UPDATE·DELETE 거부, Detector·Trainer는 읽기만, Trainer·LLM·Collector는 이력 UPDATE 거부(LLM·Collector는 읽기 권한도 없음). 실행 전 `npm run build`.
// API 역할에는 UPDATE가 없어 SELECT … FOR UPDATE도 거부된다. 저장 직렬화는 advisory lock만 쓴다(일반 SELECT → 일반 INSERT).
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { addProcess, seedRuleSetSource } from './rule-set-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_rule_set[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_rule_set database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const { readRuleSet, previewRuleSet, saveRuleSet, readHistory, readHistoryItem } = await import('../dist/ruleset/ruleSetStore.js');
const { computeThresholdStats, parseThresholdStatsRequest } = await import('../dist/ruleset/thresholdStats.js');

const admin = new pg.Pool({ connectionString: url });
const api = new pg.Pool({ connectionString: url, options: '-c role=powercode_api' });
const denied = async (role, sql) => {
  const client = await admin.connect();
  try {
    await client.query('BEGIN');
    await client.query(`SET LOCAL ROLE ${role}`);
    await client.query(sql);
    return 'allowed';
  } catch (error) {
    return error.code;
  } finally {
    await client.query('ROLLBACK').catch(() => undefined);
    client.release();
  }
};
const actor = async (actorId) => ({ actorId, displayName: actorId });

try {
  await admin.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api');

  const state = await readRuleSet(api, actor);
  const body = {
    expectedRevision: state.revision, confirmHardRuleDisable: false, restoreFromRevision: null, note: null,
    hardRules: state.hardRules.map(({ id, enabled, severity }) => ({ id, enabled, severity })),
    softRules: [{ id: null, clientRef: 'grant', name: `권한 시험 ${state.revision}`, field: 'retry_count', operator: 'gte', threshold: state.revision + 3, severity: 'info', enabled: true }],
  };
  assert.equal((await previewRuleSet(api, body, actor)).valid, true);
  const saved = await saveRuleSet(api, body, 'grant-admin', actor);
  assert.equal(saved.revision, state.revision + 1);
  assert.equal((await readHistory(api, { limit: 5, before: null }, actor)).items[0].revision, saved.revision);
  assert.equal((await readHistoryItem(api, saved.revision, actor)).snapshot.softRules.length, 1);

  const sourceId = await seedRuleSetSource(admin, 'grants');
  for (let index = 0; index < 25; index += 1) await addProcess(admin, sourceId, { occurredAt: new Date().toISOString(), counts: { total: index + 1 } });
  const request = parseThresholdStatsRequest({ field: 'total_count', sourceId, period: { mode: 'recent_1m' } });
  assert.equal((await computeThresholdStats(api, request)).status, 'proposed');

  const results = {
    apiUpdate: await denied('powercode_api', `UPDATE ops_rule_set_revisions SET note='x'`),
    apiDelete: await denied('powercode_api', 'DELETE FROM ops_rule_set_revisions'),
    apiForUpdate: await denied('powercode_api', 'SELECT 1 FROM ops_rule_set_revisions ORDER BY revision DESC LIMIT 1 FOR UPDATE'),
    detectorRead: await denied('powercode_detector', 'SELECT 1 FROM ops_rule_set_revisions LIMIT 1'),
    detectorInsert: await denied('powercode_detector', `INSERT INTO ops_rule_set_revisions(revision,content_hash,snapshot,changes,summary,saved_by_actor_id,saved_at)
      VALUES(99999,'','{}','[]','{}','x',now())`),
  };
  for (const role of ['powercode_trainer', 'powercode_llm', 'powercode_collector']) {
    results[role] = await denied(role, `UPDATE ops_rule_set_revisions SET note='x'`);
  }
  assert.deepEqual(results, {
    apiUpdate: '42501', apiDelete: '42501', apiForUpdate: '42501', detectorRead: 'allowed', detectorInsert: '42501',
    powercode_trainer: '42501', powercode_llm: '42501', powercode_collector: '42501',
  });
  console.log('rule-set grants passed: API read/preview/save/history/stats, append-only for API, read-only Detector');
} finally {
  await api.end();
  await admin.end();
}
