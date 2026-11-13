"""프로토콜 경로 설명 사례 실행기. 기반 모델(Qwen3.6-27B NF4, 어댑터 없음)만 쓴다. 학습 없음.

    # CPU(가짜 생성기 = 사실로 만든 고정 문장): 사실 다시 만들기·프롬프트·점검·요약 경로 확인
    python -X utf8 run_cases.py --cases-dir cases --output-root <저장소 밖> --run-name cpu-check --mode cpu
    # GPU 1장(CUDA_VISIBLE_DEVICES 정확히 1개, Linux): 운영 generation_service.py와 같은 NF4 적재·greedy 생성
    python -X utf8 run_cases.py --cases-dir <dir> --output-root /out --run-name syn-gpu1 --mode gpu --base-path /models/qwen-base

출력: <output-root>/<YYYYMMDD-HHMMSS>-<run-name>/{run.json, <case>/..., summary.json, summary.md}. 덮어쓰기 없음.
실데이터(index kind=real)면 summary에는 상태·개수·참/거짓만 쓴다(ID·건수·생성 문장 없음). 사례 폴더는 서버 밖으로 옮기지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import kit

pe = kit.pe
RUN_FORMAT = "protocol-explain-run-v1"
LIBRARIES = ("torch", "transformers", "bitsandbytes", "accelerate", "huggingface-hub", "safetensors")
BASE_FILES = kit.REPO / "research" / "models" / "feature_rule_spec" / "models" / "base_files.json"
MODEL_CLASS = "Qwen3_5ForConditionalGeneration"


def library_versions() -> dict[str, str | None]:
    from importlib import metadata
    out: dict[str, str | None] = {"python": platform.python_version()}
    for name in LIBRARIES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def repo_commit(given: str | None) -> dict[str, Any]:
    if given:
        return {"sha": given, "source": "argument"}
    try:
        out = subprocess.run(["git", "-C", str(kit.REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=20)
        if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out.stdout.strip()):
            return {"sha": out.stdout.strip(), "source": "git rev-parse"}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"sha": None, "source": "unavailable"}


def gpu_snapshot() -> list[dict[str, Any]] | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 5:
            rows.append({"index": parts[0], "name": parts[1], "memory_used_mib": parts[2],
                         "memory_total_mib": parts[3], "utilization_pct": parts[4]})
    return rows


def create_run_dir(root: Path, run_name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{run_name}"
    run_dir.mkdir(exist_ok=False)
    return run_dir


def check_base(base: Path, mode: str) -> dict[str, Any]:
    """Base 파일 SHA 검사(번들 목록 base_files.json, 읽기만). metadata = safetensors 밖 파일만, full = 전부."""
    listed = kit.read_json(BASE_FILES)
    files = [item for item in listed["files"] if mode == "full" or not item["name"].endswith(".safetensors")]
    mismatched = [item["name"] for item in files
                  if not (base / item["name"]).is_file() or kit.sha256_file(base / item["name"]) != item["sha256"]]
    config = kit.read_json(base / "config.json") if (base / "config.json").is_file() else {}
    return {"mode": mode, "repo_id": listed.get("repo_id"), "revision": listed.get("revision"),
            "checked_files": len(files), "mismatched": mismatched,
            "architectures": config.get("architectures"), "passed": not mismatched
            and config.get("architectures") == [MODEL_CLASS]}


class BaseModel:
    """운영 generation_service.DesignModel과 같은 적재·생성 설정에서 어댑터만 뺀 것(explain 경로 = disable_adapter와 같음)."""

    def __init__(self, base: Path):
        import torch
        from transformers import AutoTokenizer, BitsAndBytesConfig, Qwen3_5ForConditionalGeneration
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported():
            raise RuntimeError("expose_one_bf16_gpu")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True)
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            str(base), local_files_only=True, quantization_config=quant, dtype=torch.bfloat16,
            device_map={"": 0}, attn_implementation="sdpa", low_cpu_mem_usage=True)
        for name, parameter in model.named_parameters():
            parameter.requires_grad_(False)
            if parameter.ndim == 1 and "norm" in name:
                parameter.data = parameter.data.to(torch.float32)
        model.eval()
        self.model = model

    def count_tokens(self, messages: list[dict[str, str]]) -> int:
        return len(self._ids(messages))

    def _ids(self, messages: list[dict[str, str]]) -> list[int]:
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                  enable_thinking=False)
        return self.tokenizer(text, add_special_tokens=False)["input_ids"]

    def generate(self, messages: list[dict[str, str]], max_new_tokens: int, time_limit: float) -> dict[str, Any]:
        from generation_service import stopped_by_time
        ids = self._ids(messages)
        torch = self.torch
        with torch.no_grad():
            torch.manual_seed(42)
            tensor = torch.tensor([ids], device="cuda:0")
            started = time.monotonic()
            output = self.model.generate(input_ids=tensor, attention_mask=torch.ones_like(tensor),
                                         max_new_tokens=max_new_tokens, do_sample=False, use_cache=True,
                                         logits_to_keep=1, pad_token_id=self.tokenizer.eos_token_id,
                                         max_time=time_limit)
            elapsed = time.monotonic() - started
        tail = output[0, len(ids):]
        finished = bool((tail == self.tokenizer.eos_token_id).any())
        return {"raw": self.tokenizer.decode(tail, skip_special_tokens=True), "seconds": round(elapsed, 3),
                "input_tokens": len(ids), "output_tokens": int(len(tail)), "finished": finished,
                "length_limit_hit": len(tail) >= max_new_tokens and not finished,
                "time_limit_hit": stopped_by_time(len(tail), max_new_tokens, finished, elapsed, time_limit)}


class TemplateModel:
    """CPU 가짜 생성기. 사실만으로 고정 문장을 만든다(모델 0회). 토큰 수는 세지 않는다."""

    def count_tokens(self, messages: list[dict[str, str]]) -> int | None:
        return None

    def generate(self, messages: list[dict[str, str]], max_new_tokens: int, time_limit: float) -> dict[str, Any]:
        facts = json.loads(messages[-1]["content"].split("사실(JSON):\n", 1)[1])
        started = time.monotonic()
        raw = pe.template_output(facts)
        return {"raw": raw, "seconds": round(time.monotonic() - started, 3), "input_tokens": None,
                "output_tokens": None, "finished": True, "length_limit_hit": False, "time_limit_hit": False}


def judge_case(result: dict[str, Any], time_limit: float) -> tuple[bool, list[str]]:
    reasons = []
    if not result.get("input_files_match"):
        reasons.append("input_sha_mismatch")
    if not result.get("facts_match"):
        reasons.append("facts_rebuild_mismatch")
    if result.get("error"):
        reasons.append(result["error"])
    generation = result.get("generation") or {}
    if generation.get("length_limit_hit"):
        reasons.append("length_limit_hit")
    if generation.get("time_limit_hit") or (generation.get("seconds") or 0) > time_limit:
        reasons.append("time_limit")
    checks = result.get("checks")
    if checks is not None:
        reasons += checks["failures"]
    elif not reasons:
        reasons.append("not_checked")
    return not reasons, reasons


def summary_row(case_id: str, spec: dict[str, Any], result: dict[str, Any], real: bool) -> dict[str, Any]:
    """요약 행. 실데이터는 값 없이 상태·개수·참/거짓만(details 원천 값·문장·ID·건수 없음)."""
    checks = result.get("checks") or {}
    details = checks.get("details") or {}
    generation = result.get("generation") or {}
    row = {"case_id": case_id, "kind": spec.get("kind"), "passed": result["passed"], "reasons": result["reasons"],
           "facts_match": result.get("facts_match"), "checks": checks.get("checks"), "coverage": checks.get("coverage"),
           "parse_mode": details.get("parse_mode"),
           "counts": {"unknown_numbers": len(details.get("unknown_numbers") or []),
                      "unknown_identifiers": len(details.get("unknown_identifiers") or []),
                      "unknown_paths": len(details.get("unknown_paths") or []),
                      "forbidden": len(details.get("forbidden") or []),
                      "review_points": len((details.get("lengths") or {}).get("reviewPoints") or [])},
           "korean_ratio": details.get("korean_ratio"),
           "seconds": generation.get("seconds"), "input_tokens": generation.get("input_tokens"),
           "output_tokens": generation.get("output_tokens"), "finished": generation.get("finished"),
           "length_limit_hit": generation.get("length_limit_hit"), "time_limit_hit": generation.get("time_limit_hit")}
    if not real:
        row.update(scenario=spec.get("scenario"), details={k: v for k, v in details.items() if k != "parse_mode"},
                   output=checks.get("output"))
    return row


def _mark(value: Any) -> str:
    return "-" if value is None else ("O" if value else "X")


def _text(value: Any) -> str:
    return "-" if value is None else str(value)


def write_summary(run_dir: Path, run: dict[str, Any], rows: list[dict[str, Any]], real: bool) -> dict[str, Any]:
    passed = sum(row["passed"] for row in rows)
    summary = {"format": RUN_FORMAT, "mode": run["mode"], "cases_kind": run["cases_kind"], "status": run["status"],
               "prompt_version": pe.PROMPT_VERSION, "system_prompt_sha256": run["system_prompt_sha256"],
               "passed": passed, "total": len(rows), "time_limit_seconds": run["parameters"]["time_limit_seconds"],
               "max_new_tokens": run["parameters"]["max_new_tokens"],
               "value_policy": "real: status, counts, booleans only" if real else "synthetic: full output",
               "cases": rows}
    kit.write_json(run_dir / "summary.json", summary)
    names = ("json_parsed", "schema_ok", "length_ok", "numbers_subset", "identifiers_subset", "paths_in_facts",
             "korean_ok", "no_forbidden")
    lines = [f"# 프로토콜 경로 설명 시험 — {run['run_dir_name']}", "",
             f"- 통과 **{passed}/{len(rows)}** · 모드 {run['mode']} · 사례 {run['cases_kind']} · 상태 {run['status']}",
             f"- 프롬프트 {pe.PROMPT_VERSION} · 시간 상한 {run['parameters']['time_limit_seconds']}초 · "
             f"출력 토큰 상한 {run['parameters']['max_new_tokens']}",
             "- 실데이터는 값 없이 참/거짓·개수만 적는다." if real else "- 합성 사례: 생성 문장을 아래에 그대로 적는다.", "",
             "| 사례 | 종류 | 사실 재생성 | 파싱 | " + " | ".join(names[1:]) + " | 초 | 출력 토큰 | length_limit | PASS/FAIL·사유 |",
             "|" + "---|" * (len(names) + 7)]
    for row in rows:
        checks = row.get("checks") or {}
        lines.append(" | ".join([
            f"| {row['case_id']}", f"{row['kind']}" + (f" ({row['scenario']})" if row.get("scenario") else ""),
            _mark(row["facts_match"]), row.get("parse_mode") or "-", *[_mark(checks.get(name)) for name in names[1:]],
            _text(row["seconds"]), _text(row["output_tokens"]), "hit" if row["length_limit_hit"] else "no",
            ("PASS" if row["passed"] else "FAIL " + ",".join(row["reasons"])) + " |"]))
    coverage_lines = []
    for row in rows:
        if row.get("coverage"):
            coverage_lines.append(f"- {row['case_id']}: " + ", ".join(f"{k}={_mark(v)}" for k, v in row["coverage"].items()))
    if coverage_lines:
        lines += ["", "## 핵심 사실 언급(정보용, 통과 기준 아님)", "", *coverage_lines]
    if not real:
        lines += ["", "## 생성 문장(합성)", ""]
        for row in rows:
            output = row.get("output")
            lines.append(f"### {row['case_id']}")
            if output:
                lines.append(f"- explanation: {output['explanation']}")
                lines += [f"- reviewPoint: {point}" for point in output["reviewPoints"]]
            else:
                lines.append("- (형식 불일치: raw_output.txt 참고)")
            bad = {k: v for k, v in (row.get("details") or {}).items()
                   if k in ("unknown_numbers", "unknown_identifiers", "unknown_paths", "forbidden") and v}
            if bad:
                lines.append(f"- 점검 실패 값: {json.dumps(bad, ensure_ascii=False)}")
            lines.append("")
    (run_dir / "summary.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return summary


def run_case(model: Any, case_dir_in: Path, spec: dict[str, Any], case_dir: Path, args: argparse.Namespace,
             alias: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"case_id": spec["case_id"], "kind": spec["kind"], "started_at": kit.now()}
    mismatched = [name for name, digest in spec["files"].items() if kit.sha256_file(case_dir_in / name) != digest]
    result["input_files_match"] = not mismatched
    result["input_mismatched"] = mismatched
    if mismatched:
        return result
    source = kit.read_json(case_dir_in / "source.json")
    facts = kit.read_json(case_dir_in / "facts.json")
    try:
        rebuilt, _ = kit.build_facts(source, alias)
    except ValueError as exc:
        result["error"] = f"facts_rebuild_error:{exc}"
        return result
    result["facts_match"] = rebuilt == facts
    messages = pe.build_messages(facts)
    result["prompt_sha256"] = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()
    tokens = model.count_tokens(messages)
    result["input_tokens"] = tokens
    if tokens is not None and tokens > args.max_input_tokens:
        result["error"] = "input_too_long"
        return result
    try:
        generation = model.generate(messages, args.max_new_tokens, args.time_limit)
    except Exception as exc:  # 생성 실패도 사례 실패로 남기고 다음 사례로
        result["error"] = f"generation_failed:{type(exc).__name__}"
        (case_dir / "generation_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        return result
    (case_dir / "raw_output.txt").write_text(generation.pop("raw"), encoding="utf-8")
    result["generation"] = generation
    checks = pe.check_output((case_dir / "raw_output.txt").read_text(encoding="utf-8"), facts)
    result["checks"] = checks
    kit.write_json(case_dir / "checks.json", checks)
    return result


def main(argv: list[str] | None = None, model_factory: Callable[[argparse.Namespace], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--mode", choices=["cpu", "gpu"], required=True)
    parser.add_argument("--cases", help="쉼표로 구분한 사례 ID 부분집합")
    parser.add_argument("--base-path", type=Path, default=os.environ.get("SPEC_BASE_PATH"))
    parser.add_argument("--base-check", choices=["metadata", "full", "none"], default="metadata")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--max-input-tokens", type=int, default=8000)
    parser.add_argument("--time-limit", type=float, default=120.0, help="시도당 생성 시간 상한(초, max_time)")
    parser.add_argument("--repo-commit")
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", args.run_name):
        raise SystemExit("run-name: [A-Za-z0-9._-]{1,64}")
    cases_dir, root = args.cases_dir.resolve(), args.output_root.resolve()
    if kit.inside(root, kit.REPO):
        raise SystemExit("output-root must be outside the git repository")
    index = kit.read_json(cases_dir / "index.json")
    if index.get("format") != kit.FORMAT:
        raise SystemExit("cases index format unsupported")
    by_id = {case["case_id"]: case for case in index["cases"]}
    selected = [c.strip() for c in args.cases.split(",")] if args.cases else list(by_id)
    unknown = [c for c in selected if c not in by_id]
    if unknown or not selected:
        raise SystemExit(f"unknown case ids: {unknown}")
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
    if args.mode == "gpu" and model_factory is None:
        if os.name == "nt":
            raise SystemExit("GPU runs are Linux-only")
        if not cvd or "," in cvd or not cvd.strip():
            raise SystemExit("CUDA_VISIBLE_DEVICES must name exactly one GPU")
        if not args.base_path or not Path(args.base_path).is_dir():
            raise SystemExit("--base-path must be the local Qwen base directory")
    real = index.get("kind") == "real"
    run_dir = create_run_dir(root, args.run_name)
    system_sha = hashlib.sha256(pe.SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    run: dict[str, Any] = {
        "format": RUN_FORMAT, "status": "running", "mode": args.mode, "started_at": kit.now(), "ended_at": None,
        "run_dir_name": run_dir.name, "cases_index_sha256": kit.sha256_file(cases_dir / "index.json"),
        "cases_kind": index.get("kind"), "cases_format": index.get("format"), "alias": index.get("alias"),
        "selected_cases": selected, "prompt_version": pe.PROMPT_VERSION, "system_prompt_sha256": system_sha,
        "module_sha256": kit.sha256_file(Path(pe.__file__)),
        "model": {"base": "Qwen3.6-27B", "class": MODEL_CLASS, "quantization": "nf4 double_quant bf16 compute",
                  "adapter": None, "generation": "greedy, enable_thinking=False, seed 42"} if args.mode == "gpu"
        else {"generator": "template (no model)"},
        "parameters": {"max_new_tokens": args.max_new_tokens, "max_input_tokens": args.max_input_tokens,
                       "time_limit_seconds": args.time_limit, "attempts_per_case": 1},
        "repo_commit": repo_commit(args.repo_commit), "CUDA_VISIBLE_DEVICES": cvd, "platform": platform.platform(),
        "libraries": library_versions(), "gpu_visible": gpu_snapshot(), "warnings": [], "cases": {}}
    kit.write_json(run_dir / "run.json", run)

    if args.mode == "cpu":
        model: Any = TemplateModel()
    elif model_factory is not None:
        model = model_factory(args)
    else:
        if args.base_check != "none":
            start = time.monotonic()
            run["base_check"] = check_base(Path(args.base_path), args.base_check)
            run["base_check"]["seconds"] = round(time.monotonic() - start, 3)
            kit.write_json(run_dir / "run.json", run)
            if not run["base_check"]["passed"]:
                run.update(status="stopped_base_check_failed", ended_at=kit.now())
                kit.write_json(run_dir / "run.json", run)
                print("STOP: base check failed; see", run_dir)
                return 2
        start = time.monotonic()
        model = BaseModel(Path(args.base_path))
        run["model_load_seconds"] = round(time.monotonic() - start, 3)
        run["gpu_name"] = (gpu_snapshot() or [{}])[0].get("name")
        kit.write_json(run_dir / "run.json", run)

    rows = []
    try:
        for case_id in selected:
            spec = by_id[case_id]
            free_gb = shutil.disk_usage(run_dir).free / 1e9
            if free_gb < args.min_free_gb:
                run["warnings"].append({"case": case_id, "stop": "disk_free_below_minimum"})
                run["status"] = "stopped_disk"
                break
            case_dir = run_dir / case_id
            case_dir.mkdir(exist_ok=False)
            result = run_case(model, cases_dir / spec["path"], spec, case_dir, args, bool(index.get("alias", True)))
            result["passed"], result["reasons"] = judge_case(result, args.time_limit)
            result["ended_at"] = kit.now()
            kit.write_json(case_dir / "case_result.json", result)
            rows.append(summary_row(case_id, spec, result, real))
            run["cases"][case_id] = {"passed": result["passed"], "reasons": result["reasons"]}
            # 콘솔에는 사례 ID·통과·사유 코드만(값 없음)
            print(case_id, "PASS" if result["passed"] else "FAIL", ",".join(result["reasons"]))
            kit.write_json(run_dir / "run.json", run)
        else:
            run["status"] = "completed"
    except KeyboardInterrupt:
        run["status"] = "interrupted"
    finally:
        if isinstance(model, BaseModel):
            try:
                run["max_memory_allocated_bytes"] = model.torch.cuda.max_memory_allocated()
            except Exception as exc:
                run["max_memory_allocated_bytes"] = f"unavailable:{type(exc).__name__}"
        run["ended_at"] = kit.now()
        kit.write_json(run_dir / "run.json", run)
        summary = write_summary(run_dir, run, rows, real)
    print(f"passed {summary['passed']}/{summary['total']} run dir: {run_dir}")
    return 0 if run["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
