import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {DecisionDiagnostics} from '../src/features/pipeline/DecisionDiagnostics.tsx';

const base={id:'decision',sourceId:'source',processObservationId:'observation',configVersionId:'config',
  status:'unavailable',ruleStatus:'not_ready',mlStatus:'not_ready',decisionCode:null,anomalyScore:null,decidedAt:'2026-10-15T00:00:00Z'};
const render=evidence=>renderToStaticMarkup(React.createElement(DecisionDiagnostics,{decision:{...base,evidence}}));
const deadline=render({rules:[{reason:'deadline_exceeded'}],models:[{reason:'worker_busy'}],detectionPolicy:{deadlineMs:3000}});
assert(deadline.includes('처리 기한을 초과'));assert(deadline.includes('동시 실행 슬롯'));assert(deadline.includes('3000'));
const missing=render({rules:[{result:'unavailable'}],models:[{reason:'model_features_unavailable'}]});
assert(missing.includes('입력값 또는 파생변수'));assert(missing.includes('모델 입력 피처'));
const unknown=render({rules:[{reason:'RAW_SECRET'},null,{}],models:'RAW_BODY'});
assert(!unknown.includes('RAW_SECRET'));assert(!unknown.includes('RAW_BODY'));assert(unknown.includes('저장된 세부 사유가 없습니다.'));
const success=renderToStaticMarkup(React.createElement(DecisionDiagnostics,{decision:{...base,status:'complete',ruleStatus:'succeeded',mlStatus:'succeeded'}}));
assert.equal(success,'');
assert(render(undefined).includes('저장된 세부 사유가 없습니다.'));
console.log('PASS: stored deadline/busy/missing-feature reasons, legacy fallback, no invented cause or raw evidence');
