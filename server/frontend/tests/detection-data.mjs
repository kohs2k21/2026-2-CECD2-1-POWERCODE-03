import assert from "node:assert/strict";
import { featureCatalog, rawFeatures, derivedFeatures } from "../src/features/detection/data/catalog.ts";
import { developmentDetectionData } from "../src/features/detection/data/fixtures.ts";
import { createDevelopmentGateway, getDefaultGateway } from "../src/features/detection/data/gateway.ts";
assert.equal(rawFeatures.length,30);
assert.equal(derivedFeatures.filter(feature=>feature.id.startsWith("F")).length,24);
assert.equal(derivedFeatures.filter(feature=>feature.id.startsWith("A")).length,15);
assert.equal(new Set(featureCatalog.map(feature=>feature.id)).size,69);
assert.equal(featureCatalog.filter(feature=>feature.defaultSelected).length,68);
assert.ok(["A01","A02","A03","A04","A06"].every(id=>featureCatalog.find(feature=>feature.id===id).defaultSelected));
assert.equal(featureCatalog.find(feature=>feature.id==="A14").readiness,"deferred");
assert.equal(featureCatalog.find(feature=>feature.id==="A14").defaultSelected,false);
assert.equal(featureCatalog.find(feature=>feature.id==="process.PROCESS_ID").type,"identifier");
assert.equal(featureCatalog.find(feature=>feature.id==="process.RESPONSE_MESSAGE").readiness,"unsupported");
assert.equal(featureCatalog.find(feature=>feature.id==="F22").readiness,"fit-required");
assert.ok(featureCatalog.every(feature=>feature.missingRate===null));
const gateway=createDevelopmentGateway(developmentDetectionData);
const first=await gateway.read();first.versions[0].name="changed";
assert.notEqual((await gateway.read()).versions[0].name,"changed","query consumers must not mutate source fixtures");
const activeBefore=(await gateway.read()).activeVersionId;
for(const operation of Object.keys(developmentDetectionData.capabilities)) {
  await assert.rejects(gateway.request(operation,{}),error=>error.code==="service_unavailable");
}
assert.equal((await gateway.read()).activeVersionId,activeBefore,"unconnected writes must not mutate active version");
const controller=new AbortController();const pending=gateway.read(controller.signal);controller.abort();
await assert.rejects(pending,error=>error.name==="AbortError");
const production=await getDefaultGateway();
await assert.rejects(production.read(),error=>error.status===503,"outside DEV no fixture fallback");
console.log("detection data QA passed: raw30+F24+A15, defaults/deferred/fit states, immutable fixtures, unavailable writes, cancellation, production read block");
