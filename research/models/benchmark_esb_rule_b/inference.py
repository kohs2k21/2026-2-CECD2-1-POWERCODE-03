"""Explicit local-only B inference, preserving the native greedy decoding recipe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

from .contract import PACKAGE, PROVIDER, build_messages, digest, file_sha, repair_messages, validate_prediction


def _reject_constant(value):
    raise ValueError("nonfinite_JSON_constant:" + value)


def verify_package():
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    for item in manifest["files"]:
        path = (PACKAGE / item["path"]).resolve()
        if not path.is_relative_to(PACKAGE) or not path.is_file() or file_sha(path) != item["sha256"]:
            raise ValueError("package_hash_mismatch:" + item["path"])
    return manifest


class BRuleModel:
    """No implicit downloads, alternate adapters, fitting or cached-output fallback."""

    def __init__(self, base_path):
        self.base = Path(base_path).resolve()
        self.manifest = verify_package()
        self.model = None
        self.tokenizer = None

    def _load(self):
        if self.model is not None:
            return
        visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
        if not visible or "," in visible:
            raise ValueError("Set CUDA_VISIBLE_DEVICES to exactly one assigned visible GPU")
        free = subprocess.check_output(["nvidia-smi", "-i", visible, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], text=True)
        if int(free.strip()) < 20000:
            raise ValueError("Assigned GPU has less than 20GB free; do not modify other processes")
        pins = json.loads((PACKAGE / "models/base_files.json").read_text())
        if not self.base.is_dir():
            raise ValueError("Explicit local Base path required")
        for item in pins["files"]:
            if file_sha(self.base / item["name"]) != item["sha256"]:
                raise ValueError("base_hash_mismatch:" + item["name"])
        import torch
        from peft import PeftModel
        from transformers import AutoTokenizer, BitsAndBytesConfig, Qwen3_5ForConditionalGeneration

        if torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported():
            raise ValueError("Exactly one assigned BF16-capable visible GPU required")
        tok = AutoTokenizer.from_pretrained(self.base, local_files_only=True)
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                              bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
        base = Qwen3_5ForConditionalGeneration.from_pretrained(
            self.base, local_files_only=True, quantization_config=q, dtype=torch.bfloat16,
            device_map={"": 0}, attn_implementation="sdpa", low_cpu_mem_usage=True)
        for name, p in base.named_parameters():
            p.requires_grad_(False)
            if p.ndim == 1 and "norm" in name:
                p.data = p.data.float()
        # Match the evaluated native loader: CPU embedding before PEFT wrapping.
        emb = base.get_input_embeddings()
        emb.to("cpu")
        emb.register_forward_pre_hook(lambda module, args: tuple(x.to("cpu") if torch.is_tensor(x) else x for x in args))
        emb.register_forward_hook(lambda module, args, out: out.to("cuda:0"))
        torch.cuda.empty_cache()
        self.model = PeftModel.from_pretrained(base, PACKAGE / "models/rule", is_trainable=False, local_files_only=True)
        self.model.eval()
        self.model.requires_grad_(False)
        if any(p.requires_grad for p in self.model.parameters()):
            raise ValueError("inference_weights_must_be_frozen")
        self.tokenizer = tok

    def _generate(self, messages):
        import torch

        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        encoded = self.tokenizer(text, add_special_tokens=False, return_tensors="pt")
        length = encoded["input_ids"].shape[1]
        if length > 4096:
            raise ValueError("native_input_exceeds_4096_tokens; split the input, never truncate")
        encoded = {key: value.to("cuda:0") for key, value in encoded.items()}
        start = time.monotonic()
        with torch.inference_mode():
            result = self.model.generate(**encoded, max_new_tokens=1024, do_sample=False,
                                         use_cache=True, logits_to_keep=1,
                                         pad_token_id=self.tokenizer.pad_token_id,
                                         eos_token_id=self.tokenizer.eos_token_id)
        tail = result[0, length:].tolist()
        eos = self.tokenizer.eos_token_id
        if eos in tail:
            tail = tail[:tail.index(eos) + 1]
        raw = self.tokenizer.decode(tail, skip_special_tokens=True)
        capped = len(tail) >= 1024 and eos not in tail
        try:
            prediction = json.loads(raw, parse_constant=_reject_constant)
        except ValueError:
            prediction = None
        return {"prediction": prediction, "raw_output": raw, "input_tokens": length,
                "generated_tokens": len(tail), "length_capped": capped,
                "seconds": time.monotonic() - start, "messages_sha": digest(messages)}

    def recommend(self, inp, output):
        messages = build_messages(inp)
        out = Path(output)
        out.mkdir(parents=True, exist_ok=False)

        def save(name, value):
            with (out / name).open("x") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)

        save("request.json", {"input": inp, "messages": messages, "input_sha": digest(inp)})
        try:
            self._load()
            attempts = []
            for i in range(2):
                try:
                    result = self._generate(messages)
                except ValueError as exc:
                    # A repair that exceeds context cannot silently replace the initial failure.
                    if i == 0:
                        raise
                    save("repair_not_executed.json", {"reason": str(exc)})
                    break
                errors = (["output_truncated"] if result["length_capped"] else
                          validate_prediction(inp, result["prediction"]))
                result["errors"] = errors
                attempts.append(result)
                save("attempt_" + str(i) + ".json", result)
                if not errors:
                    break
                messages = repair_messages(inp, result["raw_output"], errors)
            final = attempts[-1]
            response = {"provider": PROVIDER, "model_weight_sha": self.manifest["model"]["weight_sha256"],
                        "input_sha": digest(inp), "status": "validated" if not final["errors"] else "invalid",
                        "prediction": final["prediction"], "errors": final["errors"],
                        "attempts": len(attempts), "automatic_activation": False,
                        "generation_recipe": self.manifest["generation"]}
            save("result.json", response)
            return response
        except Exception as exc:
            save("failure.json", {"error": repr(exc), "fallback_used": False})
            raise
