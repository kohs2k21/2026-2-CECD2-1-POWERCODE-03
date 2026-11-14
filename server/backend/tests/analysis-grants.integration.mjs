// 이상 분석 API의 실제 읽기·쓰기 경로를 런타임 역할 powercode_api로 실행해 database-roles.sql 권한을 확인한다(014 포함).
// 로컬·CI의 qa_analysis* DB만 허용. 역할 스크립트는 다른 CI 시험(integration_input_roles.py 등)과 같이 superuser로 적용한다.
// 확인: API 역할로 목록·상세·판정 기록·Process Flow·처리 상태 변경·조사·조치 기록·읽음 성공, 이력·기록 UPDATE·DELETE 거부,
// 읽음 행은 read_at만 갱신, Trainer·LLM·Detector·Collector는 014 표 접근 거부. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { seedAnalysisFixture } from './analysis-fixture.mjs';

const url = process.env.PIPELINE_TEST_DATABASE_URL;
if (!url) throw new Error('PIPELINE_TEST_DATABASE_URL is required');
const parsed = new URL(url);
if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !/^\/qa_analysis[a-z_0-9]*$/.test(parsed.pathname)) {
  throw new Error('Only a local qa_analysis database is permitted');
}
const here = path.dirname(fileURLToPath(import.meta.url));
const { readDecisionList, readDecisionDetail, readDecisionInterims, readDecisionFlow, readDecisionGraph } = await import('../dist/services/analysisReadModel.js');
const { changeWorkflow, addFeedback, listFeedback, markRead } = await import('../dist/services/analysisWorkflow.js');
const { readLatency } = await import('../dist/services/operationsReadModel.js');

const admin = new pg.Pool({ connectionString: url });
const api = new pg.Pool({ connectionString: url, options: '-c role=powercode_api' });
const denied = async (role, sql, params = []) => {
  const client = await admin.connect();
  try {
    await client.query('BEGIN');
    await client.query(`SET LOCAL ROLE ${role}`);
    await client.query(sql, params);
    return 'allowed';
  } catch (error) {
    return error.code;
  } finally {
    await client.query('ROLLBACK').catch(() => undefined);
    client.release();
  }
};

