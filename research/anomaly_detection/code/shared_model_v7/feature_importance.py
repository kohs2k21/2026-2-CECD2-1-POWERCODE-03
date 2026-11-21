"""Post-hoc normal-evidence ablation of the frozen v7 release.

This is NOT impurity importance, SHAP, a causal explanation, or retraining.
Only already-normalized numeric evidence is neutralized (zero = at/below
the frozen normal median). Related channels and the auxiliary statistics are
ablated jointly for the primary five-group analysis. Scores still pass through
the ORIGINAL estimator, whitening, normal references and calibration policy.
The policy may select a different observed stratum after an intervention:
therefore results explain the WHOLE released pipeline, not the bare estimator.

Whole cases, their paired controls, chronological order, labels, raw data and
model artifacts are preserved. The cached 200 pairs/task are a development
diagnostic, NOT all 5,000 pairs/task or a new blind evaluation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))

import joblib
import numpy as np
from shared_model_v6.features import BLOCKS, RAW, Evidence
from shared_model_v7.pmf_filter import Encoder as PMFEncoder
from shared_model_v7.causal_hold import _SCORES

TASKS = ('individual_delay', 'group_delay', 'time_burst', 'input_stop', 'gradual_degradation')
ALGORITHMS = ('if', 'kmeans', 'lof')
NAMES = {'if': 'Isolation Forest', 'kmeans': 'MiniBatch K-means', 'lof': 'LOF'}
TASK_NAMES = dict(zip(TASKS, ('개별 지연', '집단 지연', '시간대 폭주', '입력 중단', '점진적 악화')))
GROUP_NAMES = dict(zip(BLOCKS, ('현재 실행 지연', '지연 지속·수준 변화', '지연 증가 추세', '요청 수 증가', '입력 공백')))
FEATURE_NAMES = {
    'current_source_upper': '현재 소요시간의 정상 상한 이탈',
    'current_source_delay': '현재 소요시간의 양의 잔차',
    'seq_last_shift': '최근 실행의 소요시간 수준 변화',
    'seq_delay_cusum': '지연 잔차의 누적 증가',
    'rank_upper_memory': '최근 상단 이탈의 지속 신호',
    'rank_duration_cusum': '소요시간 순위의 누적 증가',
    'seq_delay_slope': '최근 지연 잔차의 증가 기울기',
    'seq_delay_ewma': '최근 지연의 지수이동평균',
    'traffic_tail_3m': '최근 3분 요청 수의 드문 정도',
    'traffic_tail_5m': '최근 5분 요청 수의 드문 정도',
    'current_prefix_burst': '초기 관측 대비 현재 요청 증가',
    'current_arrival_excess': '시간대 기준 대비 현재 요청 초과',
    'gap_expected_prefix': '초기 관측 빈도 대비 연속 공백',
    'traffic_zero_logprob': '연속 0건의 정상 기준 드문 정도',
    'gap_expected_recent': '최근 관측 빈도 대비 연속 공백',
    'aux_point': '추가 통계: 조건부 정상 소요시간 이웃',
    'aux_burst': '추가 통계: 요청 수 PMF 상태 신호',
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def confusion(y, score, threshold=1.):
    y = np.asarray(y, bool)
    pred = np.asarray(score) > threshold
    return np.array([np.sum(~y & ~pred), np.sum(~y & pred), np.sum(y & ~pred), np.sum(y & pred)], dtype=np.int64)


def metrics(c):
    tn, fp, fn, tp = np.asarray(c, dtype=float).T
    divide = lambda a, b: np.divide(a, b, out=np.zeros_like(a), where=b > 0)
    return {'accuracy': divide(tp + tn, tp + tn + fp + fn),
            'precision': divide(tp, tp + fp), 'recall': divide(tp, tp + fn),
            'f1': divide(2 * tp, 2 * tp + fp + fn), 'fpr': divide(fp, fp + tn)}


def per_case_confusion(case, y, score):
    ids, inverse = np.unique(case, return_inverse=True)
    pred = score > 1.
    category = np.where(y, np.where(pred, 3, 2), np.where(pred, 1, 0))
    counts = np.zeros((len(ids), 4), dtype=np.int64)
    np.add.at(counts, (inverse, category), 1)
    return ids, counts


def bootstrap_drop(base, changed, repetitions=400, seed=20261023):
    """Paired whole-case bootstrap; each case includes its paired normal stream."""
    rng = np.random.default_rng(seed)
    a = np.zeros((repetitions, 4)); b = a.copy()
    for before, after in zip(base, changed):
        assert before.shape == after.shape
        ix = rng.integers(0, len(before), size=(repetitions, len(before)))
        a += before[ix].sum(axis=1); b += after[ix].sum(axis=1)
    drop = 100 * (metrics(a)['f1'] - metrics(b)['f1'])
    return list(map(float, np.quantile(drop, [.025, .975])))


@contextmanager
def neutralized(raw_columns=(), auxiliary=()):
    """Patch only inference-time evidence, never frames, fitted objects or files.

    CachedPrimary caches by frame identity, so its score cache MUST be cleared
    on every intervention. Normal-neighbour and PMF caches are unmodified;
    their cached arrays are never mutated by this wrapper.
    """
    unknown = set(raw_columns) - set(RAW)
    if unknown or set(auxiliary) - {'point', 'burst'}:
        raise ValueError('Unknown intervention')
    indexes = [RAW.index(c) for c in raw_columns]
    original_summary = Evidence.summarize
    original_auxiliary = PMFEncoder.auxiliary
    calls = {'summary': 0, 'auxiliary': 0}

    def summarize(encoder, x, view):
        calls['summary'] += 1
        changed = x.copy()
        changed[:, indexes] = 0.
        return original_summary(encoder, changed, view)

    def auxiliary_values(encoder, frame):
        calls['auxiliary'] += 1
        point, burst = original_auxiliary(encoder, frame)
        if 'point' in auxiliary:
            point = np.zeros_like(point)
        if 'burst' in auxiliary:
            burst = np.zeros_like(burst)
        return point, burst

    _SCORES.clear()
    try:
        with patch.object(Evidence, 'summarize', summarize), patch.object(PMFEncoder, 'auxiliary', auxiliary_values):
            yield calls
    finally:
        _SCORES.clear()


def interventions():
    groups = [{'name': key, 'kind': 'group', 'label': GROUP_NAMES[key], 'raw_columns': list(cols),
               'auxiliary': [key] if key in ('point', 'burst') else []} for key, cols in BLOCKS.items()]
    features = [{'name': c, 'kind': 'feature', 'label': FEATURE_NAMES[c], 'raw_columns': [c], 'auxiliary': []} for c in RAW]
    features += [{'name': 'aux_' + key, 'kind': 'feature', 'label': FEATURE_NAMES['aux_' + key],
                  'raw_columns': [], 'auxiliary': [key]} for key in ('point', 'burst')]
    return groups + features


def add_deltas(before, after):
    return {k + '_drop_pp': float(100 * (before[k] - after[k])) for k in ('accuracy', 'precision', 'recall', 'f1', 'fpr')}


def draw(report, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False,
                         'axes.spines.top': False, 'axes.spines.right': False, 'font.size': 11})
    colors = {'if': '#2563eb', 'kmeans': '#d97706', 'lof': '#059669'}
    group_rows = [r for r in report['pooled_importance'] if r['kind'] == 'group']

    def grouped(rows, title, filename):
        fig, ax = plt.subplots(figsize=(12.5, 6.2))
        x = np.arange(len(BLOCKS))
        for j, algorithm in enumerate(ALGORITHMS):
            selected = [next(r for r in rows if r['algorithm'] == algorithm and r['name'] == key) for key in BLOCKS]
            value = np.array([r['f1_drop_pp'] for r in selected])
            bars = ax.bar(x + (j - 1) * .25, value, width=.23, color=colors[algorithm], label=NAMES[algorithm])
            for rect, v in zip(bars, value):
                ax.annotate(f'{v:.1f}', (rect.get_x() + rect.get_width()/2, v), xytext=(0, 4 if v >= 0 else -12),
                            textcoords='offset points', ha='center', fontsize=9)
        all_values = [r['f1_drop_pp'] for r in rows]
        low, high = min(0., min(all_values)), max(0., max(all_values))
        pad = max(4., (high - low) * .14)
        ax.set_ylim(low - pad, high + pad)
        ax.axhline(0, color='#475569', linewidth=.8)
        ax.set_xticks(x, list(GROUP_NAMES.values()))
        ax.set_ylabel('해당 피처 묶음을 정상 수준으로 바꾼 뒤 F1 감소량 (%p)')
        ax.set_title(title, pad=16)
        ax.legend(loc='upper center', bbox_to_anchor=(.5, -.10), ncol=3, frameon=False)
        ax.grid(axis='y', alpha=.18); ax.set_axisbelow(True)
        fig.text(.02, .015, '양수: 해당 신호가 탐지 성능에 기여 / 음수: 제거 후 F1 개선. 중요도 합이 100%인 비율이 아님.', fontsize=9, color='#475569')
        fig.tight_layout(rect=[0, .08, 1, 1]); fig.savefig(output / filename, dpi=170, bbox_inches='tight'); plt.close(fig)

    grouped(group_rows, '피처 묶음 중요도 · IF / K-means / LOF', 'group_importance.png')
    for task in TASKS:
        rows = [r for r in report['task_importance'] if r['task'] == task and r['kind'] == 'group']
        grouped(rows, TASK_NAMES[task] + ' · 피처 묶음 중요도', task + '_importance.png')

    for algorithm in ALGORITHMS:
        rows = sorted([r for r in report['pooled_importance'] if r['algorithm'] == algorithm and r['kind'] == 'feature'], key=lambda r: r['f1_drop_pp'])
        value = np.array([r['f1_drop_pp'] for r in rows]); y = np.arange(len(rows))
        fig, ax = plt.subplots(figsize=(12.5, 8.6))
        bars = ax.barh(y, value, color=[colors[algorithm] if v >= 0 else '#94a3b8' for v in value], height=.65)
        for rect, v in zip(bars, value):
            ax.annotate(f'{v:.2f}', (v, rect.get_y() + rect.get_height()/2), xytext=(4 if v >= 0 else -4, 0),
                        textcoords='offset points', ha='left' if v >= 0 else 'right', va='center', fontsize=9)
        low, high = min(0., value.min()), max(0., value.max()); pad = max(.8, (high-low)*.16)
        ax.set_xlim(low-pad, high+pad); ax.set_yticks(y, [r['label'] for r in rows]); ax.axvline(0, color='#475569', linewidth=.8)
        ax.set_xlabel('해당 피처를 정상 수준으로 바꾼 뒤 F1 감소량 (%p)')
        ax.set_title(NAMES[algorithm] + ' · 개별 피처 중요도', pad=15)
        ax.grid(axis='x', alpha=.18); ax.set_axisbelow(True)
        fig.text(.02, .015, '상관된 다른 피처가 정보를 대신할 수 있음. 0에 가깝다고 불필요한 피처라는 의미는 아님.', fontsize=9, color='#475569')
        fig.tight_layout(rect=[0, .04, 1, 1]); fig.savefig(output / (algorithm + '_feature_importance.png'), dpi=170, bbox_inches='tight'); plt.close(fig)


def notebook(output):
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client import KernelManager
    md = nbformat.v4.new_markdown_cell; code = nbformat.v4.new_code_cell
    report = json.loads((output / 'importance.json').read_text(encoding='utf-8'))
    conclusions = []
    for algorithm in ALGORITHMS:
        top = sorted([r for r in report['pooled_importance'] if r['algorithm'] == algorithm and r['kind'] == 'feature'],
                     key=lambda r: r['f1_drop_pp'], reverse=True)[:3]
        conclusions.append('- **' + NAMES[algorithm] + '**: ' + '; '.join(r['label'] + f" {r['f1_drop_pp']:+.2f}%p" for r in top))
    conclusions += ['\n전체 수치는 분 판정을 합산하므로 시나리오별 길이와 정상·이상 비율의 영향을 받는다. 예를 들어 개별 지연의 현재 실행 신호는 전체 합산 기여가 작더라도 해당 시나리오에서 크게 작동할 수 있다.',
                   '\n점진적 악화에서도 단순 증가 기울기만 아니라 지연의 지속·수준 변화가 중요할 수 있다. 따라서 “LOF는 기울기를 학습해서 강하다”처럼 알고리즘의 원리로 단정하지 않는다.']
    cells = [
        md('# IF · K-means · LOF 피처 중요도\n\n학습된 최신 모델과 경보 기준을 그대로 둔 **사후 분석**이다. 장기간 모델은 포함하지 않는다.\n\n**방법:** 정상 기준으로 변환된 신호를 하나씩 정상 수준(0)으로 바꾸고, 최종 판정의 F1이 얼마나 감소하는지 측정한다. 양수가 크면 이 평가에서 의존도가 높았고, 음수면 해당 신호를 제거했을 때 F1이 개선되었다. 다시 학습한 성능이 아니며, 중요도 합이 100%인 비율도 아니다.'),
        code("from pathlib import Path\nimport json,pandas as pd\nfrom IPython.display import display,Image,Markdown\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'reports/shared_model_v7/feature_importance/importance.json').exists())\nOUT=ROOT/'reports/shared_model_v7/feature_importance'\nr=json.loads((OUT/'importance.json').read_text(encoding='utf-8'))\nname={'if':'Isolation Forest','kmeans':'MiniBatch K-means','lof':'LOF'}\ndisplay(pd.DataFrame(r['sample']))\ndisplay(pd.DataFrame(r['baseline']).assign(모델=lambda d:d.algorithm.map(name))[['모델','task','accuracy','precision','recall','f1','fpr']].round(4))"),
        md('## 1. 먼저 피처 묶음으로 확인\n\n현재 지연·지연 지속·증가 추세·요청 증가·입력 공백의 다섯 묶음이다. 관련 피처를 함께 정상 수준으로 바꾸며, 현재 지연 묶음에는 조건부 정상 이웃 통계, 요청 증가 묶음에는 PMF 상태 통계도 포함한다.\n\nIF는 최종 학습 코어 입력이 1개 종합 신호이므로 내부 입력 중요도를 뽑으면 해석이 어렵다. 여기서는 **압축 전 신호 → 학습 모델 → 추가 정상 통계 → 경보 보정**을 포함한 전체 파이프라인을 설명한다.'),
        code("display(Image(filename=str(OUT/'group_importance.png')))\ng=pd.DataFrame([x for x in r['pooled_importance'] if x['kind']=='group'])\ndisplay(g[['algorithm','label','f1_drop_pp','recall_drop_pp','f1_drop_ci95_pp']].round(3))"),
        md('### 이번 표본의 핵심 결과\n\n' + '\n'.join(conclusions)),
        md('## 2. 개별 피처는 무엇을 의미하나\n\n15개 원천 수치 신호와 2개 추가 통계 신호를 각각 바꾼 결과다. 상관된 다른 피처가 정보를 대신할 수 있으므로, 개별 중요도가 작아도 전체 묶음 중요도는 클 수 있다. **Adapter·Process ID는 정상 통계와 경보 기준을 고르는 키이므로 숫자 피처와 같은 방법으로 섞지 않았다.**'),
    ]
    for a in ALGORITHMS:
        cells += [md('### ' + NAMES[a]), code("display(Image(filename=str(OUT/" + repr(a + '_feature_importance.png') + ")))\nf=pd.DataFrame([x for x in r['pooled_importance'] if x['kind']=='feature' and x['algorithm']==" + repr(a) + "])\ndisplay(f[['name','label','f1_drop_pp','recall_drop_pp']].sort_values('f1_drop_pp',ascending=False).round(3))")]
    cells += [md('## 3. 시나리오별로 보면\n\n아래 그래프는 모델이 해당 시나리오에서 **어느 정보를 활용했는지** 보여준다. 특정 모델이 다른 모델보다 성능이 좋은지 비교하는 그래프는 아니다. 그 비교는 기존 성능표로 확인해야 한다.')]
    for t in TASKS:
        cells += [md('### ' + TASK_NAMES[t]), code("display(Image(filename=str(OUT/" + repr(t + '_importance.png') + "))) ")]
    cells += [md('## 4. 평가 범위와 해석 주의\n\n- 시나리오별 200개 전체 이상 사례와 짝 정상 200개를 포함한 기존 개발 진단 표본이다. 5,000쌍 전수 평가 성능표와 구분한다.\n- 원본 로그, 정답, 모델, 경보선, 기준 통계를 변경하지 않았다. 원래 점수는 릴리스의 동일 판정 점수와 대조했다.\n- 막대는 정상 수준 치환에 따른 **F1 감소량(%p)**이다. 모델 고유의 impurity importance나 SHAP 값이 아니다.\n- 95% 구간은 정상·이상 쌍을 유지한 사례 단위 bootstrap 400회 결과다. 학습 불확실성이나 실제 운영 성능을 보장하지 않는다.\n- 정상 수준 치환으로 피처 조합이 실제 로그와 달라질 수 있고, 관측 신호에 따른 경보 기준 선택도 바뀔 수 있다. 따라서 인과적 장애 원인 또는 순수 알고리즘의 기여로 해석하지 않는다.\n- 개발 벤치마크를 반복 사용한 결과다. 이 그림을 보고 피처를 삭제·재학습하려면 별도 미사용 데이터에서 확인해야 한다.\n- 입력 중단은 로그가 없는 분도 포함한 타이머 감시가 필요하다. 완료 로그 한 건에 대한 즉시 판정과 별개의 문제다.')]
    nb = nbformat.v4.new_notebook(cells=cells, metadata={'kernelspec': {'display_name': 'Python (analysis venv)', 'language': 'python', 'name': 'python3'}})
    km = KernelManager(kernel_name='python3'); km.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}']
    NotebookClient(nb, timeout=120, resources={'metadata': {'path': str(ROOT)}}, km=km).execute()
    nbformat.write(nb, ROOT / 'code/shared_model_v7/feature_importance.ipynb')


def run(output, bootstrap=400):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    bank_path = ROOT / 'reports/shared_model_v6/probe_bank.joblib'
    release = ROOT / 'reports/shared_model_v7/release'
    manifest = json.loads((release / 'handoff/manifest.json').read_text(encoding='utf-8'))
    model_paths = {a: release / 'handoff' / (a + '_shared.joblib') for a in ALGORITHMS}
    watched = {**{str(p.relative_to(ROOT)): digest(p) for p in model_paths.values()},
               str(bank_path.relative_to(ROOT)): digest(bank_path)}
    for record in manifest['models']:
        assert digest(model_paths[record['algorithm']]) == record['sha256']
    bank = joblib.load(bank_path)
    report = {'method': 'normal_scaled_evidence_ablation_frozen_entire_pipeline', 'seed': 20261023,
              'external_blind_test': False, 'retrained': False, 'threshold_changed': False,
              'sample': [], 'baseline': [], 'task_importance': [], 'pooled_importance': [],
              'interventions': interventions(), 'source_sha256': watched, 'bootstrap_repetitions': bootstrap,
              'confidence_interval_unit': 'paired whole-case bootstrap, stratified by scenario',
              'interpretation': 'F1 decrease in percentage points; negative means improvement after ablation; not additive, causal, SHAP or impurity importance'}
    for task in TASKS:
        d = bank[task]
        report['sample'].append({'task': task, '이상 사례': int(len(np.unique(d['case_index']))),
                                 '짝 정상 사례': int(len(np.unique(d['case_index']))), '분 판정': len(d['y']),
                                 '이상 분': int(d['y'].sum()), '정상 분': int((~d['y']).sum())})
    for ai, algorithm in enumerate(ALGORITHMS):
        model = joblib.load(model_paths[algorithm]).detector
        assert float(model.threshold) == 1.
        assert type(model.encoder).__module__ == 'shared_model_v6.features'
        assert isinstance(model.reference.parent.encoder, PMFEncoder)
        base = {}; base_cases = {}; source_scores = {}
        start = time.perf_counter()
        for task in TASKS:
            d = bank[task]; f = d['frame']; _SCORES.clear()
            score = model.score(f)
            with np.load(release / (task + '_scores.npz')) as saved:
                keep = saved['case_index'] % 25 == 0
                np.testing.assert_array_equal(d['y'], saved['y'][keep])
                np.testing.assert_array_equal(d['case_index'], saved['case_index'][keep])
                np.testing.assert_allclose(score, saved['scores'][keep, ai], rtol=1e-10, atol=1e-10)
            source_scores[task] = score.copy(); base[task] = confusion(d['y'], score)
            _, base_cases[task] = per_case_confusion(d['case_index'], d['y'], score)
            report['baseline'].append({'algorithm': algorithm, 'task': task, **{k: float(v) for k, v in metrics(base[task]).items()}})
        pooled_before = metrics(sum(base.values()))
        print('BASELINE verified', algorithm, 'F1', round(100*pooled_before['f1'], 3), 'seconds', round(time.perf_counter()-start, 2), flush=True)
        for spec in report['interventions']:
            changed = {}; changed_cases = {}; task_rows = []
            for task in TASKS:
                d = bank[task]
                with neutralized(spec['raw_columns'], spec['auxiliary']) as calls:
                    score = model.score(d['frame'])
                assert calls['summary'] > 0 and calls['auxiliary'] > 0
                if not np.isfinite(score).all():
                    raise ValueError('Nonfinite ablated score')
                c = confusion(d['y'], score); changed[task] = c
                _, changed_cases[task] = per_case_confusion(d['case_index'], d['y'], score)
                row = {'algorithm': algorithm, 'task': task, **spec,
                       **add_deltas(metrics(base[task]), metrics(c)), 'ablated_counts': c.tolist(),
                       'changed_predictions': int(np.sum((score > 1) != (source_scores[task] > 1)))}
                report['task_importance'].append(row); task_rows.append(row)
            row = {'algorithm': algorithm, **spec, **add_deltas(pooled_before, metrics(sum(changed.values()))),
                   'baseline_counts': sum(base.values()).tolist(), 'ablated_counts': sum(changed.values()).tolist(),
                   'changed_predictions': sum(r['changed_predictions'] for r in task_rows),
                   'f1_drop_ci95_pp': bootstrap_drop([base_cases[t] for t in TASKS], [changed_cases[t] for t in TASKS], bootstrap)}
            report['pooled_importance'].append(row)
            write_json(output / 'progress.json', report)
            print('IMPORTANCE', algorithm, spec['name'], 'F1 drop pp', round(row['f1_drop_pp'], 4), flush=True)
        # Verify restoration after temporary patches and caches are reset.
        for task in TASKS:
            _SCORES.clear()
            np.testing.assert_allclose(model.score(bank[task]['frame']), source_scores[task], rtol=0, atol=1e-12)
        report.setdefault('restored_baselines_verified', []).append(algorithm)
    for path, before in watched.items():
        assert digest(ROOT / path) == before
    report['source_models_and_probe_unchanged'] = True
    report['release_baseline_scores_verified'] = True
    report['total_sample_decisions'] = sum(len(bank[t]['y']) for t in TASKS)
    write_json(output / 'importance.json', report)
    draw(report, output); notebook(output)
    write_json(output / 'completion.json', {'completed': True, 'algorithms': list(ALGORITHMS),
                'group_interventions': 5, 'single_interventions': 17, 'pngs': sorted(p.name for p in output.glob('*.png')),
                'notebook': 'code/shared_model_v7/feature_importance.ipynb',
                'all_original_models_and_probe_unchanged': True, 'external_blind_test': False})
    print('COMPLETED', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/shared_model_v7/feature_importance')
    parser.add_argument('--bootstrap', type=int, default=400)
    parser.add_argument('--render-only', action='store_true', help='Rebuild charts/notebook from existing importance.json; no model scoring')
    args = parser.parse_args()
    if args.render_only:
        report = json.loads((args.output / 'importance.json').read_text(encoding='utf-8'))
        draw(report, args.output); notebook(args.output)
        print('RENDERED', args.output, flush=True)
    else:
        run(args.output, args.bootstrap)
