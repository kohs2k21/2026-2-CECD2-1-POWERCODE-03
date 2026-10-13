"""Conventional bars, histograms and confusion matrices; no decorative encoding."""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

from .models import METHODS

LABELS = ["IF\n통합", "IF\nAdapter", "IF\n앙상블", "K-means\n통합", "K-means\nAdapter", "K-means\n앙상블"]
NAMES = dict(zip(METHODS,[v.replace("\n"," ") for v in LABELS]))
SCENARIO_NAMES = {"individual_delay":"개별 지연","group_delay":"집단 지연","column_relation":"컬럼 관계",
                  "time_burst":"시간대 폭주","input_stop":"입력 중단","gradual_degradation":"점진적 악화","mixed_hard":"혼합 Hard"}
COLORS = ["#7596BC", "#4C75A3", "#234A77", "#D79B88", "#BE705B", "#964733"]


def style():
    fonts = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic","Noto Sans CJK KR","NanumGothic"):
        if name in fonts:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams.update({"font.size":11,"figure.dpi":115,"axes.unicode_minus":False,
                         "axes.spines.top":False,"axes.spines.right":False,"figure.facecolor":"white"})


def table(summary, unit="window", supported=False):
    rows = summary["supported_only" if supported else "pooled"][unit]
    frame = pd.DataFrame(rows)
    frame["method"] = frame.method.map(NAMES)
    return frame[["method","n","normal","anomaly","accuracy","precision","recall","f1","normal_false_positive_rate","always_normal_accuracy"]].rename(columns={
        "method":"모델","n":"평가 수","normal":"정상 수","anomaly":"이상 수","accuracy":"Accuracy",
        "precision":"Precision","recall":"Recall","f1":"F1","normal_false_positive_rate":"정상 오탐률","always_normal_accuracy":"모두 정상 예측 Accuracy"})


def overview(summary, unit="window"):
    rows = summary["pooled"][unit]
    fig, axes = plt.subplots(2,2,figsize=(15,9),layout="constrained")
    for ax,metric in zip(axes.flat,("accuracy","precision","f1","recall")):
        values = [np.nan if r[metric] is None else r[metric] for r in rows]
        ax.bar(np.arange(6),values,color=COLORS,width=.65)
        ax.set_xticks(np.arange(6),LABELS)
        ax.set(title=metric.capitalize(),ylim=(0,1.16))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(axis="y",alpha=.18); ax.set_axisbelow(True)
        for i,value in enumerate(values):
            ax.text(i,.025 if np.isnan(value) else value+.025,"N/A" if np.isnan(value) else f"{value:.2%}",ha="center",fontsize=10)
    width = summary['protocol'].get('window_minutes',1)
    fig.suptitle("6개 모델 비교 — "+(f"최근 {width}분 시간창 · 1분마다 판단" if unit=="window" else "개별 Process 행 기준"),fontsize=15)
    return fig


def confusion(summary, unit="window"):
    fig,axes = plt.subplots(2,3,figsize=(15,9),layout="constrained")
    for ax,row in zip(axes.flat,summary["pooled"][unit]):
        matrix = np.array([[row["tn"],row["fp"]],[row["fn"],row["tp"]]])
        rates = np.divide(matrix,matrix.sum(axis=1,keepdims=True),out=np.zeros_like(matrix,dtype=float),where=matrix.sum(axis=1,keepdims=True)>0)
        ax.imshow(rates,vmin=0,vmax=1,cmap="Blues")
        for r in range(2):
            for c in range(2):
                ax.text(c,r,f"{matrix[r,c]:,}\n({rates[r,c]:.1%})",ha="center",va="center",color="white" if rates[r,c]>.55 else "#172B42",fontsize=11)
        ax.set(xticks=[0,1],yticks=[0,1],xticklabels=["정상 예측","이상 예측"],yticklabels=["실제 정상","실제 이상"],title=NAMES[row["method"]])
        ax.set_xlabel("예측"); ax.set_ylabel("정답")
    fig.suptitle("혼동행렬 — 숫자는 건수, 색·괄호는 정답 클래스 내 비율",fontsize=14)
    return fig


