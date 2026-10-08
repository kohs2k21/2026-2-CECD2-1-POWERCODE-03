import assert from "node:assert/strict";
import { developmentDetectionData } from "../src/features/detection/data/fixtures.ts";
import {
  composePreview,
  compositionErrors,
  currentConfigurationFingerprint,
  evaluationMatchesConfiguration,
} from "../src/features/detection/data/configuration.ts";
const data = structuredClone(developmentDetectionData);
const input = {
  name: "조합",
  modelArtifactId: data.modelArtifacts[0].id,
  ruleVersionIds: [data.ruleVersions[0].id],
  explanationVersion: "explanation-v1",
};
const preview = composePreview(input, data);
assert.ok(preview);
assert.equal(preview.id, "composition-preview");
assert.equal(preview.state, "draft");
assert.equal(preview.evaluationId, null);
assert.deepEqual(preview.featureIds, data.modelArtifacts[0].featureIds);
assert.equal(preview.fitVersion, data.modelArtifacts[0].fitVersion);
assert.equal(
  preview.preprocessingVersion,
  data.modelArtifacts[0].preprocessingVersion,
);
assert.equal(
  currentConfigurationFingerprint(preview, data),
  preview.configurationFingerprint,
);
const result = {
  ...data.evaluations[0],
  candidateId: preview.id,
  configurationFingerprint: preview.configurationFingerprint,
};
assert.equal(evaluationMatchesConfiguration(result, preview, data), true);
const changed = structuredClone(data);
changed.ruleVersions[0].rules[0].threshold += 1;
assert.equal(
  evaluationMatchesConfiguration(result, preview, changed),
  false,
  "same logical rule/revision IDs do not excuse changed definitions",
);
changed.ruleVersions[0].rules[0].threshold -= 1;
changed.modelArtifacts[0].fitVersion = "fit-changed";
assert.equal(
  currentConfigurationFingerprint(preview, changed),
  null,
  "an artifact cannot mix independently edited fit metadata",
);
changed.modelArtifacts[0].state = "queued";
assert.equal(
  composePreview(input, changed),
  null,
  "an algorithm or queued job is not a completed artifact",
);
assert.ok(
  compositionErrors(
    {
      ...input,
      ruleVersionIds: data.ruleVersions.slice(0, 2).map((item) => item.id),
    },
    data,
  ).length > 0,
  "multiple versions containing the same logical rule must be rejected",
);
assert.equal(composePreview({ ...input, name: " " }, data), null);
assert.equal(
  data.activeVersionId,
  "version-current",
  "local composition never applies a version",
);
console.log(
  "configuration contract QA passed: completed artifact ownership, exact rule content binding, fit isolation, duplicate rule rejection, local-only preview",
);
