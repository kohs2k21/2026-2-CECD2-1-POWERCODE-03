"""Notion figures for our Process tasks only; no ADBench results mixed in."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from model_research_v13.data import read,write

OUT=ROOT/'reports/shared_model_v4_final';DEST=OUT/'notion';ALG=['if','kmeans','lof']
LABELS={'individual_delay':'개별 지연','group_delay':'집단 지연','column_relation':'컬럼 관계',
    'time_burst':'시간대 폭주','input_stop':'입력 중단','gradual_degradation':'점진적 악화','mixed_hard':'Hard 혼합'}

def run():
    DEST.mkdir(exist_ok=True);current=read(OUT/'summary.json');previous=read(ROOT/'reports/shared_model_v3_release/summary.json')
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False})
    for j,(a,name,color)in enumerate(zip(ALG,['Isolation Forest','MiniBatch K-means','LOF'],['#2563eb','#d97706','#059669'])):
        fig=plt.figure(figsize=(10,7.3));gs=fig.add_gridspec(2,1,height_ratios=[1.2,4.6],hspace=.35)
        ax=fig.add_subplot(gs[0]);ax.axis('off');fig.suptitle(name+' · 우리가 만든 Process task 평가',fontsize=17,y=.98)
        cols=['구분','Accuracy','Precision','Recall','F1','정상 오탐률'];fields=['accuracy','precision','recall','f1','normal_false_positive_rate']
        values=[[label,*[f'{r[k]*100:.2f}%'for k in fields]]for label,r in [('직전 모델',previous['pooled'][j]),('현재 모델',current['pooled'][j])]]
        table=ax.table(cellText=values,colLabels=cols,cellLoc='center',loc='center');table.auto_set_font_size(False);table.set_fontsize(11);table.scale(1,1.8)
        for (r,c),cell in table.get_celld().items():
            cell.set_edgecolor('#cbd5e1')
            if r==0:cell.set_facecolor('#e2e8f0');cell.set_text_props(weight='bold')
            elif r==2:cell.set_facecolor('#eff6ff')
        ax=fig.add_subplot(gs[1]);rows=current['datasets'];vals=[d['metrics'][j]['f1']*100 for d in rows]
        bars=ax.barh(np.arange(len(rows)),vals,color=color,height=.62);ax.bar_label(bars,fmt='%.2f%%',padding=5,fontsize=11)
        ax.set(yticks=range(len(rows)),yticklabels=[LABELS[d['dataset']]for d in rows],xlim=(0,105),xlabel='현재 시나리오별 F1 (%)',title='전체 F1 목표 달성 ≠ 모든 시나리오 해결');ax.invert_yaxis();ax.grid(axis='x',alpha=.15);ax.set_axisbelow(True)
        fig.subplots_adjust(left=.17,right=.96,bottom=.10,top=.91)
        fig.text(.5,.025,'동일 2,167,766개 1분 판정 · 6개 시나리오 + Hard 포함 · ADBench 결과 아님',ha='center',fontsize=10,color='#475569')
        fig.savefig(DEST/(a+'_task_performance_20261018.png'),dpi=160);plt.close(fig)
    payload={'current':current,'previous':previous,'selection':read(OUT/'selection.json'),
        'audit':read(OUT/'audit.json'),'fresh':read(OUT/'fresh_comparison.json'),'labels':LABELS}
    write(DEST/'task_page_payload.json',payload)
    print(json.dumps({'destination':str(DEST),'pooled':current['pooled'],'datasets':current['datasets'],
        'selected':{a:{'settings':s['settings'],'threshold':s['threshold'],'threshold_policy':s['threshold_policy']}for a,s in payload['selection']['models'].items()}},ensure_ascii=False))

if __name__=='__main__':run()
