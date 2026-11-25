"""Produce a standalone scientific report from measured results, never fabricated metrics."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def markdown_table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
        *['| '+' | '.join(map(str,row))+' |' for row in rows]])


def figures(out,quality,load):
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(2,2,figsize=(14,7),layout='constrained')
    for row,family in enumerate(['gutentag','tsb_csv']):
        data=np.load(out/(family+'-representative.npz'),allow_pickle=False)
        base,changed,mask=data['base'],data['changed'],data['labels'].astype(bool)
        locations=np.flatnonzero(mask);start,finish=locations[0],locations[-1]+1
        for col in range(2):
            ax=axes[row,col];ax.plot(base,color='#64748b',lw=.7,label='Paired original')
            ax.plot(np.flatnonzero(mask),changed[mask],color='#dc2626',lw=1.5,label='One injected interval')
            ax.axvspan(start,finish,color='#f97316',alpha=.12)
            ax.set_xlabel('Observation index');ax.set_ylabel('Signal value' if row==0 else 'Completed process duration (ms)')
            ax.grid(alpha=.18);ax.set_title(('GutenTAG: mean +2' if row==0 else 'Real PROCESS: delay +6000 ms')+(' / full test' if col==0 else ' / interval detail'))
            if col==1:
                ax.set_xlim(max(0,start-300),min(len(base),finish+300))
                visible=np.concatenate([base[max(0,start-300):min(len(base),finish+300)],changed[mask]])
                margin=max(float(np.ptp(visible))*.1,.1);ax.set_ylim(visible.min()-margin,visible.max()+margin)
        axes[row,0].legend(loc='upper right',fontsize=8)
    fig.savefig(out/'scenarios.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
    gut=quality[(quality.family=='gutentag')&(quality.length==200)]
    for detector,color in [('iforest','#2563eb'),('median_mad','#9333ea')]:
        values=gut[gut.detector==detector].groupby('magnitude').injected_recall.agg(['mean','std'])
        axes[0].errorbar(values.index,values['mean']*100,yerr=values['std']*100,marker='o',label=detector,color=color,capsize=3)
    axes[0].set(title='GutenTAG: injected-point recall / 200 samples',xlabel='Mean offset',ylabel='Recall (%)',ylim=(0,105))
    real=quality[(quality.family=='tsb_csv')&(quality.kind=='latency_add')&(quality.length==200)&(quality.detector=='iforest')]
    for q,color in [(.99,'#2563eb'),(.95,'#ea580c')]:
        v=real[np.isclose(real.threshold_quantile,q)].groupby('magnitude').additional_injected_coverage.agg(['mean','std'])
        axes[1].errorbar(v.index/1000,v['mean']*100,yerr=v['std']*100,marker='o',label=f'Calibration q={q}',color=color,capsize=3)
    axes[1].set(title='Real CSV: additional coverage beyond paired original',xlabel='Injected delay (seconds)',ylabel='Additional coverage (%)',ylim=(0,105))
    for ax in axes:ax.legend(fontsize=8);ax.grid(alpha=.2)
    fig.savefig(out/'quality.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(12,7),layout='constrained')
    for row,family in enumerate(['gutentag','tsb_csv']):
        for batch,color in [(1,'#dc2626'),(32,'#2563eb')]:
            subset=load[(load.family==family)&(load.batch_max==batch)]
            grouped=subset.groupby('target_rate').mean(numeric_only=True)
            axes[row,0].plot(grouped.index,grouped.within_budget_all_offered_fraction*100,'o-',label=f'batch <= {batch}',color=color)
            axes[row,1].plot(grouped.index,grouped.p95_scheduled_to_result_ms,'o-',label=f'batch <= {batch}',color=color)
        axes[row,0].set(title=family+' / completed within 1 second',ylabel='All offered rows (%)',ylim=(0,105))
        axes[row,1].set(title=family+' / completed-only latency',ylabel='p95 (ms)')
        for ax in axes[row]:
            ax.set_xscale('log');ax.set_xlabel('Target rows / second');ax.set_xticks([10,100,1000,5000],labels=['10','100','1000','5000']);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(out/'load.png',dpi=160);plt.close(fig)


def report(out):
    out=Path(out);manifest=json.loads((out/'manifest.json').read_text())
    if manifest['status']!='complete':raise ValueError('quality/load run is not complete')
    quality=pd.read_csv(out/'quality.csv');load=pd.read_csv(out/'load.csv');micro=pd.read_csv(out/'microbenchmark.csv')
    if quality.isna().loc[:,['injected_recall','threshold_quantile']].any().any():raise ValueError('incomplete quality measurements')
    config=json.loads((out/'config.json').read_text())
    expected_conditions=2*len(config['load_rates'])*len(config['load_batches'])*config['load_repeats']
    if len(load)!=expected_conditions or (load.offered!=load.rejected+load.completed_by_cutoff+load.errors+load.unresolved_at_cutoff).any():
        raise ValueError('load conditions or accounting incomplete')
    figures(out,quality,load)
    data=manifest['real_csv'];lines=['# 단일 시나리오 합성 데이터 실험 결과','',
        '실행일: 2026-10-18. 기존 미커밋 서비스 변경을 모두 원복한 뒤 별도 연구 실험으로 수행했다. 서버 접속·커밋·푸시는 하지 않았다.',
        '', '## 1. 실험 범위','',
        f"- GutenTAG 및 TSB-UAD 실제 공식 코드, 5 seed, 이상 길이 20/200 관측. 결과 {len(quality)}행은 시나리오·모델·임계값별 평가 결과이며 독립 고객 데이터 수가 아니다.",
        '- 신규 연구용 단변량 IF와 비교용 median/MAD. 운영 피처 생성·Rule·LLM·기존 모델 정확도 검증과 구분한다.',
        '- GutenTAG는 정확한 합성 정답. 실제 CSV는 성공·완료 등 후보 필터를 썼지만 장애 정답은 없다. 주입 검출과 기존 원본 경보를 분리했다.',
        f"- 원본 P {data['source_rows']:,}행 → 유효 후보 {data['eligible_rows']:,}행 → 가장 큰 동일 프로세스 {data['selected_rows']:,}행. 학습/보정/시험: {data['split_rows']['train']:,}/{data['split_rows']['calibration']:,}/{data['split_rows']['test']:,}행.",
        '- 거래별 완료 시각순 분할. 원본 숫자·행 파일을 덮어쓰지 않았고, 이상 구간 밖 값의 완전 일치를 검사했다. 처리시간(ms)은 완료 관측 전용이다.',
        '', '## 2. GutenTAG: 정상 패턴에 평균 이동 하나','',
        '아래는 길이 200 관측의 5 seed 평균이다. 임계값은 별도 정상 보정 점수 99분위다. 작은 평균 이동은 정상 사인파 범위와 겹쳐 놓칠 수 있다. 전체 길이 20 결과와 seed별 원시는 runs의 quality.csv에 있다.','']
    rows=[]
    g=quality[(quality.family=='gutentag')&(quality.length==200)]
    for (magnitude,detector),part in g.groupby(['magnitude','detector']):
        rows.append([magnitude,detector,f'{part.injected_recall.mean()*100:.1f}%',f'{part.f1_against_injection_mask.mean():.3f}',
            f'{part.paired_background_alarm_rate.mean()*100:.2f}%',f'{part.event_hit.mean()*100:.0f}%'])
    lines+=[markdown_table(['평균 이동','모델','점별 Recall','점별 F1','정상 대조 경보율','사건 내 1회 이상 적중'],rows),
        '', '## 3. TSB-UAD: 실제 CSV의 한 구간에 처리 지연 주입','',
        'IF, 길이 200 관측, 5 seed 평균. `추가 탐지`는 원본 쌍에서 울리지 않았지만 주입 뒤 울린 주입 구간의 비율이다. 원본 경보율은 정답이 없어 실제 오탐률이라고 부르지 않는다.','']
    rows=[];r=quality[(quality.family=='tsb_csv')&(quality.kind=='latency_add')&(quality.length==200)&(quality.detector=='iforest')]
    for (q,magnitude),part in r.groupby(['threshold_quantile','magnitude'],sort=False):
        rows.append([f'{q:.0%}',f'+{magnitude/1000:g}초',f'{part.injected_recall.mean()*100:.1f}%',
            f'{part.additional_injected_coverage.mean()*100:.1f}%',f'{part.paired_background_alarm_rate.mean()*100:.2f}%'])
    lines+=[markdown_table(['보정 분위','지연','주입 구간 검출','원본 대비 추가 탐지','원본 경보율'],rows),
        '', '- 기존 처리시간의 긴 꼬리 때문에 99분위 임계값은 주입 지연을 거의 추가 검출하지 못했다. 실패를 그대로 보존했다.',
        '- 95분위는 초벌 실패를 본 뒤 추가한 탐색 조건이다. 모든 결과를 공개하며 독립적인 최종 검증이나 최적값 선택으로 주장하지 않는다.',
        '- 순서만 뒤집는 flip 대조도 수행했다. 점별 IF·MAD의 전체 경보 개수는 순열에 불변이다. 구간 내 기존 이상값이 이동해 적중처럼 보일 수 있지만 시간 순서 변화를 학습해 잡은 증거는 아니다.',
        '', '## 4. 순수 추론 연산 성능','',
        '각 100회 측정. IF 모델·Scaler만 호출한 결과다. 배치 처리량을 DB·네트워크 포함 처리량으로 해석하지 않는다.','']
    rows=[]
    for _,v in micro[micro.detector=='iforest'].iterrows():
        rows.append([v.family,int(v.batch),f'{v.throughput_rows_per_second:,.0f}',f'{v.call_p95_ms:.3f}'])
    lines+=[markdown_table(['데이터','배치','순수 추론 행/초','호출 p95 ms'],rows),
        '', '## 5. 실제 시간으로 수행한 부하 시험','',
        '단일 IF 작업 스레드·상한 512 메모리 큐·배치 상한 1/32·각 8초·조건별 2회·최대 drain 5초. 조건 순서는 고정 seed로 섞었다. p95는 완료된 요청에 한정하므로 거절·미완료를 별도 집계하고, 1초 이내 완료율은 전체 offered를 분모로 계산했다.','']
    rows=[]
    for (family,batch,rate),part in load.groupby(['family','batch_max','target_rate']):
        rows.append([family,int(batch),int(rate),int(part.offered.sum()),int(part.rejected.sum()),int(part.unresolved_at_cutoff.sum()),
            f'{part.p95_scheduled_to_result_ms.min():.2f}–{part.p95_scheduled_to_result_ms.max():.2f}',
            f'{part.within_budget_all_offered_fraction.min()*100:.1f}–{part.within_budget_all_offered_fraction.max()*100:.1f}%',int(part.queue_peak.max())])
    lines+=[markdown_table(['데이터','배치 상한','목표 행/초','총 offered 2회','거절','미완료','완료분 p95 범위 ms','전체 1초 완료율 범위','최대 큐'],rows),
        '', f"- 총 부하 offered {int(load.offered.sum()):,}건, 완료 {int(load.completed_by_cutoff.sum()):,}건, 거절 {int(load.rejected.sum()):,}건, 미완료 {int(load.unresolved_at_cutoff.sum()):,}건, 연산 오류 {int(load.errors.sum()):,}건.",
        f"- 조건별 샘플링 최대 RSS 범위 {load.sampled_peak_rss_mib.min():.1f}–{load.sampled_peak_rss_mib.max():.1f} MiB. 평균 프로세스 CPU 범위 {load.mean_process_cpu_percent.min():.1f}–{load.mean_process_cpu_percent.max():.1f}% (100%=논리 코어 1개 상당, 전용 CPU 고정 아님).",
        '- 대상은 로컬 macOS ARM의 연산·메모리 큐다. PostgreSQL·수집 API·Docker·SSE·LLM/GPU는 제외했다. 운영 서버 처리량 또는 ESB 발생부터 판정까지의 SLA는 아직 검증하지 않았다.',
        '', '## 6. 시행착오와 해석','',
        '1. NumPy 1.26.4에서 최신 GutenTAG 내부 API import 실패 → upstream을 바꾸지 않고 NumPy 2.2.6으로 고정.',
        '2. TSB-UAD import가 만든 pycache를 소스 변경으로 오인 → pycache만 허용하고 추적 코드/새 소스 변경은 계속 거부.',
        '3. 초벌 start-time 분할 → 완료 후 처리시간 가용성에 맞춰 최종 completion-time 분할로 수정. 초벌 수치는 최종 표에서 제외.',
        '4. 합성 강한 이상 성공 → 실제 긴 꼬리에서 놓침. 합성 결과만으로 실제 데이터 성능을 주장하지 않음.',
        '5. 임계값 99→95분위 탐색에서 민감도와 원본 경보 증가를 함께 공개. 실제 정상 라벨과 운영 경보 예산이 있어야 선택 가능.',
        '6. 순서 이상은 현재 점별 입력의 한계. 다음 실험 후보는 과거값만 쓰는 이동 기준·시차 피처지만 이번 결과에 섞지 않음.',
        '', '## 7. 재현·계보','',
        f"- Python {manifest['environment']['python']}, {manifest['environment']['platform']}",
        f"- GutenTAG commit `{manifest['upstream']['gutentag']}`",f"- TSB-UAD commit `{manifest['upstream']['tsb_uad']}`",
        f"- 원본 CSV SHA256 `{data['source_sha256']}`",f"- 설정 SHA256 `{manifest['config_sha256']}`",
        f"- 실행 결과: `{out.name}`. manifest에 실행 모듈 4개의 SHA256·모델 SHA256·분할·환경 버전 포함.",
        '- 원본 식별자·메시지 본문·토큰은 결과 문서에 포함하지 않았다. 원시 생성값·모델·개별 결과는 Git 제외 runs에 보관.',
        '', '공식 소스: [GutenTAG](https://github.com/TimeEval/GutenTAG), [TSB-UAD](https://github.com/TheDatumOrg/TSB-UAD).',
        '', '실행 방법은 README.md, 사전 조건과 추가 탐색 이력은 PROTOCOL.md를 참고한다.']
    text='\n'.join(lines)+'\n'
    (out/'RESULTS.md').write_text(text)
    Path(__file__).with_name('RESULTS.md').write_text(text)
    summary={'quality_rows':len(quality),'load_conditions':len(load),'offered':int(load.offered.sum()),
        'rejected':int(load.rejected.sum()),'completed':int(load.completed_by_cutoff.sum()),'errors':int(load.errors.sum()),
        'figures':['scenarios.png','quality.png','load.png']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    report(parser.parse_args().output)