try {
  await admin.query(await readFile(path.join(here, '../../infra/scripts/database-roles.sql'), 'utf8'));
  const fixture = await seedAnalysisFixture(admin);
  const D = fixture.decisions, J = fixture.jobs;
  assert.equal((await api.query('SELECT current_user AS u')).rows[0].u, 'powercode_api');

  // 읽기 경로(ITEMS_CTE·상세·판정 기록·흐름)는 API 역할 SELECT 권한만으로 동작한다.
  const list = await readDecisionList(api, { category: 'all', q: '', signal: null, completeness: null, sort: 'newest', page: 1, pageSize: 50 }, 'grant-user');
  assert.ok(list.items.some((item) => item.id === D.v4Info.id) && list.counts.risk_info >= 1);
  assert.equal((await readDecisionDetail(api, D.anomaly.id, 'grant-user')).workflow.state, 'detected');
  assert.equal((await readDecisionInterims(api, J.interim.id)).items.length, 2);
  const capped = await readDecisionFlow(api, D.anomaly.id, { processes: 1, messages: 2 });
  assert.deepEqual([capped.processes.length, capped.messages.length, capped.truncated, capped.counts], [1, 2, true, { processes: 2, messages: 3, errors: 1 }],
    'capped node lists keep the true totals');
  assert.ok(!capped.processes.some((p) => Object.keys(p).some((key) => key.startsWith('_'))), 'internal count columns are not exposed');
  const cappedGraph = await readDecisionGraph(api, D.anomaly.id, { processes: 1, messages: 2 });
  assert.deepEqual([cappedGraph.truncated, cappedGraph.nodes.filter((n) => n.kind === 'process').length, cappedGraph.nodes.filter((n) => n.kind === 'message').length],
    [true, 1, 2], 'graph uses the same caps and API role grants (decision lookup included)');
  assert.ok(!JSON.stringify(cappedGraph).includes('"_'), 'internal columns are not exposed in the graph');
  // 운영 현황 대기·실패도 detect 작업 원천을 입력 manifest에서 읽는다.
  assert.equal((await readLatency(api, fixture.sourceId)).stages.find((stage) => stage.id === 'detector').recentFailures, 2);

  // 쓰기 경로(API 역할): 처리 상태 이력 INSERT, 조사·조치 기록 INSERT·조회, 읽음 upsert.
  assert.equal((await changeWorkflow(api, J.v4Final.id, 'grant-user', { state: 'open', expectedState: 'detected' })).workflow.state, 'open');
  assert.equal((await changeWorkflow(api, D.anomaly.id, 'grant-user', { state: 'resolved', expectedState: 'detected' })).workflow.state, 'resolved');
  // 029 확인 결과 열: 표 단위 014 GRANT로 API 역할이 쓰고 읽는다.
  const record = (await addFeedback(api, D.anomaly.id, 'grant-admin', { kind: 'action', reason: '권한 시험', outcome: 'normal' })).item;
  assert.deepEqual([record.kind, record.reason, record.outcome], ['action', '권한 시험', 'normal']);
  assert.deepEqual((await listFeedback(api, D.anomaly.id)).items.map((item) => item.outcome), ['normal']);
  const seen = list.items.find((item) => item.id === J.interim.id).occurredAt;
  assert.equal((await markRead(api, J.interim.id, 'grant-user', seen)).readAt, seen);
  assert.equal((await markRead(api, J.interim.id, 'grant-user', seen)).readAt, seen, 'upsert on conflict (read_at only)');
  assert.ok((await markRead(api, D.normal.id, 'grant-user', seen)).readAt);

  // append-only·라벨 분리: API 역할도 이력·기록을 고치거나 지울 수 없고, 읽음 행은 read_at만 바꾼다.
  const results = {
    apiUpdateWorkflow: await denied('powercode_api', "UPDATE run_decision_workflow_events SET to_state='open'"),
    apiDeleteWorkflow: await denied('powercode_api', 'DELETE FROM run_decision_workflow_events'),
    apiUpdateFeedback: await denied('powercode_api', "UPDATE run_decision_feedback SET reason='x'"),
    apiDeleteFeedback: await denied('powercode_api', 'DELETE FROM run_decision_feedback'),
    apiUpdateReadActor: await denied('powercode_api', "UPDATE run_decision_reads SET actor_id='x'"),
    apiDeleteRead: await denied('powercode_api', 'DELETE FROM run_decision_reads'),
    apiUpdateReadAt: await denied('powercode_api', 'UPDATE run_decision_reads SET read_at=read_at'),
  };
  for (const role of ['powercode_trainer', 'powercode_llm', 'powercode_detector', 'powercode_collector']) {
    for (const table of ['run_decision_workflow_events', 'run_decision_feedback', 'run_decision_reads']) {
      results[`${role}:${table}`] = await denied(role, `SELECT 1 FROM ${table} LIMIT 1`);
    }
  }
  assert.deepEqual(results, {
    apiUpdateWorkflow: '42501', apiDeleteWorkflow: '42501', apiUpdateFeedback: '42501', apiDeleteFeedback: '42501',
    apiUpdateReadActor: '42501', apiDeleteRead: '42501', apiUpdateReadAt: 'allowed',
    ...Object.fromEntries(['powercode_trainer', 'powercode_llm', 'powercode_detector', 'powercode_collector'].flatMap((role) =>
      ['run_decision_workflow_events', 'run_decision_feedback', 'run_decision_reads'].map((table) => [`${role}:${table}`, '42501']))),
  });
  console.log('analysis grants integration passed: powercode_api read/write paths (list, detail, interims, capped flow with true totals, '
    + 'workflow, feedback, read upsert), API UPDATE/DELETE denied on history and records, read_at-only update, other roles denied on 014 tables');
} finally {
  await api.end();
  await admin.end();
}
