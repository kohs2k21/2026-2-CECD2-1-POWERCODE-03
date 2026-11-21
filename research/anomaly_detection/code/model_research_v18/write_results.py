"""Generate evidence-backed final results without confusing model/data effects."""
from pathlib import Path
from collections import defaultdict
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
from model_research_v13.data import read, write
from model_research_v18.run import OUTPUT

NAMES = tuple(f"{a} {r}" for a in ("IF", "K-means", "LOF") for r in ("전체", "Adapter", "앙상블"))
TASKS = {"individual_delay": "개별 지연", "group_delay": "집단 지연", "column_relation": "컬럼 관계",
         "time_burst": "시간대 폭주", "input_stop": "입력 중단", "gradual_degradation": "점진적 악화", "mixed_hard": "혼합 Hard"}
KEYS = ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")


def stage(name): return read(ROOT/read(OUTPUT/f"{name}_evaluation_index.json")["current"] / "summary.json")


def metrics_table(records):
    rows = ["| 전략 | Accuracy | Precision | Recall | F1 | 정상 오탐 |", "|---|---:|---:|---:|---:|---:|"]
    rows += ["| "+NAMES[i]+" | "+" | ".join(f"{r[k]*100:.2f}%" for k in KEYS)+" |" for i,r in enumerate(records)]
    return "\n".join(rows)


