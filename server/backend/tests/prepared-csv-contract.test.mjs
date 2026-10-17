import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { canonicalJson, parsePreparedManifest, parsePreparedUpload, preparedHash, PREPARED_LIMITS } from '../dist/routes/preparedCsvContract.js';

const manifest = () => ({contractVersion:'prepared-csv-v1',sourceId:'10000000-0000-4000-8000-000000000001',sourceSchemaVersion:'process-v1',
  domain:'controlled-example',rowCount:3,inputStage:'model_input',features:[{name:'value',type:'number',unit:'count'}],
  preprocessing:{definitionHash:preparedHash('identity:v1'),scaling:'none',fitHash:null,fitSplit:'unknown',trainRowIdsHash:null},
  split:{version:'provided-v1',timeBasis:'unspecified'},columns:{rowId:'row_id',split:'split',label:'label',status:'status',isSynthetic:'is_synthetic'}});
const csvText = 'row_id,split,label,status,is_synthetic,value\nr1,train,normal,S,false,0.2\nr2,validation,normal,S,false,0.3\nr3,test,anomaly,F,true,9.8\n';
const rejects = value => assert.throws(() => parsePreparedManifest(value), error => error.code?.startsWith('prepared_'));

test('complete model inputs preserve unknown fit and optional metadata without defaults', () => {
  const m=manifest(); delete m.columns.split; m.columns.status=null;
  const result=parsePreparedManifest(m);
  assert.equal(result.inputStage,'model_input'); assert.equal(result.columns.status,null);
  assert.equal(result.preprocessing.fitHash,null); assert.equal(result.labelSource,undefined);
  assert.equal(result.columns.split,undefined);
});
test('idempotency hashes bind exact CSV bytes and canonical manifest independently of object key order', () => {
  const a=parsePreparedUpload({csvText,manifest:manifest()});
  const b=parsePreparedUpload({manifest:Object.fromEntries(Object.entries(manifest()).reverse()),csvText});
  assert.equal(a.requestHash,b.requestHash); assert.equal(a.manifestHash,b.manifestHash);
  assert.notEqual(a.requestHash,parsePreparedUpload({csvText:csvText.replaceAll('\n','\r\n'),manifest:manifest()}).requestHash);
  assert.equal(a.originalHash,preparedHash(Buffer.from(csvText)));
  assert.equal(canonicalJson({z:['한글',1],a:{z:2,b:1}}),'{"a":{"b":1,"z":2},"z":["한글",1]}');
});
test('small manifest numbers match the Python worker canonical JSON and camelCase remains intact', () => {
  for (const [number,expected] of [[1e-7,'1e-07'],[1e-5,'1e-05'],[1e-4,'0.0001'],[1.234567e-20,'1.234567e-20'],
    [0,'0'],[1,'1'],[1.5,'1.5'],[1e6,'1000000'],[5e-324,'5e-324']]) assert.equal(canonicalJson(number),expected);
  const m=manifest(); m.features[0].name='startTimeHourSin'; m.columns.rowId='_RowID';
  const parsed=parsePreparedManifest(m);
  assert.equal(parsed.features[0].name,'startTimeHourSin'); assert.equal(parsed.columns.rowId,'_RowID');
});
test('trust boundary rejects executable fields, preprocessing mismatch, duplicate metadata/features and unbounded inputs', () => {
  for (const change of [m=>m.fitCode='run()',m=>m.inputStage='raw',m=>m.rowCount=0,m=>m.domain='   ',
    m=>m.features[0].type='string',m=>m.features[0].unit='',m=>m.preprocessing.fitHash='bad',m=>m.preprocessing.fitSplit='all',
    m=>m.columns.status='value',m=>m.columns.rowId=null,m=>m.columns.unknown='unknown',m=>m.features.push({...m.features[0]}),
    m=>m.synthetic={generator:'test',version:'v1',seed:1,intensity:Infinity,baselineReference:'r1'}]) {
    const m=manifest(); change(m); rejects(m);
  }
  assert.throws(()=>parsePreparedUpload({csvText:'a'.repeat(PREPARED_LIMITS.csvBytes+1),manifest:manifest()}),{code:'prepared_csv_too_large'});
  for (const value of ['', '\u0000', '\ud800', '\udc00']) assert.throws(()=>parsePreparedUpload({csvText:value,manifest:manifest()}),{code:'prepared_invalid_csv'});
  assert.throws(()=>parsePreparedUpload({csvText,manifest:manifest(),filePath:'/tmp/a'}));
});
test('published minimum example is directly accepted by the API contract', async () => {
  const md=await readFile(new URL('../../contracts/prepared-csv-import.md',import.meta.url),'utf8');
  const m=JSON.parse(md.match(/```json\n([\s\S]*?)\n```/)[1]);
  const csv=md.match(/```csv\n([\s\S]*?)\n```/)[1];
  assert.equal(parsePreparedUpload({csvText:csv,manifest:m}).manifest.rowCount,3);
});
