/** Bounded CSV authoring through the provided Artifact Tool, preserving typed values. */
import fs from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import readline from 'node:readline';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { Workbook } from '@oai/artifact-tool';

const args = process.argv.slice(2);
const [inputDir, outputDir] = args;
if (!inputDir || !outputDir) throw new Error('Usage: author_csv.mjs intermediateDir emptyOutputDir');
await fs.mkdir(outputDir,{recursive:true});
const root = JSON.parse(await fs.readFile(path.join(inputDir,'metadata.json'),'utf8'));
const repairIndex=args.indexOf('--repair');
if(repairIndex>=0) {
  const plan=JSON.parse(await fs.readFile(args[repairIndex+1],'utf8'));
  for(const entry of plan.datasets) {
    if(!root.datasets.includes(entry.dataset)) throw new Error('Unknown repair dataset');
    const directory=path.resolve(outputDir,entry.dataset);
    const old=JSON.parse(await fs.readFile(path.join(directory,'manifest.json'),'utf8'));
    const metadata=entry.metadata;
    metadata.files={};
    const caseIds=new Set(entry.case_ids),txIds=new Set(entry.transaction_ids);
    for(const table of entry.tables) {
      if(path.basename(table.filename)!==table.filename) throw new Error('Unsafe repair filename');
      const filename=path.join(directory,table.filename),temporary=filename+'.repair';
      const handle=await fs.open(temporary,'w');
      const oldHash=crypto.createHash('sha256'),newHash=crypto.createHash('sha256');
      const droppedIds=table.columns[0]==='transactionId'?txIds:caseIds;
      let rows=0,read=0,header=true,buffer=[];
      async function flush(){if(buffer.length){const text=buffer.join('');await handle.write(text,null,'utf8');newHash.update(text,'utf8');buffer=[];}}
      const lines=readline.createInterface({input:createReadStream(filename,{encoding:'utf8'}),crlfDelay:Infinity});
      for await(const line of lines) {
        oldHash.update(line+'\r\n','utf8');
        if(header){
          if(line.replace(/^\uFEFF/,'')!==table.columns.join(',')) throw new Error('Repair schema mismatch');
          buffer.push(line+'\r\n');header=false;continue;
        }
        read++;
        const id=line.slice(0,line.indexOf(','));
        if(!/^[CE][0-9a-f]{16}$/.test(id)) throw new Error('Unexpected multiline or quoted identifier');
        if(!droppedIds.has(id)){buffer.push(line+'\r\n');rows++;}
        if(buffer.length>=5000) await flush();
      }
      await flush();
      if(read!==old.files[table.filename].rows || oldHash.digest('hex')!==old.files[table.filename].sha256) throw new Error('Original staging CSV changed');
      const workbook=Workbook.create(),sheet=workbook.worksheets.add('Replacement');
      for(let offset=0;offset<table.rows.length;offset+=5000) {
        const matrix=table.rows.slice(offset,offset+5000);
        const range=sheet.getRangeByIndexes(0,0,matrix.length,table.columns.length);
        range.values=matrix;workbook.recalculate();
        const authored=range.values;
        for(let r=0;r<matrix.length;r++) for(let c=0;c<table.columns.length;c++)
          if((authored[r][c]??null)!==(matrix[r][c]??null)) throw new Error('Replacement typed value changed');
        const text=authored.map(row=>row.map(value=>{const s=value==null?'':String(value);return /[",\r\n]/.test(s)?`"${s.replaceAll('"','""')}"`:s;}).join(',')).join('\r\n')+'\r\n';
        await handle.write(text,null,'utf8');newHash.update(text,'utf8');rows+=matrix.length;
        range.clear({applyTo:'contents'});
      }
      await handle.close();
      metadata.table_rows[table.filename]=rows;
      metadata.files[table.filename]={rows,columns:table.columns.length,sha256:newHash.digest('hex')};
      await fs.rename(temporary,filename);
    }
    await fs.writeFile(path.join(directory,'manifest.json'),JSON.stringify(metadata,null,2)+'\n','utf8');
    await fs.unlink(path.join(directory,'validation.json')).catch(e=>{if(e.code!=='ENOENT') throw e;});
    console.log(`${entry.dataset}: replaced ${entry.case_ids.length} cases`);
    if(global.gc)global.gc();
  }
  const manifest=JSON.parse(await fs.readFile(path.join(outputDir,'manifest.json'),'utf8'));
  const columns=['dataset','anomaly_cases','normal_cases','process_rows','normal_process_rows','event_count','target_adapters','unique_content_cases'];
  const summary=[];
  for(const dataset of manifest.datasets) {
    const m=JSON.parse(await fs.readFile(path.join(outputDir,dataset,'manifest.json'),'utf8'));
    summary.push([dataset,m.anomaly_cases,m.normal_cases,m.files['processes.csv'].rows,m.files['normal_processes.csv'].rows,m.files['events.csv'].rows,Object.keys(m.target_adapter_case_counts).length,m.unique_content_cases]);
  }
  const workbook=Workbook.create(),sheet=workbook.worksheets.add('Summary');
  const matrix=[columns,...summary],range=sheet.getRangeByIndexes(0,0,matrix.length,columns.length);
  range.values=matrix;workbook.recalculate();
  const text='\uFEFF'+range.values.map(row=>row.join(',')).join('\r\n')+'\r\n';
  await fs.writeFile(path.join(outputDir,'scenario_summary.csv'),text,'utf8');
  manifest.files['scenario_summary.csv']={rows:summary.length,columns:columns.length,sha256:crypto.createHash('sha256').update(text,'utf8').digest('hex')};
  manifest.generation_constraints=plan.reason;
  manifest.replaced_case_counts=Object.fromEntries(plan.datasets.map(d=>[d.dataset,d.case_ids.length]));
  await fs.writeFile(path.join(outputDir,'manifest.json'),JSON.stringify(manifest,null,2)+'\n','utf8');
  process.exit(0);
}
const datasetIndex = args.indexOf('--dataset');
const selected = datasetIndex >= 0 ? args[datasetIndex+1] : undefined;
if (selected !== undefined && !['',...root.datasets].includes(selected)) throw new Error('Unknown dataset');
const directories = selected === undefined ? ['',...root.datasets] : [selected];
const resume = args.includes('--resume');
const workersIndex = args.indexOf('--workers');
const workers = workersIndex >= 0 ? Number(args[workersIndex+1]) : 1;
if (!Number.isInteger(workers) || workers < 1 || workers > 4) throw new Error('Use 1-4 bounded workers');
if (selected === undefined && workers > 1) {
  const queue = [...directories];
  let failed = false;
  await Promise.all(Array.from({length:workers},async()=>{
    while (queue.length) {
      const directory = queue.shift();
      await new Promise((resolve,reject)=>{
        const child = spawn(process.execPath,['--expose-gc',process.argv[1],inputDir,outputDir,'--dataset',directory,...(resume?['--resume']:[])],{stdio:'inherit',windowsHide:true});
        child.on('error',reject);
        child.on('exit',code=>{if(code!==0) failed=true; resolve();});
      });
    }
  }));
  if (failed) throw new Error('One or more CSV authoring workers failed; do not release partial outputs');
  process.exit(0);
}
const encode = v => {
  const text = v == null ? '' : String(v);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"','""')}"` : text;
};
for (const directory of directories) {
  const source = path.join(inputDir,directory), target = path.join(outputDir,directory);
  await fs.mkdir(target,{recursive:true});
  const metadata = JSON.parse(await fs.readFile(path.join(source,'metadata.json'),'utf8'));
  if (resume) {
    let saved = null;
    try { saved = JSON.parse(await fs.readFile(path.join(target,'manifest.json'),'utf8')); }
    catch(e) { if(e.code!=='ENOENT') throw e; }
    if (saved) {
      const {files,...oldMetadata} = saved;
      if (JSON.stringify(oldMetadata)!==JSON.stringify(metadata)) throw new Error(`Completed output belongs to a different generation: ${directory}`);
      for (const [filename,info] of Object.entries(files)) {
        if (path.basename(filename)!==filename || !filename.endsWith('.csv')) throw new Error('Unsafe checkpoint path');
        const hash = crypto.createHash('sha256');
        for await (const chunk of createReadStream(path.join(target,filename))) hash.update(chunk);
        if (hash.digest('hex')!==info.sha256) throw new Error(`Changed completed CSV: ${filename}`);
      }
      console.log(`${directory||'root'}: verified completed checkpoint`);
      continue;
    }
    // Only our named partial CSV exports, never unknown files or source data.
    for (const filename of Object.keys(metadata.table_rows)) {
      if (path.basename(filename)!==filename || !filename.endsWith('.csv')) throw new Error('Unsafe partial output path');
      const file = path.resolve(target,filename), base = path.resolve(outputDir)+path.sep;
      if (!file.startsWith(base)) throw new Error('Partial output escaped staging directory');
      await fs.unlink(file).catch(e=>{if(e.code!=='ENOENT') throw e;});
    }
  }
  const outputs = new Map();
  const lines = readline.createInterface({input:createReadStream(path.join(source,'tables.ndjson'),{encoding:'utf8'}),crlfDelay:Infinity});
  let blocks = 0;
  for await (const line of lines) {
    const table = JSON.parse(line);
    if (path.basename(table.filename) !== table.filename || !table.filename.endsWith('.csv')) throw new Error('Unsafe output path');
    let state = outputs.get(table.filename);
    if (!state) {
      const handle = await fs.open(path.join(target,table.filename),'wx');
      const workbook = Workbook.create();
      state = {handle,hash:crypto.createHash('sha256'),rows:0,columns:table.columns.length,
               workbook,sheet:workbook.worksheets.add('Records'),previousRange:null};
      outputs.set(table.filename,state);
    }
    const {workbook,sheet} = state;
    if (state.previousRange) state.previousRange.clear({applyTo:'contents'});
    const matrix = state.rows === 0 ? [table.columns,...table.rows] : table.rows;
    if (!matrix.length) continue;
    const range = sheet.getRangeByIndexes(0,0,matrix.length,table.columns.length);
    range.values = matrix;
    state.previousRange = range;
    workbook.recalculate();
    const authored = range.values;
    for (let r=0;r<matrix.length;r++) for (let c=0;c<table.columns.length;c++) {
      if ((authored[r][c]??null) !== (matrix[r][c]??null)) throw new Error(`Typed value changed: ${directory}/${table.filename}:${r}:${c}`);
    }
    const csv = (state.rows === 0 ? '\uFEFF' : '')+authored.map(r=>r.map(encode).join(',')).join('\r\n')+'\r\n';
    await state.handle.write(csv,null,'utf8');
    state.hash.update(csv,'utf8');
    state.rows += table.rows.length;
    if (++blocks%30===0) {
      console.log(`${directory||'root'}: ${blocks} bounded authoring blocks`);
      if (global.gc) global.gc();
    }
  }
  metadata.files = {};
  for (const [filename,state] of outputs) {
    await state.handle.close();
    if (state.rows !== (metadata.table_rows[filename]??0)) throw new Error(`Row count changed: ${filename}`);
    metadata.files[filename] = {rows:state.rows,columns:state.columns,sha256:state.hash.digest('hex')};
  }
  await fs.writeFile(path.join(target,'manifest.json'),JSON.stringify(metadata,null,2)+'\n','utf8');
  console.log(`${directory||'root'}: authored ${outputs.size} CSV files`);
}
