from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import gzip
import json
import time
from jsonschema import ValidationError
from .artifacts import DESIGN, PROJECT, RELEASE, InputError, XAI, canonical, digest, read, seal, sha, write
from .engine import rejection, render
from .saved_esb import SavedESB


def run(args):
    out = Path(args.output).resolve() if args.output else PROJECT / 'data/eda/xai_grounded_baseline' / datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%dT%H%M%S_%f%z')
    if not out.is_relative_to(PROJECT): raise InputError('output_outside_project')
    out.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    try:
        policy = read(args.severity_policy) if args.severity_policy else None
        source = SavedESB(args.run, release=args.release, capture=args.capture)
        selected = source.select(None if args.all else args.limit)
        source.prepare(selected)
    except (InputError, ValidationError, ValueError, KeyError, OSError, ImportError) as exc:
        reason = str(exc).split('\n')[0][:400]
        write(out/'startup_rejection.json', rejection(None, reason))
        summary = dict(status='input_rejected', reason=reason, explained=0, detector_decision_unchanged=True,
                       LLM_calls=0, SFT_steps=0, detector_score_calls=0, fit_calls=0)
        write(out/'summary.json', summary)
        seal(out)
        print(json.dumps(dict(output=str(out), **summary), ensure_ascii=False), flush=True)
        return summary
    counters = {k: Counter() for k in ('decision', 'severity', 'fact_kind', 'fact_state', 'component_patterns', 'rejection_reason')}
    accepted, rejected, facts_total = 0, 0, 0
    input_hash = digest(dict(run=str(source.run), manifest_sha256=sha(source.run / 'manifest.json'), selection=[r['entity_id'] for r in selected]))
    with gzip.open(out/'evidence_packets.jsonl.gz', 'xt') as packets, gzip.open(out/'explanations.jsonl.gz', 'xt') as results, gzip.open(out/'packet_audit.jsonl.gz', 'xt') as audit, gzip.open(out/'rejections.jsonl.gz', 'xt') as rejections, gzip.open(out/'rejection_details.jsonl.gz', 'xt') as rejection_details, (out/'explanations.txt').open('x') as text:
        text.write('동결된 저장 탐지 결과의 기본 설명\nLLM 생성·학습·재탐지 없이 코드로 생성한 설명입니다.\n')
        text.write('주의사항: 심각도 정책 미제공 시 심각도 미정이며, 규칙별 정책을 임의로 배정하지 않습니다.\n\n')
        for index, saved in enumerate(selected):
            packet = None
            try:
                packet, provenance = source.packet(saved, policy)
                result = render(packet)
                packets.write(canonical(packet) + '\n')
                results.write(canonical(result) + '\n')
                audit.write(canonical(provenance) + '\n')
                accepted += 1; facts_total += len(packet['facts'])
                counters['decision'][result['decision']['state']] += 1
                counters['severity'][result['severity']['label']] += 1
                counters['component_patterns']['/'.join(saved[k] for k in ('numeric_SR', 'logical_SR', 'ML'))] += 1
                for fact in packet['facts']:
                    counters['fact_kind'][fact['kind']] += 1; counters['fact_state'][fact['state']] += 1
                text.write(f"[{result['packet_id']}] 판정={result['decision']['state']} / 심각도={result['severity']['label']}\n")
                text.write('위치: 거래 원천 T 행 ' + saved['source_row_id'] + '\n')
                for sentence in result['sentences']:
                    text.write('- [' + ', '.join(sentence['source_refs']) + '] ' + sentence['text'] + '\n')
                text.write('실제 장애 원인: ' + result['cause_status'] + '\n\n')
            except (InputError, ValidationError, KeyError, ValueError) as exc:
                rejected += 1
                reason = str(exc).split('\n')[0][:400]
                counters['rejection_reason'][reason] += 1
                rejected_packet = rejection(packet['packet_id'] if packet else None, reason)
                rejections.write(canonical(rejected_packet) + '\n')
                rejection_details.write(canonical(dict(**rejected_packet, saved_decision_sha256=digest(saved), saved_state=saved['combined'], reason=reason)) + '\n')
            if (index + 1) % 20 == 0:
                print(json.dumps(dict(processed=index + 1, selected=len(selected), accepted=accepted, rejected=rejected)), flush=True)
    source.assert_unchanged()
    code_pins = [dict(path=str(p.relative_to(XAI)), sha256=sha(p)) for p in sorted((XAI/'xai_runtime').glob('*.py'))]
    code_pins += [dict(path='run.py', sha256=sha(XAI/'run.py'))]
    settings = dict(contract='xai-saved-esb-settings/1.0.0', source_run=str(source.run), source_run_manifest_sha256=sha(source.run/'manifest.json'),
                    capture=str(source.capture_path), capture_sha256=sha(source.capture_path), source_class=source.capture_manifest['data_class'],
                    release=str(source.release), release_model_sha256=sha(source.release/'model.json'), release_bundle_manifest_sha256=sha(source.release/'bundle_manifest.json'),
                    source_entities=len(source.decisions), selected_entities=len(selected), selection='all' if args.all or len(selected)==len(source.decisions) else 'deterministic round-robin over saved component-state groups',
                    source_selection_sha256=input_hash, design='xai-model/1.1.0', design_manifest_sha256=sha(DESIGN/'artifact_manifest.json'), code=code_pins,
                    severity_policy=policy, severity_policy_status='not_configured' if policy is None else 'approved' if policy.get('approved_for_operations') else 'draft',
                    no_policy_behavior='preserve warning and explain facts; explicitly unrated pending rule severity policy; missing entries in a supplied policy are configuration errors',
                    HR_scope='not part of frozen 1.1.0 primary profile; no HR facts invented',
                    native_feature_values='selected exact Decimal50 AST values re-read from admitted sources, compared with pinned saved NPZ values',
                    ML_values='saved native margins/scores; LOF threshold metadata from SHA-verified trusted local bundle, no scoring',
                    preprocessor_pin_scope='same model bundle contains preprocessing; hash covers entire bundle',
                    LLM_calls=0, SFT_steps=0, detector_score_calls=0, fit_calls=0, frontend_created=False)
    write(out/'execution_settings.json', settings)
    summary = dict(status='completed' if rejected == 0 else 'completed_with_input_rejections', selected=len(selected), explained=accepted,
                   input_rejected=rejected, facts=facts_total, counts={k:dict(v) for k,v in counters.items()}, source_total_entities=len(source.decisions),
                   processing_seconds=time.perf_counter()-start, evaluation_scope='saved-result explanation correctness, not new detection performance',
                   original_model_and_run_preserved=True, LLM_calls=0, SFT_steps=0, detector_score_calls=0, fit_calls=0)
    write(out/'summary.json', summary)
    report = ['# XAI 단계 1~2 실행 결과', '', f"- 원천 저장 거래: {len(source.decisions):,}건", f"- 설명 대상: {len(selected):,}건", f"- 설명 생성: {accepted:,}건", f"- 입력 거부: {rejected:,}건", f"- 근거 수: {facts_total:,}개", '- 기존 판정·모델·원본 보존 확인', '- LLM·SFT·모델 재탐지·fit 호출: 모두 0', '',
              '## 범위와 한계', '', '- 저장된 1.1.0의 수치 SR·논리 SR·ML만 설명하며, HR은 이 프로필에 없음.', '- 심각도 규칙 정책이 없으면 모든 경고를 명시적으로 심각도 미정으로 남김. 정책 미확정과 ML 자체의 등급 미정 사유는 packet_audit에서 구분.', '- 실제 장애·인과 원인과 ML 피처 기여도는 확정하지 않음.', '- 원천 관계·값·피처 정의·저장 판정을 검사한 기본 설명기이며 실제 XAI LoRA는 아직 없음.', '- 큰 거래의 근거 분할과 토큰 측정은 모델 호출 연결 단계에서 구현할 사항. 현재는 전체 근거를 보존.', '- 새 운영 서비스/API 연결, FlowBench 입력 어댑터, 운영 HR 저장 결과 어댑터는 이번 실행 범위 밖.', '', '## 집계', '', '```json', json.dumps(summary, ensure_ascii=False, indent=2), '```', '']
    (out/'report.md').write_text('\n'.join(report))
    seal(out)
    print(json.dumps(dict(output=str(out), **summary), ensure_ascii=False), flush=True)
    return summary


def main():
    p = argparse.ArgumentParser(description='Explain existing frozen ESB decisions without re-detection, fit or LLM.')
    p.add_argument('--run', required=True, help='Saved hybrid 1.1.0 ESB result directory')
    p.add_argument('--release', default=str(RELEASE))
    p.add_argument('--capture', help='Explicit relocated capture; must match stored SHA')
    p.add_argument('--output', help='New output directory; existing directories are never overwritten')
    p.add_argument('--limit', type=int, default=100)
    p.add_argument('--all', action='store_true', help='Explain all saved transactions, may produce large output')
    p.add_argument('--severity-policy', help='Explicit external rule-level policy JSON; no default grades')
    result = run(p.parse_args())
    if result['status'] != 'completed':
        raise SystemExit(2)
