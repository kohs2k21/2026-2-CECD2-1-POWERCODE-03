// v04 판정 evidence 표본. v04-evidence-samples.json은 Detector의 build_evidence·build_interim_payload 출력이다(시험마다 달라지는 값만 자리표시자).
import { readFileSync } from 'node:fs';

const samples = JSON.parse(readFileSync(new URL('./v04-evidence-samples.json', import.meta.url), 'utf8'));
const fill = (value, names) => JSON.parse(Object.entries(names)
  .reduce((text, [name, replacement]) => text.replaceAll(`__${name}__`, replacement), JSON.stringify(value)));

/** 최종 판정: 판정 행 열 값(columns)과 evidence. input은 작업 input_spec의 inputOrigin(입력 출처가 있을 때만). */
export const v04Final = (name, { sourceId, observationId, inputAsOf, manifestHash, modelVersionId, input }) => {
  const sample = fill(samples.final[name], { SOURCE_ID: sourceId, OBSERVATION_ID: observationId, INPUT_AS_OF: inputAsOf,
    MANIFEST_HASH: manifestHash, MODEL_VERSION_ID: modelVersionId });
  return input ? { ...sample, evidence: { ...sample.evidence, input } } : sample;
};

/** 중간 판정 기록 payload(미도착 방법은 cause=pending). */
export const v04Interim = (name, modelVersionId) => fill(samples.interim[name], { MODEL_VERSION_ID: modelVersionId });
