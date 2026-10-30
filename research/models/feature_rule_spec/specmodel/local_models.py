"""Pinned local NF4 Qwen execution. No downloads, templates, or saved-output fallback."""
from pathlib import Path
import os, json, time, contextlib, subprocess
from .io import *
PACKAGE = Path(__file__).resolve().parent.parent

class LocalModels:

    def __init__(self, base_path, rule_adapter=None, candidate_feature=None):
        self.base = Path(base_path).resolve() if base_path else None
        self.rule = Path(rule_adapter).resolve() if rule_adapter else PACKAGE / 'models/rule'
        self.candidate = Path(candidate_feature).resolve() if candidate_feature else None
        self.model = None
        self.tokenizer = None
        self.compiler = None
        self.adapters = {}
        self.integrity = None

    def _cpu(self):
        selected = read(PACKAGE / 'model_manifest.json')
        if selected['Feature']['selection'] not in ['Base', 'LoRA']:
            raise ModelFailure('Explicit supported Feature selection required')
        if selected['Feature']['selection'] == 'Base' and selected['Feature']['adapter'] is not None:
            raise ModelFailure('Base default cannot contain a Feature adapter')
        if selected['Feature']['selection'] == 'LoRA' and (not selected['Feature'].get('weight_sha256')):
            raise ModelFailure('Selected Feature LoRA requires an exact weight SHA')
        if not self.base or not self.base.is_dir():
            raise ModelFailure('Pinned local Base path required; no download or stored response fallback')
        if self.tokenizer is None:
            expected = read(PACKAGE / 'models/base_files.json')
            for f in expected['files']:
                p = self.base / f['name']
                if not p.is_file() or sha(p) != f['sha256']:
                    raise ModelFailure('Pinned Base content mismatch: ' + f['name'])
            self.integrity = {'repo_id': expected['repo_id'], 'revision': expected['revision'], 'verified_files': len(expected['files'])}
            from transformers import AutoTokenizer, AutoConfig
            import xgrammar
            self.tokenizer = AutoTokenizer.from_pretrained(self.base, local_files_only=True)
            cfg = AutoConfig.from_pretrained(self.base, local_files_only=True)
            if cfg.architectures != ['Qwen3_5ForConditionalGeneration']:
                raise ModelFailure('Expected pinned Qwen3_5 class')
            self.compiler = xgrammar.GrammarCompiler(xgrammar.TokenizerInfo.from_huggingface(self.tokenizer, vocab_size=cfg.text_config.vocab_size), max_threads=4)

    def preflight(self, messages, schema, maximum):
        self._cpu()
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        ids = self.tokenizer(text, add_special_tokens=False)['input_ids']
        if len(ids) > 12000 or len(ids) + maximum + 3072 > 32768:
            raise InputFailure('Input exceeds validated context with reserved output/repair; split requested analysis context, never truncate')
        grammar = self.compiler.compile_json_schema(schema)
        return (text, grammar, {'input_tokens': len(ids), 'input_token_sha': digest(ids), 'output_reserved': maximum, 'repair_reserved': 3072, 'truncated': False, 'schema_sha': digest(schema), 'chat_template_sha': digest(self.tokenizer.chat_template)})

    def _load(self, dest):
        if self.model is not None:
            return
        cvd = os.environ.get('CUDA_VISIBLE_DEVICES')
        if not cvd or ',' in cvd:
            raise ModelFailure('Set CUDA_VISIBLE_DEVICES to exactly one assigned visible GPU')
        snap = subprocess.check_output(['nvidia-smi', '-i', cvd, '--query-gpu=uuid,name,memory.free,utilization.gpu', '--format=csv,noheader,nounits'], text=True)
        write(dest / 'GPU_before_load.json', {'CUDA_VISIBLE_DEVICES': cvd, 'snapshot': snap})
        if int(snap.split(',')[2]) < 20000:
            raise ModelFailure('Assigned GPU free memory below validated20GB load requirement; do not modify other processes')
        import torch
        from transformers import Qwen3_5ForConditionalGeneration, BitsAndBytesConfig
        if torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported():
            raise ModelFailure('Exactly one BF16-capable assigned GPU required')
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
        self.model = Qwen3_5ForConditionalGeneration.from_pretrained(self.base, local_files_only=True, quantization_config=q, dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa', low_cpu_mem_usage=True)
        for (n, p) in self.model.named_parameters():
            p.requires_grad_(False)
            if p.ndim == 1 and 'norm' in n:
                p.data = p.data.float()
        emb = self.model.model.language_model.embed_tokens
        emb.to('cpu')
        emb.register_forward_pre_hook(lambda module, args: tuple((x.to('cpu') if torch.is_tensor(x) else x for x in args)))
        emb.register_forward_hook(lambda module, args, out: out.to('cuda:0'))
        torch.cuda.empty_cache()
        self.model.eval()

    def generate(self, kind, messages, schema, dest, budget, call, maximum=1536, arm='Base'):
        freeze = os.environ.get('SPEC_FROZEN_POLICY')
        if freeze:
            for p in read(freeze)['files']:
                if sha(p['path']) != p['sha256']:
                    raise ModelFailure('Frozen executable policy changed: ' + p['path'])
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (text, grammar, tokens) = self.preflight(messages, schema, maximum)
        receipt = budget.reserve(kind, call)
        write(dest / 'input.json', {'messages': messages, 'messages_sha': digest(messages), 'schema': schema, 'tokens': tokens, 'budget': receipt})
        start = time.monotonic()
        try:
            self._load(dest)
            import torch
            from peft import PeftModel
            from transformers import LogitsProcessorList
            from xgrammar.contrib.hf import LogitsProcessor
            selection = read(PACKAGE / 'model_manifest.json')['Feature']
            path = self.rule if kind == 'rule' else self.candidate if arm == 'candidate' else (PACKAGE / selection['adapter']).resolve() if arm == 'selected' and selection['selection'] == 'LoRA' else None
            name = 'Rule' if kind == 'rule' else 'Feature_selected' if arm == 'selected' else 'Feature_candidate'
            if arm == 'candidate' and path is None:
                raise ModelFailure('Explicit candidate required; no automatic model switch')
            if path:
                if not (path / 'adapter_model.safetensors').is_file():
                    raise ModelFailure('Required adapter absent')
                if kind == 'rule':
                    expected = read(PACKAGE / 'model_manifest.json')['Rule']['weight_sha256']
                    if sha(path / 'adapter_config.json') != read(PACKAGE / 'model_manifest.json')['Rule']['config_sha256']:
                        raise ModelFailure('Pinned Rule adapter config mismatch')
                    if sha(path / 'adapter_model.safetensors') != expected:
                        raise ModelFailure('Fixed selected Rule weight mismatch')
                if kind == 'feature' and arm == 'selected' and sha(path / 'adapter_config.json') != selection['config_sha256']:
                    raise ModelFailure('Pinned selected Feature adapter config mismatch')
                if kind == 'feature' and arm == 'selected' and (sha(path / 'adapter_model.safetensors') != selection['weight_sha256']):
                    raise ModelFailure('Selected Feature adapter mismatch; no fallback to Base or a candidate')
                if not isinstance(self.model, PeftModel):
                    self.model = PeftModel.from_pretrained(self.model, path, is_trainable=False, adapter_name=name, local_files_only=True)
                elif name not in self.adapters:
                    self.model.load_adapter(path, adapter_name=name, is_trainable=False, local_files_only=True)
                self.model.set_adapter(name)
                self.adapters[name] = str(path)
            disabled = path is None and isinstance(self.model, PeftModel)
            self.model.eval()
            self.model.requires_grad_(False)
            assert not any((p.requires_grad for p in self.model.parameters()))
            encoded = self.tokenizer(text, return_tensors='pt', add_special_tokens=False).to('cuda:0')
            with self.model.disable_adapter() if disabled else contextlib.nullcontext():
                flags = [m.disable_adapters for m in self.model.modules() if hasattr(m, 'disable_adapters')] if disabled else []
                if disabled:
                    assert flags and all(flags), 'Base must actually disable all LoRA layers'
                with torch.inference_mode():
                    answer = self.model.generate(**encoded, max_new_tokens=maximum, do_sample=False, use_cache=True, logits_processor=LogitsProcessorList([LogitsProcessor(grammar)]), eos_token_id=self.tokenizer.eos_token_id, pad_token_id=self.tokenizer.pad_token_id)
            ids = answer[0, encoded['input_ids'].shape[1]:].tolist()
            eos = self.tokenizer.eos_token_id
            if eos in ids:
                ids = ids[:ids.index(eos) + 1]
            raw = self.tokenizer.decode(ids, skip_special_tokens=True)
            try:
                parsed = json.loads(raw)
                error = None
            except json.JSONDecodeError as e:
                parsed = None
                error = str(e)
            out = {'kind': kind, 'arm': arm, 'raw': raw, 'parsed': parsed, 'parse_error': error, 'length_limit_hit': len(ids) >= maximum and eos not in ids, 'messages_sha': digest(messages), 'tokens': tokens, 'output_tokens': len(ids), 'greedy': True, 'thinking': False, 'budget': receipt, 'model': {'Base': self.integrity, 'adapter_kind': name if path else None, 'adapter_weight_sha': sha(path / 'adapter_model.safetensors') if path else None, 'Base_adapter_layers_disabled': len(flags), 'all_weights_frozen': True, 'class': type(self.model).__name__, 'quantization': self.model.config.quantization_config.to_dict(), 'compute_dtype': 'bfloat16'}, 'seconds': time.monotonic() - start, 'GPU_memory': {'allocated': torch.cuda.memory_allocated(), 'reserved': torch.cuda.memory_reserved(), 'peak': torch.cuda.max_memory_allocated()}}
            write(dest / 'output.json', out)
            del encoded, answer
            torch.cuda.empty_cache()
            return out
        except BaseException as e:
            write(dest / 'failure.json', {'kind': kind, 'error': repr(e), 'budget': receipt, 'no_template_or_old_response_fallback': True, 'Model_failure_vs_infrastructure': 'infrastructure/execution; inspect traceback', 'seconds': time.monotonic() - start})
            raise
