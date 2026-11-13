// 배포 안전 장치(rule-set.md v3.7 "API v3 저장은 Detector v3 배포 확인 뒤에만"): 이 API는 hash v3로 저장하므로, 같은 저장소의 Detector가
// v3 행의 hash를 계산하지 못하면(= 시도마다 rule_set_hash_mismatch/버전 거절로 판정 실패) 이 시험이 실패한다.
// 방법: Detector의 실제 함수 `detector.operational_state.rule_set_content_hash(snapshot, 3)`를 Python으로 불러 API가 저장할 스냅샷의
// hash와 계약 기준값 (c)~(h)를 비교한다(Detector 모듈은 표준 라이브러리만 쓴다). 건너뛰지 않는다: Python이 없거나 함수가 v3를 거절하면 실패.
// DETECTOR_WORKER_ROOT로 다른 worker 폴더를 가리킬 수 있다(Detector 브랜치 확인용, 기본은 이 저장소의 server/worker).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { CONTENT_HASH_VERSION, contentHash, planRuleSetChange, parseRuleSetRequest, projectHardRules } from '../dist/ruleset/ruleSetContract.js';
import { HARD_RULES } from '../dist/ruleset/hardRuleCatalog.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const workerRoot = process.env.DETECTOR_WORKER_ROOT ?? path.resolve(here, '../../worker');
// rule-set.md v3.8 기준값(c)·(h)와 같은 입력·값(rule-set-v3.test.mjs와 같은 계약 문장에서 옮김).
const C_HASH = '01875d1309dbe56a94158de82f4c5b94664dc25651c77ddf70a6cec96b2960b2';
const H_HASH = '73045b14ab8d6c81d814e164283f5432a54f7a9d997ec0c636b6d6f6de4ab895';

const SCRIPT = `
import json, sys
sys.path.insert(0, sys.argv[1])
from detector.operational_state import rule_set_content_hash
out = []
for snapshot in json.loads(sys.stdin.read()):
    try:
        out.append(rule_set_content_hash(snapshot, 3))
    except Exception as exc:
        out.append("error:" + type(exc).__name__ + ":" + str(exc))
print(json.dumps(out))
`;

function detectorHashes(snapshots) {
  const failures = [];
  for (const python of process.platform === 'win32' ? ['python', 'python3'] : ['python3', 'python']) {
    const run = spawnSync(python, ['-I', '-c', SCRIPT, workerRoot], { input: JSON.stringify(snapshots), encoding: 'utf8' });
    if (run.error) { failures.push(`${python}: ${run.error.code ?? run.error.message}`); continue; }
    assert.equal(run.status, 0, `Detector hash import failed (${python}): ${run.stderr}`);
    return JSON.parse(run.stdout);
  }
  // 건너뛰지 않는다: 이 시험이 돌지 않으면 v3 저장을 막을 장치가 없다.
  throw new Error(`python is required for the Detector hash v3 guard (${failures.join('; ')})`);
}

test('the Detector in this repository computes hash v3 the same way as this API (deploy guard)', () => {
  assert.equal(CONTENT_HASH_VERSION, 3);
  // (c): 내장 6개(4개 켬·거래 실패 꺼짐·미등록 인터페이스 켬) — 저장 스냅샷 형식 그대로(표시 필드·editable 포함).
  const c = { hardRules: HARD_RULES.map((rule) => ({ ...rule, enabled: rule.id !== 'hard:transaction-failed' })), softRules: [], relationRules: [], protocolRules: [] };
  // (h): 이스케이프 순서 함정.
  const h = { hardRules: [], softRules: [], relationRules: [], protocolRules: [{ id: 'hard:proto-0000000b', interfaceId: 'IF_D', severity: 'warning', enabled: true,
    paths: [{ channels: ['A:'], source: null }, { channels: ['A%3A'], source: null }], source: null, createdAtRevision: 1, updatedAtRevision: 1 }] };
  // API 저장 경로가 실제로 만드는 스냅샷(관계·프로토콜·scope·새 내장 위험도 포함).
  const hard = HARD_RULES.map((rule) => ({ id: rule.id, enabled: true, ...(rule.editable.includes('severity') ? { severity: rule.id === 'hard:transaction-failed'
    ? { byCode: { 9: 'critical', 10: 'warning', '0100': 'info' }, default: 'warning' } : rule.severity } : {}) }));
  const saved = planRuleSetChange({
    request: parseRuleSetRequest({ expectedRevision: 1, hardRules: hard, relationRules: [
      { id: null, clientRef: 'r', kind: 'precedes', scope: { interfaceId: 'IF_A', adapterType: 'HTTP' }, from: { processId: 'P1' }, to: { processId: 'P2' }, match: null, severity: 'critical', enabled: true },
      { id: null, clientRef: 's', kind: 'required_step', scope: { interfaceId: 'IF_A' }, from: { processId: 'P1' }, to: null, match: null, severity: 'warning', enabled: true }],
    protocolRules: [{ id: null, clientRef: 'p', interfaceId: 'IF_A', paths: [{ channels: ['CH_B'] }, { channels: ['CH_A', '채널C'] }], severity: 'critical', enabled: true }],
    softRules: [{ id: null, clientRef: 'f', name: '처리 시간', field: 'elapsed_ms', operator: 'gt', threshold: 704.5, severity: 'warning', enabled: true, scope: { processId: 'P2', interfaceId: 'IF_A' } }] }),
    current: { hardRules: projectHardRules([]), softRules: [], relationRules: [], protocolRules: [] }, restore: null, activeModelCount: 0, nextRevision: 2,
    assignId: (prefix) => `${prefix}0000000c`,
  });
  // JSON 왕복 = DB jsonb에 들어갔다 나온 모양.
  const stored = JSON.parse(JSON.stringify(saved.snapshot));
  const [hc, hh, hs] = detectorHashes([c, h, stored]);
  assert.equal(hc, C_HASH, `Detector cannot compute hash v3 for reference (c): ${hc}`);
  assert.equal(hh, H_HASH, `Detector hash v3 differs for reference (h): ${hh}`);
  assert.equal(hs, saved.contentHash, 'Detector hash of a snapshot saved by this API equals the hash the API stores');
  assert.equal(contentHash(stored, 3), saved.contentHash);
});
