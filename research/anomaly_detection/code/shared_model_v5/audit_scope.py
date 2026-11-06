"""Revalidate bytes; inherit prior rule results ONLY for unchanged task files."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
from model_research_v13.data import read,write
from model_evaluation.data import sha256,verify_benchmark
from shared_model_v5.protocol import TASKS,RETIRED,OUT,freeze

def run():
    p=freeze();archive=ROOT/'reports/retired_tasks_20261018';root=ROOT/'data/evaluation'
    old=read(archive/'validation.json');assert old['passed'];records=[]
    for name in (*TASKS,*RETIRED):
        folder=(root if name in TASKS else archive)/name
        manifest=read(folder/'manifest.json')
        if name in TASKS:assert manifest==p['source_five_task_file_manifests'][name]
        for filename,info in manifest['files'].items():
            actual=sha256(folder/filename);assert actual==info['sha256'],(name,filename)
            records.append({'task':name,'file':filename,'sha256':actual,'unchanged':True})
    assert read(root/'manifest.json')['datasets']==list(TASKS)
    assert sha256(ROOT/'data/adbench_1000/dataset.npz')==p['ADBench_npz_sha256']
    report={**old,'coverage':'all_five_active_datasets','datasets':{k:old['datasets'][k]for k in TASKS},
        'anomaly_cases':25000,'normal_cases':25000,
        'provenance':{'method':'Inherited previous complete rule executions after exact SHA256 verification of every retained CSV; NOT new rule executions',
            'prior_validation':'reports/retired_tasks_20261018/validation.json','prior_validation_sha256':sha256(archive/'validation.json'),
            'all_retained_and_archived_task_files_verified_unchanged':True}}
    write(root/'validation.json',report)
    verify_benchmark(root,ROOT/'reports/evaluation_v2_work/inputs.json')
    write(OUT/'scope_audit.json',{'passed':True,'retained_data_or_labels_changed':False,'ADBench_unchanged':True,
        'active_root_manifest_sha256':sha256(root/'manifest.json'),'parent_root_manifest_sha256':p['parent_benchmark_manifest_sha256'],
        'files':records,'retired_recoverable_at':str(archive.relative_to(ROOT))})
    print('SCOPE PASS:',len(records),'task files unchanged; active five tasks; archived two; ADBench unchanged',flush=True)

if __name__=='__main__':run()
