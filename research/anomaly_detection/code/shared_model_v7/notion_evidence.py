"""Aggregate-only Notion figure/evidence export; never changes model artifacts."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from model_research_v13.data import read,write
from shared_model_v7.protocol import OUT,ALGORITHMS,TASKS
import numpy as np


def run():
    source=OUT/'release';dest=OUT/'notion_latest_20261019';dest.mkdir(exist_ok=True)
    s=read(source/'summary.json');selection=read(source/'selection.json')
    assert s['all_targets_met']and read(source/'audit.json')['executed_notebook_validated']
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False})
    names={'if':'Isolation Forest','kmeans':'MiniBatch K-means','lof':'LOF'}
    tasks=dict(zip(TASKS,['개별 지연','집단 지연','시간대 폭주','입력 중단','점진적 악화']))
    roles={'if':'individual_delay','kmeans':'group_delay','lof':'gradual_degradation'}
    metrics=['accuracy','precision','recall','f1','normal_false_positive_rate']
    for a in ALGORITHMS:
        rows=[('전체 합산',next(r for r in s['pooled']if r['method']==a))]
        rows +=[(tasks[t],next(r for d in s['datasets']if d['dataset']==t for r in d['metrics']if r['method']==a))for t in TASKS]
        data=[[label,*[f'{r[k]*100:.2f}%'for k in metrics]]for label,r in rows]
        fig,ax=plt.subplots(figsize=(13,4.8));ax.axis('off')
        fig.suptitle(names[a]+' · v7 전체·시나리오별 성능',fontsize=17,y=.96,fontweight='bold')
        table=ax.table(cellText=data,colLabels=['평가 범위','Accuracy','Precision','Recall','F1','정상 오탐률'],
                       cellLoc='center',colLoc='center',bbox=[0,.18,1,.72],colWidths=[.25,.15,.15,.15,.15,.15])
        table.auto_set_font_size(False);table.set_fontsize(11)
        for (i,j),cell in table.get_celld().items():
            cell.set_edgecolor('#d1d5db');cell.set_linewidth(.6)
            if i==0:cell.set_facecolor('#1e3a5f');cell.set_text_props(color='white',weight='bold')
            elif i==1:cell.set_facecolor('#e8f1ff');cell.set_text_props(weight='bold')
            elif rows[i-1][0]==tasks[roles[a]]:cell.set_facecolor('#e7f5ed')
            elif i%2==0:cell.set_facecolor('#f8fafc')
        fig.text(.06,.12,'모든 지표는 분 단위 판정 기준. FPR = 정상 분 중 잘못 경보한 비율.',fontsize=10)
        fig.text(.06,.065,'정상 통계·경보 보정을 포함한 개발 벤치마크 결과. 순수 알고리즘 또는 외부 블라인드 성능 아님.',fontsize=10,color='#4b5563')
        fig.subplots_adjust(left=.04,right=.98,top=.9,bottom=.02)
        fig.savefig(dest/(a+'_latest_performance_20261019.png'),dpi=150,bbox_inches='tight');plt.close(fig)
    colors=['#2563eb','#d97706','#059669'];x=np.arange(len(TASKS))
    fig,axes=plt.subplots(2,1,figsize=(13,9))
    for ax,key,title in zip(axes,['recall','f1'],['시나리오별 Recall','시나리오별 F1']):
        for i,(a,color)in enumerate(zip(ALGORITHMS,colors)):
            values=[100*next(r for d in s['datasets']if d['dataset']==t for r in d['metrics']if r['method']==a)[key]for t in TASKS]
            bars=ax.bar(x+(i-1)*.26,values,width=.24,color=color,label=names[a]);ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
        ax.set(xticks=x,xticklabels=list(tasks.values()),ylim=(0,110),ylabel=key+' (%)',title=title);ax.legend(ncol=4,fontsize=9)
        ax.spines[['top','right']].set_visible(False)
    fig.tight_layout();fig.savefig(dest/'latest_task_recall_f1_20261019.png',dpi=150,bbox_inches='tight');plt.close(fig)
    fig,ax=plt.subplots(figsize=(13,5))
    for i,(a,color)in enumerate(zip(ALGORITHMS,colors)):
        values=[100*next(r for d in s['datasets']if d['dataset']==t for r in d['metrics']if r['method']==a)['normal_false_positive_rate']for t in TASKS]
        bars=ax.bar(x+(i-1)*.26,values,width=.24,color=color,label=names[a]);ax.bar_label(bars,fmt='%.4f',padding=3,fontsize=9)
    peak=max(100*r['normal_false_positive_rate']for d in s['datasets']for r in d['metrics'])
    ax.set(xticks=x,xticklabels=list(tasks.values()),ylim=(0,max(.1,peak*1.25)),ylabel='정상 오탐률 (%)',title='시나리오별 정상 오탐률')
    ax.spines[['top','right']].set_visible(False);ax.legend(ncol=4,fontsize=9)
    fig.tight_layout();fig.savefig(dest/'latest_task_fpr_20261019.png',dpi=150,bbox_inches='tight');plt.close(fig)
    write(dest/'latest_current_metrics_20261019.json',{'version':'v7','datasets':s['datasets'],'pooled':s['pooled'],'protocol':s['protocol']})
    audit=read(source/'audit.json')
    model_records={}
    for a,v in selection['models'].items():
        r=v['record'];model_records[a]={k:r[k]for k in ('actual_estimator_parameters','transformed_columns','fit_rows','calibration_groups',
            'model_weight_point','model_weight_burst','filter_parameters','observed_volume_margin')}
        model_records[a]['actual_estimator_features']=v['actual_estimator_features']
    write(dest/'latest_experiment_evidence_20261019.json',{
        'version':'v7','date':'2026-10-19','minimum_task_recall':.55,'maximum_task_and_pooled_FPR':.06,
        'models':model_records,'audit':{k:audit[k]for k in ('unit_tests_run','unit_tests_passed','independent_metrics_recomputed',
            'exported_handoff_scores_verified_against_full_replay','executed_notebook_validated','all_benchmark_file_hashes_verified')},
        'roles':s['requested_roles'],'model_package_sha256':selection['model_sha256'],
        'normal_only_estimator_fit':True,'benchmark_labels_used_for_calibration_selection':True,
        'external_blind_test':False,'ADBench_changed':False,'production_deployed':False})
    print('Aggregate Notion exports prepared:',dest,flush=True)


if __name__=='__main__':run()