def scenario_bars(summary, unit="window", metric="f1"):
    datasets = summary["datasets"]
    fig,axes = plt.subplots(2,1,figsize=(15,10),layout="constrained")
    for ax,offset,title in zip(axes,(0,3),("Isolation Forest","MiniBatch K-means")):
        x = np.arange(len(datasets))
        for local,label in enumerate(("통합","Adapter (미지원=통합)","앙상블")):
            method = METHODS[offset+local]
            values = []
            for d in datasets:
                records = d[unit].get("metrics",[])
                row = next((r for r in records if r["method"]==method),{})
                value = row.get(metric)
                values.append(np.nan if value is None else value)
            ax.bar(x+(local-1)*.24,values,width=.23,color=COLORS[offset+local],label=label)
        ax.set_xticks(x,[SCENARIO_NAMES[d["dataset"]].replace(" ","\n") for d in datasets])
        ax.set(title=f"{title} — 시나리오별 {metric.upper()}",ylim=(0,1.12))
        ax.yaxis.set_major_formatter(PercentFormatter(1)); ax.grid(axis="y",alpha=.18); ax.set_axisbelow(True)
        ax.legend(ncols=3,loc="upper right",fontsize=10)
        for i,d in enumerate(datasets):
            if d[unit]["status"]!="assessed":
                ax.text(i,.04,"N/A",ha="center")
    return fig


def margins(output, summary, unit="window"):
    normal, anomaly = [], []
    for dataset in summary["datasets"]:
        if dataset[unit]["status"]!="assessed":
            continue
        for cohort in ("observed","normal"):
            with np.load(Path(output)/dataset["dataset"]/f"{unit}_{cohort}.npz",allow_pickle=False) as arrays:
                valid,y,score = arrays["valid"],arrays["truth"],arrays["margin"]
                normal.append(score[valid & ~y]); anomaly.append(score[valid & y])
    normal,anomaly = np.concatenate(normal),np.concatenate(anomaly)
    fig,axes = plt.subplots(2,3,figsize=(16,10),layout="constrained")
    for i,ax in enumerate(axes.flat):
        n,a = normal[:,i],anomaly[:,i]
        combined = np.concatenate([n,a])
        lo,hi = np.quantile(combined,[.005,.995])
        lo,hi = min(float(lo),-.05),max(float(hi),.05)
        bins = np.linspace(lo,hi,41)
        for values,color,label in ((n,"#4C75A3","정상 정답"),(a,"#C45E52","이상 정답")):
            visible = values[(values>=lo)&(values<=hi)]
            ax.hist(visible,bins=bins,density=True,color=color,alpha=.5,label=label)
        ax.axvline(0,color="#222222",ls="--",lw=1.6,label="판정선 0")
        outside_n = np.mean((n<lo)|(n>hi)); outside_a = np.mean((a<lo)|(a>hi)) if len(a) else 0
        ax.set(title=NAMES[METHODS[i]],xlabel="정규화 점수 − 해당 경로 임계값",ylabel="확률밀도 (표시 범위 내 면적 1)")
        ax.text(.02,.98,f"표시 밖: 정상 {outside_n:.2%}, 이상 {outside_a:.2%}",transform=ax.transAxes,va="top",fontsize=9)
        ax.legend(fontsize=9)
    fig.suptitle("일반 히스토그램 — 판정은 전체 값으로 수행, 그림만 중앙 범위 표시",fontsize=14)
    return fig


def hard_results(summary):
    hard = next(d for d in summary["datasets"] if d["dataset"]=="mixed_hard")
    events = hard["window"]["events"]
    types = list(SCENARIO_NAMES)[:-1]
    matrix = np.array([[next(r["event_recall"] for r in events["per_type"] if r["scenario"]==name and r["method"]==method) for method in METHODS] for name in types])
    fig,axes = plt.subplots(2,1,figsize=(14,10),layout="constrained",gridspec_kw={"height_ratios":[2,1]})
    ax = axes[0]
    ax.imshow(matrix,vmin=0,vmax=1,cmap="Blues",aspect="auto")
    ax.set_xticks(np.arange(6),LABELS); ax.set_yticks(np.arange(6),[SCENARIO_NAMES[n] for n in types])
    for r in range(6):
        for c in range(6):
            ax.text(c,r,f"{matrix[r,c]:.1%}",ha="center",va="center",color="white" if matrix[r,c]>.55 else "#172B42")
    ax.set_title("혼합 Hard — 각 유형의 사건 탐지율")
    values = [events["all_six_recall"][m] for m in METHODS]
    axes[1].bar(np.arange(6),values,color=COLORS,width=.65)
    axes[1].set_xticks(np.arange(6),LABELS); axes[1].set(title="한 사례의 6개 사건을 모두 탐지한 비율",ylim=(0,1.12))
    axes[1].yaxis.set_major_formatter(PercentFormatter(1)); axes[1].grid(axis="y",alpha=.18); axes[1].set_axisbelow(True)
    for i,value in enumerate(values):
        axes[1].text(i,value+.02,f"{value:.2%}",ha="center")
    return fig


