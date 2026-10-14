"""Portable frozen-input research inspection and bounded model generation."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
from rule_policy import validate_rule, factual_review

PACKAGE = Path(__file__).resolve().parent.parent
ROOT = PACKAGE.parents[2]
EVALUATION = ROOT / 'research/evaluation/feature_rule_design_v0_2'


def read(path):
    return json.loads(Path(path).read_text())


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1048576), b''):
            h.update(data)
    return h.hexdigest()


def verify_package(weights=False):
    manifest = read(PACKAGE / 'model_manifest.json')
    for relative, expected in manifest['publication_assets'].items():
        path = ROOT / relative
        if sha(path) != expected:
            raise ValueError('Published artifact changed: ' + relative)
    if weights:
        for adapter in manifest['adapters'].values():
            if sha(ROOT / adapter['weights']) != adapter['sha256']:
                raise ValueError('Adapter weights changed')
            if sha(ROOT / adapter['config']) != adapter['config_sha256']:
                raise ValueError('Adapter configuration changed')
    return manifest


def load_case(kind, case):
    if kind not in {'feature', 'rule'} or not case or any(c in case for c in '/\\'):
        raise ValueError('Use a case ID from the published package')
    result = read(EVALUATION / (kind + '_inputs') / (case + '.json'))
    if result['id'] != case:
        raise ValueError('Case ID mismatch')
    return result


def inspect(weights=False):
    manifest = verify_package(weights)
    return {'package_version': manifest['package_version'], 'models': list(manifest['adapters']),
            'evaluation': manifest['evaluation'], 'training': manifest['training'],
            'feature_cases': sorted(p.stem for p in (EVALUATION / 'feature_inputs').glob('*.json')),
            'rule_cases': sorted(p.stem for p in (EVALUATION / 'rule_inputs').glob('*.json')),
            'weights_verified': weights, 'raw_source_measurement_performed': False,
            'IF_connected': False, 'server_connected': False, 'GPU_calls': 0}


def replay(case):
    verify_package()
    published = load_case('feature', case)
    comparison = read(EVALUATION / 'comparison_r02.json')['results']
    phases = {}
    for phase in ['v4', 'v5']:
        response = read(EVALUATION / 'feature_outputs' / phase / (case + '.json'))
        if digest(response['raw_output']) != response['raw_sha']:
            raise ValueError('Raw response changed')
        phases[phase] = {'raw_output': response['raw_output'], 'raw_sha': response['raw_sha'],
                         'archived_checks': response['checks'],
                         'archived_review': next(r for r in comparison[phase]['rows'] if r['id'] == case)}
    rule_checks = []
    for path in sorted((EVALUATION / 'rule_inputs').glob(case + '_*.json')):
        response = read(EVALUATION / 'rule_outputs' / path.name)
        rule_checks.append({'id': path.stem, 'raw_output': response['raw_output'],
                            'code_validation': validate_rule(read(path)['input'], json.loads(response['raw_output']))})
    return {'mode': 'published_snapshot_replay', 'published_input': published,
            'phases': phases, 'rule_checks': rule_checks, 'GPU_calls': 0,
            'archived_measurement_recalculated': False,
            'source_scope': 'pinned aggregate artifacts; private raw CSV excluded',
            'meaning_review_scope': 'archived assistant review, not independent business truth'}


def check_rule(case, response):
    verify_package()
    value = read(response) if isinstance(response, (str, Path)) else response
    if isinstance(value, dict) and 'raw_output' in value:
        value = json.loads(value['raw_output'])
    original = load_case('rule', case)['input']
    return {'code_validation': validate_rule(original, value),
            'limited_fact_review': factual_review(original, value),
            'meaning_review': 'required for every new response',
            'private_source_revalidation': False, 'GPU_calls': 0}


def generate(kind, case, output):
    """One frozen input per invocation. Imports GPU packages only on explicit generation."""
    output = Path(output)
    if output.exists():
        raise FileExistsError('Use a new output path')
    manifest = verify_package(weights=True)
    record = load_case(kind, case)
    base = os.environ.get('RULE_MODEL_BASE_PATH')
    if not base:
        raise ValueError('Set RULE_MODEL_BASE_PATH to the local pinned Qwen snapshot')
    base = Path(base).expanduser().resolve()
    for name, expected in manifest['base']['metadata_sha256'].items():
        if sha(base / name) != expected:
            raise ValueError('Base metadata mismatch: ' + name)
    import torch
    from transformers import AutoTokenizer, BitsAndBytesConfig, Qwen3_5ForConditionalGeneration
    from peft import PeftModel
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError('Expose exactly one assigned GPU; no automatic environment change')
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError('The pinned inference configuration requires bf16 support')
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=index,name,memory.used,memory.total,utilization.gpu',
                                   '--format=csv,noheader'], text=True)
    adapter = manifest['adapters']['feature_v5' if kind == 'feature' else 'rule_v2']
    tok = AutoTokenizer.from_pretrained(str(base), local_files_only=True)
    messages = [{'role': 'system', 'content': (PACKAGE / (kind + '_system.txt')).read_text().rstrip('\n')},
                {'role': 'user', 'content': json.dumps(record['input'], ensure_ascii=False, separators=(',', ':'))}]
    ids = tok(tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                     enable_thinking=False), add_special_tokens=False)['input_ids']
    if len(ids) > manifest['generation']['maximum_input_tokens']:
        raise ValueError('Input exceeds the explicit budget; no truncation')
    torch.manual_seed(42)
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                              bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        str(base), local_files_only=True, quantization_config=quant, dtype=torch.bfloat16,
        device_map={'': 0}, attn_implementation='sdpa', low_cpu_mem_usage=True)
    for name, parameter in model.named_parameters():
        parameter.requires_grad_(False)
        if parameter.ndim == 1 and 'norm' in name:
            parameter.data = parameter.data.to(torch.float32)
    model = PeftModel.from_pretrained(model, str((ROOT / adapter['weights']).parent), is_trainable=False)
    model.eval()
    tensors = torch.tensor([ids], device='cuda:0')
    budget = manifest['generation'][kind + '_output_tokens']
    with torch.no_grad():
        result = model.generate(input_ids=tensors, attention_mask=torch.ones_like(tensors),
                                max_new_tokens=budget, do_sample=False, use_cache=True,
                                logits_to_keep=1, pad_token_id=tok.eos_token_id)
    tail = result[0, len(ids):]
    raw = tok.decode(tail, skip_special_tokens=True)
    receipt = {'id': case, 'kind': kind, 'messages': messages, 'input_sha': digest(record['input']),
               'raw_output': raw, 'raw_sha': digest(raw), 'GPU_preflight': gpu,
               'CUDA_VISIBLE_DEVICES': os.environ.get('CUDA_VISIBLE_DEVICES'),
               'adapter_sha': adapter['sha256'], 'weight_updates': 0, 'GPU_calls': 1,
               'length_limit_reached': len(tail) >= budget and not bool((tail == tok.eos_token_id).any()),
               'status': 'awaiting_private_measurement_and_meaning_review' if kind == 'feature' else 'awaiting_meaning_review'}
    if kind == 'rule':
        try:
            receipt.update(check_rule(case, json.loads(raw)))
            receipt['GPU_calls'] = 1
        except json.JSONDecodeError:
            receipt['code_validation'] = {'passed': False, 'errors': ['invalid JSON']}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return {'output': str(output), 'status': receipt['status'], 'GPU_calls': 1}


def main():
    parser = argparse.ArgumentParser(description='피처·규칙 모델의 연구 자료 확인과 제한된 생성')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inspect').add_argument('--verify-weights', action='store_true')
    commands.add_parser('replay').add_argument('--case', required=True)
    validation = commands.add_parser('validate-rule')
    validation.add_argument('--case', required=True)
    validation.add_argument('--response', required=True)
    generation = commands.add_parser('generate')
    generation.add_argument('--kind', choices=['feature', 'rule'], required=True)
    generation.add_argument('--case', required=True)
    generation.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'inspect':
        result = inspect(args.verify_weights)
    elif args.command == 'replay':
        result = replay(args.case)
    elif args.command == 'validate-rule':
        result = check_rule(args.case, args.response)
    else:
        result = generate(args.kind, args.case, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == 'validate-rule' and not result['code_validation']['passed']:
        raise SystemExit(2)

