"""Generate a small, executable comparison notebook from frozen JSON results."""
from pathlib import Path
import sys
import nbformat as nb

cells = [
    nb.v4.new_markdown_cell("# 벤치마크 난이도 소폭 완화 비교\n\n**기존 `data/evaluation`을 난이도 완화 데이터로 변경했습니다.** 별도의 활성 평가셋은 없습니다. 변경 전 데이터는 복구·비교 근거로 `reports/benchmark_before_mild_20261014`에 보관했습니다. 모델은 v12 그대로이고 STATUS=S, 기존 하드 룰 통과 조건, 정상 대조군과 사례 수를 유지했습니다."),
    nb.v4.new_code_cell("from pathlib import Path\nimport sys, json\nROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p/'code/benchmark_difficulty').exists())\nsys.path.insert(0, str(ROOT/'code'))\nfrom benchmark_difficulty import report\nfrom IPython.display import display\nreport.setup()"),
    nb.v4.new_markdown_cell("## 1. 조정 범위\n\n- 개별/집단 지연, 컬럼 관계: 정상 값 대비 주입 편차를 1.2배로 강화.\n- 폭주: 추가 실행 수를 1.2배, 정수 올림.\n- 중단: 5/10/20분에서 6/12/24분.\n- 점진적 악화: 증가량을 1.2배, 같은 진행 모양 유지.\n- 모든 소요시간은 원래 고정 하드 룰 상한을 유지.\n- **혼합 Hard는 변경하지 않음.**\n\n20%는 신호 변화량이며 난이도 또는 F1의 변화율이 아닙니다. 원본 v12 평가와 동일하게 피처 float32 round-trip을 사용합니다. 직접 float64 평가 시 경계 근처에서 일부 판정이 달라지는 진단 결과는 별도로 보존했습니다."),
    nb.v4.new_code_cell("protocol = json.loads((ROOT/'reports/benchmark_mild_v1/protocol.json').read_text(encoding='utf-8'))\ndisplay(protocol)"),
    nb.v4.new_markdown_cell("## 2. 하드 룰과 원본 보존 검증\n\n최종 CSV를 실제 파이프라인 규칙으로 재검사합니다. 원본 데이터와 모델은 해시로 보존 여부를 확인합니다."),
    nb.v4.new_code_cell("validation = json.loads((ROOT/'data/evaluation/validation.json').read_text(encoding='utf-8'))\nassert validation['passed']\ndisplay({'passed': validation['passed'], 'anomaly_cases': validation['anomaly_cases'], 'normal_cases': validation['normal_cases'], 'datasets': list(validation['datasets'])})\naudit = json.loads((ROOT/'reports/benchmark_mild_v1/audit.json').read_text(encoding='utf-8'))\ndisplay(audit)\nactivation = json.loads((ROOT/'reports/benchmark_mild_v1/activation.json').read_text(encoding='utf-8'))\nassert activation['state'] == 'active'\ndisplay({k:activation[k] for k in ('active_path','backup_path','verified','csv_files','model_files_unchanged')})"),
    nb.v4.new_markdown_cell("## 3. 전체 성능\n\nAccuracy는 정상 비중에 영향을 받습니다. Precision·Recall·F1·정상 오탐률을 함께 확인하세요. Adapter/앙상블은 전용 모델이 없는 집단에서 전체 모델로 fallback합니다."),
    nb.v4.new_code_cell("table = report.overall()\ndisplay(table[['모델', '원본 accuracy (%)', '완화 accuracy (%)', '원본 precision (%)', '완화 precision (%)', '원본 recall (%)', '완화 recall (%)']])\ndisplay(table[['모델', '원본 f1 (%)', '완화 f1 (%)', 'f1 변화 (%p)', '원본 normal_false_positive_rate (%)', '완화 normal_false_positive_rate (%)']])"),
    nb.v4.new_code_cell("report.bars();"),
    nb.v4.new_markdown_cell("## 4. task별 성능\n\n동일 모델이라도 task별 민감도가 다릅니다. 강도를 키워도 현재 피처와 threshold가 구분하지 못하면 성능이 그대로이거나 내려갈 수 있습니다. 모든 task를 유지하고 불리한 결과도 표시합니다."),
    nb.v4.new_code_cell("display(report.task_table())\nreport.task_bars();"),
    nb.v4.new_markdown_cell("## 5. 평가 분모 변화\n\n사례 수는 그대로지만 중단 지속 시간이 늘어 이상 구간 수가 달라집니다. 소요시간 변화로 완료 시각이 넘어가면 판정 구간도 늘 수 있습니다. 전체 F1과 동일 task 비중의 macro 결과를 구분합니다."),
    nb.v4.new_code_cell("display(report.denominators())\nold = report.read('model_research_v12')\nnew = report.read('benchmark_mild_v1')\nimport pandas as pd\ndisplay(pd.DataFrame([{'모델': report.LABELS[i], '원본 task-macro F1 (%)': a['f1']*100, '완화 task-macro F1 (%)': b['f1']*100} for i,(a,b) in enumerate(zip(old['basic_macro'],new['basic_macro']))]).round(2))"),
    nb.v4.new_markdown_cell("## 6. 사건 발견과 탐지 지연\n\n사건 Recall은 장애 사건 중 한 번이라도 경보를 낸 비율입니다. 모든 이상 구간을 탐지했다는 뜻이 아니며, 이 값을 구간 F1로 대체하지 않습니다. 탐지 지연은 **탐지한 사건만**의 중앙값입니다."),
    nb.v4.new_code_cell("rows=[]\nfor a,b in zip(old['datasets'],new['datasets']):\n    previous={(r['scenario'],r['method']):r for r in a['events']['per_type']}\n    for r in b['events']['per_type']:\n        if r['method'] in ('if_global','kmeans_ensemble'):\n            p=previous[(r['scenario'],r['method'])]\n            rows.append({'데이터셋': report.TASKS[b['dataset']], '장애 유형': r['scenario'], '모델': r['method'], '원본 사건 Recall (%)':p['event_recall']*100,'완화 사건 Recall (%)':r['event_recall']*100,'원본 탐지 지연 (초)':p['median_delay_seconds'],'완화 탐지 지연 (초)':r['median_delay_seconds']})\ndisplay(pd.DataFrame(rows).round(2))"),
    nb.v4.new_markdown_cell("## 결론을 읽는 기준\n\n이 실험은 모델 성능 개선 주장이 아닙니다. **원본과 완화 벤치마크를 함께 보고** 신호 강도에 따른 민감도와 남은 실패 유형을 확인합니다. 같은 원본/생성기를 사용하며, 난이도 설정도 원본 결과를 본 뒤 정했으므로 외부 blind 평가가 아닙니다. 실제 운영 신뢰도는 별도 데이터가 필요합니다.")
]
notebook = nb.v4.new_notebook(cells=cells, metadata={"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}})
notebook.cells[1].source = "%matplotlib inline\n" + notebook.cells[1].source
target = Path(__file__).with_name("comparison.ipynb")
if "--execute" in sys.argv:
    # Same existing-workspace execution method as model_evaluation/execute_notebook.py.
    from nbclient import NotebookClient
    from jupyter_client import KernelManager
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(notebook, km=manager, timeout=600,
        resources={"metadata": {"path": str(target.parent)}},
        on_cell_start=lambda cell, cell_index: print(f"Comparison cell {cell_index}: {cell.cell_type}", flush=True))
    client.execute(cleanup_kc=True)
nb.validate(notebook)
nb.write(notebook, target)