def timeline(output, summary, dataset="input_stop", case_index=0):
    path = Path(output)/dataset
    with np.load(path/"window_observed.npz",allow_pickle=False) as source:
        observed = {k:source[k][source["case_index"]==case_index] for k in source.files if source[k].shape[0]==len(source["case_index"])}
    with np.load(path/"window_normal.npz",allow_pickle=False) as source:
        normal = {k:source[k][source["case_index"]==case_index] for k in source.files if source[k].shape[0]==len(source["case_index"])}
    minutes = (observed["window_end_seconds"]-observed["window_end_seconds"][0])/60
    fig,axes = plt.subplots(2,1,figsize=(14,7),layout="constrained",sharex=True)
    for ax,key,title in zip(axes,("native_execution_arrivals","duration_p95_ms"),("현재 1분 실행 수","선택 시간창의 완료 Process 시간 P95 (ms)")):
        if key not in observed: key = 'execution_arrivals'
        ax.plot(minutes,normal[key],label="정상 대조군",color="#4C75A3",lw=1.6)
        ax.plot(minutes,observed[key],label="장애 주입 관측",color="#C45E52",lw=1.6)
        for x,flag in zip(minutes,observed["truth"]):
            if flag:
                ax.axvspan(x-1,x,color="#D3A545",alpha=.16)
        ax.set_ylabel(title); ax.legend(loc="upper right"); ax.grid(alpha=.15)
    axes[-1].set_xlabel("사례 시작 후 경과 시간 (분)")
    fig.suptitle(f"{SCENARIO_NAMES[dataset]} — 고정된 첫 사례, 음영은 실제 이상 구간",fontsize=14)
    scorefig,scoreaxes = plt.subplots(2,3,figsize=(15,9),layout="constrained")
    for i,ax in enumerate(scoreaxes.flat):
        ax.plot(minutes,normal["margin"][:,i],color="#4C75A3",label="정상 대조군",lw=1.3)
        ax.plot(minutes,observed["margin"][:,i],color="#C45E52",label="장애 주입 관측",lw=1.3)
        ax.axhline(0,color="#222222",ls="--",lw=1.2,label="판정선 0")
        for x,flag in zip(minutes,observed["truth"]):
            if flag:
                ax.axvspan(x-1,x,color="#D3A545",alpha=.16)
        ax.set(title=NAMES[METHODS[i]],xlabel="경과 시간 (분)",ylabel="점수 − 임계값")
        ax.legend(fontsize=8)
    return fig,scorefig


def window_search(selection):
    fig,axes = plt.subplots(1,2,figsize=(15,5),layout='constrained')
    candidates = selection['candidates']
    x = np.arange(len(candidates))
    labels = [f"{c['minutes']}분" for c in candidates]
    colors = ['#234A77' if c['minutes']==selection['selected_minutes'] else '#A9BED3' for c in candidates]
    for ax,key,title in zip(axes,('objective_macro_f1','mean_macro_normal_fpr'),('검증: 6개 모델의 시나리오 평균 F1','검증: 평균 정상 오탐률')):
        values = [c[key] for c in candidates]
        ax.bar(x,values,color=colors,width=.65)
        ax.set_xticks(x,labels); ax.set_title(title)
        ax.set_ylim(0,max(values)*1.3+.01); ax.yaxis.set_major_formatter(PercentFormatter(1))
        for i,v in enumerate(values): ax.text(i,v+.003,f'{v:.2%}',ha='center')
        ax.grid(axis='y',alpha=.18); ax.set_axisbelow(True)
    return fig


def window_before_after(before,after):
    fig,axes = plt.subplots(1,3,figsize=(16,5),layout='constrained')
    for ax,key in zip(axes,('f1','recall','normal_false_positive_rate')):
        for summary,offset,color,label in ((before,-.18,'#A9BED3','고정 1분 (동일 새 학습 참조)'),(after,.18,'#234A77',f"선택 {after['protocol']['window_minutes']}분")):
            ax.bar(np.arange(6)+offset,[r[key] for r in summary['pooled']['window']],width=.34,color=color,label=label)
        ax.set_xticks(np.arange(6),LABELS); ax.set_title(key)
        ax.yaxis.set_major_formatter(PercentFormatter(1)); ax.grid(axis='y',alpha=.18); ax.set_axisbelow(True)
        ax.legend(fontsize=8)
    return fig
