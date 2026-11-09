"""Separate F1/recall gains from precision/false-alarm regressions."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from model_research_v13.data import read, write
from model_research_v14.run import OUTPUT


def assess():
    folder = ROOT / read(OUTPUT / "evaluation_index.json")["current"]
    result = read(folder / "summary.json")
    baseline = read(ROOT / "reports/benchmark_mild_v1/summary.json")
    assert baseline["protocol"]["benchmark_manifest_sha256"] == result["protocol"]["benchmark_manifest_sha256"]
    comparisons = []
    for new, old in zip(result["pooled"][:6], baseline["pooled"]):
        changes = {key: (new[key]-old[key])*100 for key in (
            "accuracy", "precision", "recall", "f1", "normal_false_positive_rate")}
        comparisons.append({"method": new["method"], "change_percentage_points": changes,
            "all_four_metrics_improved": all(changes[key] > 0 for key in ("accuracy", "precision", "recall", "f1")),
            "false_alarm_rate_not_worse": changes["normal_false_positive_rate"] <= 0})
    assessment = {"comparisons_on_same_benchmark": comparisons,
        "recommendation": {
            "if_v14": "Research recall/F1-oriented candidate only. Precision, accuracy and false-alarm rate worsened; keep v12 for conservative alerts until independent operating validation.",
            "kmeans_v14": "Exactly the preserved v12 model. New training/feature candidates did not beat it on development criteria.",
            "lof_v14": "New density-based comparator, strong time-burst results, weak delay/drift. Development alarm budget failed; not a wholesale replacement."},
        "automatic_model_promotion": False, "github_upload_performed": False,
        "benchmark_adjustment": "Pending clarification; current benchmark unchanged",
        "existing_notebook_overwritten": False,
        "new_executed_notebook": "code/model_research_v14/result.ipynb",
        "case_partition_caution": read(OUTPUT / "case_partition_audit.json"),
        "caution": "Adaptive synthetic pilot with paired observed-search case overlap in normal calibration; not external blind performance; no claim that all four metrics improved."}
    write(OUTPUT / "ASSESSMENT.json", assessment)
    lines = ["# LOF 추가 및 추가 개선 실험 결과", "", "같은 현재 벤치마크에서 2,167,766개 Process 분 단위 의사결정을 평가했다.", "",
             "| 전략 | Accuracy | Precision | Recall | F1 | 정상 오탐 |", "|---|---:|---:|---:|---:|---:|"]
    for r in result["pooled"]:
        lines.append("| " + r["method"] + " | " + " | ".join(f"{r[k]*100:.2f}%" for k in (
            "accuracy", "precision", "recall", "f1", "normal_false_positive_rate")) + " |")
    lines += ["", "## 판단", "",
        "IF 전체: F1 34.43%→41.05%, Recall 22.13%→30.41%. 하지만 Precision 77.50%→63.13%, 정상 오탐 1.33%→3.68%. 안정적인 전면 개선이라고 할 수 없다.",
        "IF Adapter/앙상블도 Recall/F1이 상승하지만 Precision·오탐에 비용이 있다. 기존 운영 모델을 교체하지 않았다.",
        "K-means는 개선 후보가 기존 모델을 넘지 못해 v12를 그대로 보존했다. 최종 분별 예측도 기존과 같음을 검증했다.",
        "LOF는 시간대 폭주 앙상블 F1 50.34%로 IF 44.33%, K-means 39.78%보다 높다. 반면 개별 지연·점진적 악화·Hard에는 약하다.",
        "LOF 앙상블만 잡은 이상 분 11,197개가 있지만 LOF만 낸 오탐도 40,030개다. 단순 OR 결합을 바로 권하지 않는다.", "",
        "## 이번에 다시 검토한 내용", "",
        "- LOF novelty=True, k=30/50/75, 중복 제거·거리·reference cap, 정상 참고 벡터의 유효 다양성.",
        "- 원본 train/calibration/threshold 템플릿을 섞지 않은 더 다양한 정상 스트림 재생.",
        "- 고정 정상 median/MAD에 대한 누적 편차, EWMA, 지속 비율. IF/K-means에서는 검증 개선에 연결되지 않아 채택하지 않음.",
        "- 별도 정상 사례로 단계/관측 여부별 threshold 보정. IF 선택 후보는 나무 재학습이 아니라 보정 변경이며 확인 오탐 상승도 공개.",
        "- 분할 감사: 보정 정상 270개 사례는 정상 탐색 사례와는 분리됐지만 paired 이상 탐색 사례와 ID를 공유했다. 메타데이터를 정정하고 원본 선택 기록을 보존했다. 최종/확인 사례 보정 사용은 없으나 탐색 수치는 낙관적일 수 있다.",
        "- IF 표본 수/회전, K-means K/거리 형태. 회전·군집 거리 변경은 이전에도 고려한 항목.",
        "- 분 지표·시나리오 macro·사건 Recall·탐지 지연·정상 사례 오탐·모델 간 고유 탐지를 분리. point adjustment 미사용.", "",
        "## 근거 자료", "",
        "- [Breunig 등, LOF, SIGMOD 2000](https://sigmodrecord.org/?download_id=6631&smd_process_download=1): 이웃 밀도 비교. 중복 제거/cap은 자체 엔지니어링 선택.",
        "- [Kim 등, AAAI 2022](https://arxiv.org/abs/2109.05257): PA 과대평가 경계. 분 단위 정답은 그대로 보존.",
        "- [Tatbul 등, NeurIPS 2018](https://arxiv.org/abs/1803.03639): 점/구간 평가 차이. 사건 탐지율과 지연을 함께 제시하되 range 지표 공식 전체 재현은 아님.",
        "- [NIST CUSUM](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm): 작은 지속 변화 누적 참고. 정식 제어 한계 보장 CUSUM 구현 아님.",
        "- [Schmidl 등, PVLDB 2022](https://timeeval.github.io/evaluation-paper/): 알고리즘/이상 종류별 차이. 시나리오별 비교와 비용을 구분.", "",
        "## 보류 사항", "",
        "벤치마크 난도 조정 방향/폭과 기존 results.ipynb 대상 경로는 사용자 확인 대기다. 현재 데이터와 기존 노트북을 덮어쓰지 않았다.",
        "새 실행 결과는 code/model_research_v14/result.ipynb, portable 연구 후보는 reports/model_research_v14/handoff에 있다.",
        "이번 작업에서 GitHub 업로드나 운영 배포를 수행하지 않았다."]
    (OUTPUT / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Tradeoffs and no-auto-promotion assessment saved", flush=True)


if __name__ == "__main__": assess()