def build():
    old = read(ROOT/"reports/model_research_v14/evaluation_7f2dc4351af61664/summary.json")
    parent, adjusted, difficulty = stage("parent"), stage("adjusted"), stage("difficulty_only")
    confirmation = read(OUTPUT/"validation_results.json")
    adjustments = defaultdict(list)
    for row in read(ROOT/"reports/benchmark_mild_v2_work/adjustments.json"):
        adjustments[row["scenario"]].append(row)
    pairs = {"individual_delay": ("old_duration_sum_ms","new_duration_sum_ms"),
             "group_delay": ("old_duration_sum_ms","new_duration_sum_ms"),
             "column_relation": ("old_duration_sum_ms","new_duration_sum_ms"),
             "time_burst": ("old_added","new_added"), "input_stop": ("old_minutes","new_minutes"),
             "gradual_degradation": ("old_increment_ms","new_increment_ms")}
    adjustment_summary = {name: {"cases": len(rows), "cases_with_quantity_changed":
        sum(r[pairs[name][0]] != r[pairs[name][1]] for r in rows),
        "compared_fields": list(pairs[name]),
        "cap_limited_cases": (sum(bool(r["cap_limited"]) for r in rows)
                              if all("cap_limited" in r for r in rows) else None)}
        for name,rows in adjustments.items()}
    changes = [{"method": NAMES[i], "model_only_delta_percentage_points": {k: (b[k]-a[k])*100 for k in KEYS},
                "difficulty_only_delta_percentage_points": {k: (c[k]-b[k])*100 for k in KEYS}}
               for i,(a,b,c) in enumerate(zip(old["pooled"],parent["pooled"],adjusted["pooled"]))]
    improved = {k: sum(r["model_only_delta_percentage_points"][k] > 0 for r in changes) for k in KEYS}
    macro_changes = [{"method": NAMES[i], "old_macro_f1": a["f1"], "model_only_macro_f1": b["f1"],
                      "change_percentage_points": 100*(b["f1"]-a["f1"])}
                     for i,(a,b) in enumerate(zip(old["basic_macro"],parent["basic_macro"]))]
    case_fpr = {r["method"]: sum(d["case_count"]*d["events"]["normal_case_false_alarm_rate"][r["method"]]
                              for d in adjusted["datasets"])/sum(d["case_count"] for d in adjusted["datasets"])
                for r in adjusted["pooled"]}
    payload = {"date": "2026-10-14", "model_only": changes, "model_only_improved_route_counts": improved,
               "model_only_macro_f1_changes": macro_changes,
               "benchmark_adjustment_summary": adjustment_summary,
               "adjusted_normal_case_false_alarm_rate": case_fpr,
               "all_four_metrics_improved_all_routes": all(all(r["model_only_delta_percentage_points"][k]>0 for k in KEYS[:4]) for r in changes),
               "fresh_confirmation": confirmation, "github_uploaded": False, "auto_deployed": False,
               "limitation": "Adaptive synthetic research; shared generator/source pool; not external blind operational evaluation"}
    write(OUTPUT/"ASSESSMENT.json", payload)
    lines = ["# IF · K-means · LOF 개선 결과", "", "2026-10-14. 3개 알고리즘 × 전체/Adapter/앙상블 = 9개 전략. 기준은 직전에 보고한 v14의 9개 결과다.", "",
        "## 1. 같은 기존 데이터에서의 모델 효과", "",
        f"F1 개선 {improved['f1']}/9, Accuracy 개선 {improved['accuracy']}/9, Precision 개선 {improved['precision']}/9, Recall 개선 {improved['recall']}/9. 모든 지표가 모든 경로에서 좋아진 것은 아니다.", "",
        "| 전략 | 기존 F1 | 새 F1 | 모델 변화 | Precision 변화 | Recall 변화 | 오탐 변화 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for i,r in enumerate(changes):
        d = r["model_only_delta_percentage_points"]
        lines.append(f"| {NAMES[i]} | {old['pooled'][i]['f1']*100:.2f}% | {parent['pooled'][i]['f1']*100:.2f}% | {d['f1']:+.2f}%p | {d['precision']:+.2f}%p | {d['recall']:+.2f}%p | {d['normal_false_positive_rate']:+.2f}%p |")
    lines += ["", "기존 mild-v1의 같은 정상·이상 사례/시간창이다. 오탐 변화는 음수일수록 좋다.", "", metrics_table(parent["pooled"]), "",
        "## 2. 현재 완화 데이터에서의 최종 결과", "", metrics_table(adjusted["pooled"]), "",
        f"평가 판단 수 {adjusted['pooled'][0]['n']:,}분. 전부 정상으로 예측하는 Accuracy 기준은 {adjusted['pooled'][0]['always_normal_accuracy']*100:.2f}%. 행·사례·분 단위를 혼동하지 않는다.", "",
        "| 전략 | 새 모델 기존 데이터 F1 | 새 모델 완화 데이터 F1 | 난도 효과 |", "|---|---:|---:|---:|"]
    for i,r in enumerate(changes):
        lines.append(f"| {NAMES[i]} | {parent['pooled'][i]['f1']*100:.2f}% | {adjusted['pooled'][i]['f1']*100:.2f}% | {r['difficulty_only_delta_percentage_points']['f1']:+.2f}%p |")
    lines += ["", "난도 효과는 모델 개선으로 합산하지 않는다. 전체 편차 ×1.1, 기존 cap/정수 반올림 유지. 입력 중단 6/12/24→7/14/27분. 정상 대조군·Hard 바이트 동일, STATUS 전부 S, 하드 룰 적발 0건. 기존 활성 데이터는 복구 백업으로 보존했다.", "",
        "변형 기록에서 실제 주입량이 달라진 사례: "+", ".join(f"{TASKS[n]} {r['cases_with_quantity_changed']:,}/{r['cases']:,}" for n,r in adjustment_summary.items())+". 컬럼 관계는 시간 합이 같아도 개별 컬럼 변화가 있을 수 있어 위 개수는 시간 합 기준이다.", "",
        "## 3. 시나리오별 앙상블 F1", "", "| 시나리오 | IF | K-means | LOF |", "|---|---:|---:|---:|"]
    for d in adjusted["datasets"]:
        lines.append("| "+TASKS[d["dataset"]]+" | "+" | ".join(f"{d['metrics'][i]['f1']*100:.2f}%" for i in (2,5,8))+" |")
    lines += ["", "정상 사례에 한 번이라도 경보가 난 비율(시간창 오탐과 다른 단위): "+
              ", ".join(f"{a} 앙상블 {case_fpr[a+'_ensemble']*100:.2f}%" for a in ("if","kmeans","lof"))+". "
              "낮은 분 단위 오탐도 긴 정상 사례에서는 누적될 수 있다. 운영 경보로 바로 전환하기 전에 검증해야 한다."]
    lines += ["", "6개 기본 시나리오의 같은 비중 평균(분 수로 가중하지 않는 macro):", "", metrics_table(adjusted["basic_macro"]), "",
        "같은 기존 데이터에서의 macro-F1 개선도 별도로 비교한다. 종합 F1 상승과 다른 결과일 수 있다.", "",
        "| 전략 | 기존 macro-F1 | 모델만 변경 macro-F1 | 변화 |", "|---|---:|---:|---:|"]
    for r in macro_changes:
        lines.append(f"| {r['method']} | {r['old_macro_f1']*100:.2f}% | {r['model_only_macro_f1']*100:.2f}% | {r['change_percentage_points']:+.2f}%p |")
    lines += ["",
        "## 4. 실제 수정과 탈락 후보", "",
        "- IF 전체: 원래 희귀 조합 분기와 정상 학습 시간 변화 IF 결합. 시간 분기 max_samples=2048, trees=384, seed=42.",
        "- LOF 전체: 기존 조건부/트래픽 밀도 분기에 완료 분의 과거 상대 변화 분기 추가. 시간 분기 k=30, novelty=True, reference cap=2048, Euclidean, seed=42. 이웃 밀도는 분포 중복·참고 집단에 민감.",
        "- K-means 전체: 시간 군집 K=4/12와 whitening 후보가 탐색 제약을 넘지 못해 기존 군집 유지. 정상 보정과 결합 개선이 실제 변화다.",
        "- Adapter: 새 시간 전문가의 오탐 증가가 확인돼 보수적 원래 전문가와 사례 수 보정 선택. 신규 Adapter는 전체로 정확히 fallback.",
        "- 앙상블: 각 알고리즘의 실제 전체(v15)/Adapter(v17) 점수를 정상 P95−median 스케일로 결합. weight/mean-max/분위수/alpha는 개발 사례에서 선택. 세 알고리즘 OR가 아니다.",
        "- 새 피처: 초반 이미 관측된 최대 8개 완료 분의 median/MAD 대비 변화, 최근 15분 상대 변화/기울기, 양의 변화 비율, EWMA. prefix가 정상이라는 가정은 없고 초기 이상에는 둔감할 수 있다.",
        "- v16의 공유 시간 전문가/미세 수치 안정화, v17의 처리량 조건부 잔차 시간 분기/OAS 후보가 모두 최종 선택된 것은 아니다. 탈락 결과도 보존했다.", "",
        "## 5. 동결 후 새 확인 사례", "",
        "새 seed 19270042, 시나리오별 240개 paired 사례. 정상 보정·탐색·초기 확인과 case ID가 겹치지 않는다. 확인 결과로 재선택하지 않았다. 비교 기준은 보수적 v12 IF/K-means 및 v14 LOF이며, 위 모델 효과 표의 직전 v14 9개와 다르다.", "",
        "| 알고리즘 앙상블 | 기준 F1 | 후보 F1 | 후보 Precision | 후보 Recall | 후보 오탐 |", "|---|---:|---:|---:|---:|---:|"]
    for a in ("if","kmeans","lof"):
        v=confirmation["results"][a]; b,c=v["baseline"]["pooled"][2],v["candidate"]["pooled"][2]
        lines.append(f"| {a} | {b['f1']*100:.2f}% | {c['f1']*100:.2f}% | {c['precision']*100:.2f}% | {c['recall']*100:.2f}% | {c['normal_false_positive_rate']*100:.2f}% |")
    lines += ["", "탐색 때의 상대 오탐 제약이 새 확인 사례에서도 보장되지는 않았다. 합성 생성기/원본 pool 공유, 이미 관찰한 벤치마크의 적응적 연구라는 한계가 있다. 비지도 알고리즘도 개발 정답으로 선택했으므로 전체 연구가 완전 무라벨은 아니다.", "",
        "## 6. 논문·공식 근거", "",
        "- [Breunig 등, LOF, SIGMOD 2000](https://sigmodrecord.org/?download_id=6631&smd_process_download=1): 정상 이웃의 국소 밀도 차이를 LOF의 독립 성격으로 사용.",
        "- [LOF novelty 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html): 새 입력 점수화 novelty=True.",
        "- [Kim 등, AAAI 2022](https://arxiv.org/abs/2109.05257): point adjustment 없이 분 지표와 사건 지표 분리.",
        "- [Tatbul 등, NeurIPS 2018](https://arxiv.org/abs/1803.03639): 구간 이상 평가의 관점. 공식 전체 재현이 아니라 사건 Recall/최초 지연 병기.",
        "- [Schmidl 등, PVLDB 2022](https://timeeval.github.io/evaluation-paper/): 시나리오/알고리즘/오탐 비용을 분리한 비교.",
        "- [OAS 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.OAS.html): 공분산 수축 후보 시험. Gaussian 가정의 보장을 이 로그에 적용하지 않았다.",
        "- prefix/MAD·사례 수 수축·결합은 자체 엔지니어링이다. 논문의 완전한 모델 재현·새 확정 하드 룰이라고 주장하지 않는다.", "",
        "## 7. 확인 파일", "",
        "- 기존 결과 노트북: `code/model_research_v8/results.ipynb` (일반 막대, 분포+threshold, 실제 판정 예시, 의미 설명)",
        "- 최종 후보 3개: `reports/model_research_v18/handoff/*_v18_candidate.joblib` + manifest (크기/hash/threshold/의존성)",
        "- `audit.json`: 9개 혼동행렬 재집계, 정확한 fallback, 저장 전후 판정 동일성, 기존 모델 불변 검증",
        "- `ASSESSMENT.json`: 모든 지표의 모델 효과·난도 효과 원값",
        "- 입력은 Process 파생 연속 1분 grid+완료 행. Transaction 불필요. prefix/빈 창/실제 운영 이력 유지 필요.",
        "- GitHub/main/기존 운영 모델 자동 배포는 하지 않았다."]
    (OUTPUT/"RESULTS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("Final result report and separated model/difficulty assessment written",flush=True)


if __name__ == "__main__": build()
