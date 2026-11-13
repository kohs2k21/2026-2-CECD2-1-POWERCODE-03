"""Rule v3 기준선 사례 실행기. 번들 파일은 고치지 않고 공식 CLI(`specmodel.cli.main`)를 같은 프로세스에서 부른다.

한 실행 = GPU 1장. 모델은 실행당 한 번만 올리고(공식 `LocalModels` 인스턴스를 사례 간 공유) 사례마다
validate-feature(CPU) → [rule(GPU)] → replay(CPU)를 새 폴더에 남긴다. 기존 폴더는 절대 덮어쓰지 않는다.

    # 오늘(CPU만): 패키지 검사 + 번들 CPU 시험 + 사례별 validate-feature·기준선 사전 검사·검증기 대조
    python run_cases.py --cases-dir cases --output-root D:/Temp/... --run-name cpu-check --cpu-only
    # 토요일(GPU 1장): CUDA_VISIBLE_DEVICES는 정확히 1개
    python run_cases.py --cases-dir <dir> --output-root /out --run-name syn-gpu1 --base-path /models/qwen-base

출력: <output-root>/<YYYYMMDD-HHMMSS>-<run-name>/{run.json, precheck_*.log, <case>/..., summary.json, summary.md}
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import types
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BUNDLE = REPO / "research" / "models" / "feature_rule_spec"
FORMAT = "rule-v3-baseline-run-v1"
KO_LINE = ("Additional run instruction (outside the frozen bundle): write every decision reason field in Korean; "
           "keep all other fields, identifiers and numbers exactly as specified.")
STATES = ("proposed", "deferred", "request_evidence", "no_applicable")
LIBRARIES = ("torch", "transformers", "peft", "bitsandbytes", "xgrammar", "jsonschema", "accelerate", "huggingface-hub")


def _utf8_reexec() -> None:
    """번들은 Path.read_text()를 기본 인코딩으로 쓴다. UTF-8 모드가 아니면 -X utf8로 다시 실행한다."""
    import locale
    if sys.flags.utf8_mode or locale.getpreferredencoding(False).lower().replace("-", "") == "utf8":
        return
    os.execv(sys.executable, [sys.executable, "-X", "utf8", *sys.argv])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def repo_commit(repo: Path, given: str | None) -> dict[str, Any]:
    """git 명령이 없거나(slim 이미지) 읽기 전용 마운트여도 .git을 직접 읽어 HEAD 커밋을 찾는다."""
    if given:
        return {"sha": given, "source": "argument"}
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out.stdout.strip()):
            return {"sha": out.stdout.strip(), "source": "git rev-parse"}
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        git = repo / ".git"
        if git.is_file():  # 워크트리: "gitdir: <path>"
            git = (repo / git.read_text(encoding="utf-8").split(":", 1)[1].strip()).resolve()
        common = git
        if (git / "commondir").is_file():
            common = (git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return {"sha": head, "source": ".git/HEAD (detached)"}
        ref = head.split(":", 1)[1].strip()
        for base in (git, common):
            if (base / ref).is_file():
                return {"sha": (base / ref).read_text(encoding="utf-8").strip(), "source": ".git ref file"}
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return {"sha": line.split()[0], "source": ".git packed-refs"}
    except (OSError, IndexError):
        pass
    return {"sha": None, "source": "unavailable"}


def gpu_snapshot() -> list[dict[str, Any]] | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 6:
            rows.append({"index": parts[0], "uuid": parts[1], "name": parts[2], "memory_used_mib": int(parts[3]),
                         "memory_total_mib": int(parts[4]), "utilization_pct": parts[5]})
    return rows


def foreign_changes(before, after, assigned: str | None, threshold: int) -> list[dict[str, Any]]:
    """할당되지 않은 GPU의 메모리 변화(중단 기준). 컨테이너 안에서는 할당 GPU만 보여 비교 대상이 없을 수 있다."""
    if not before or not after or len(before) < 2:
        return []
    out = []
    for b, a in zip(before, after):
        if assigned is not None and assigned in (b["index"], b["uuid"]):
            continue
        delta = a["memory_used_mib"] - b["memory_used_mib"]
        if abs(delta) >= threshold:
            out.append({"index": b["index"], "delta_mib": delta})
    return out


def library_versions() -> dict[str, str | None]:
    from importlib import metadata
    versions = {"python": platform.python_version()}
    for name in LIBRARIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def run_logged(command: list[str], cwd: Path, log: Path, env: dict[str, str]) -> tuple[int, float, str]:
    start = time.monotonic()
    out = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env)
    log.write_text(out.stdout + "\n--- stderr ---\n" + out.stderr, encoding="utf-8")
    return out.returncode, round(time.monotonic() - start, 3), out.stdout + out.stderr


FCNTL_SHIM = ("import sys,types\ntry:\n import fcntl\nexcept ImportError:\n"
              " sys.modules['fcntl']=types.SimpleNamespace(LOCK_EX=2,flock=lambda *a,**k:None)\n")


def prechecks(bundle: Path, run_dir: Path) -> dict[str, Any]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    result: dict[str, Any] = {}
    code, seconds, text = run_logged([sys.executable, "-B", "verify_package.py"], bundle,
                                     run_dir / "precheck_verify_package.log", env)
    match = re.search(r"\{.*\}", text)
    result["verify_package"] = {"exit": code, "seconds": seconds,
                                "report": json.loads(match.group(0)) if match else None}
    tests = ("import unittest,sys\nr=unittest.main(module=None,argv=['t','discover','-s','tests','-v'],exit=False)\n"
             "sys.exit(0 if r.result.wasSuccessful() else 1)\n")
    command = [sys.executable, "-B", "-c", (FCNTL_SHIM if os.name == "nt" else "") + tests]
    code, seconds, text = run_logged(command, bundle, run_dir / "precheck_bundle_tests.log", env)
    ran = re.search(r"Ran (\d+) tests?", text)
    result["bundle_tests"] = {"exit": code, "seconds": seconds, "ran": int(ran.group(1)) if ran else None,
                              "ok": bool(re.search(r"^OK", text, re.M)),
                              "windows_fcntl_shim": os.name == "nt"}
    return result


def create_run_dir(root: Path, run_name: str, stamp: str | None = None) -> Path:
    """<root>/<YYYYMMDD-HHMMSS>-<run-name>. 이미 있으면 FileExistsError로 즉시 실패(덮어쓰기 없음)."""
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / f"{stamp or dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{run_name}"
    run_dir.mkdir(exist_ok=False)
    return run_dir


def gpu_platform_ok() -> bool:
    """GPU 실행은 Linux만(번들 Budget이 fcntl.flock 사용). 배선 시험은 이 함수를 바꿔 끼운다."""
    return os.name != "nt"


def import_bundle(bundle: Path):
    sys.dont_write_bytecode = True
    try:
        import fcntl  # noqa: F401
    except ImportError:  # Windows CPU 확인용. GPU 실행은 main의 gpu_platform_ok()가 Windows를 막는다
        sys.modules["fcntl"] = types.SimpleNamespace(LOCK_EX=2, flock=lambda *a, **k: None)
    sys.path.insert(0, str(bundle))
    import specmodel.cli as cli
    import specmodel.rule_logic as rule_logic
    return cli, rule_logic


def call_cli(cli, argv: list[str], log: Path) -> dict[str, Any]:
    """공식 CLI를 같은 프로세스에서 부른다. 표준 출력·오류는 사례 로그로만 보낸다(콘솔에 값이 나가지 않게)."""
    buffer_out, buffer_err = io.StringIO(), io.StringIO()
    start = time.monotonic()
    crash = None
    with contextlib.redirect_stdout(buffer_out), contextlib.redirect_stderr(buffer_err):
        try:
            code = cli.main([str(a) for a in argv])
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # CLI가 잡지 않는 예외도 사례 실패로 남기고 다음 사례로 간다
            code, crash = 4, type(exc).__name__
            traceback.print_exc(file=buffer_err)
    seconds = round(time.monotonic() - start, 3)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {argv[0]} (exit {code}, {seconds}s)\n{buffer_out.getvalue()}\n--- stderr ---\n{buffer_err.getvalue()}\n")
    return {"exit": code, "seconds": seconds, "crash": crash}


def oracle_decision(record: dict[str, Any], state: str, needed: list[str]) -> dict[str, Any]:
    """검증기 대조용 결정(모델 답이 아님). 기대 상태가 번들 검증기와 모순되지 않는지만 본다."""
    i = record["input"]
    b = i["baseline"]
    verified = b.get("state") == "verified"
    executable = state in ("proposed", "deferred")
    return {"v": "3", "feature": i["feature"]["id"], "sha": i["feature"]["sha"], "decisions": [{
        "request": "baseline", "role": "SR", "state": state,
        "basis": "reference_p95" if executable else "insufficient" if state == "request_evidence" else "none",
        "condition": {"op": b.get("comparison", "gt"), "value": b.get("value")} if executable and verified else
        ({"op": "gt", "value": 0} if executable else None),
        "evidence": [i["measurement_id"]] + ([b["id"]] if b.get("id") else []),
        "needed": list(needed), "reason": "Oracle decision for a CPU validator consistency check.",
        "purpose": "other", "warning_meaning": "Offline comparison check only.",
        "population_ref": i["application_context"]["population_ref"],
        "precondition_refs": list(i["application_context"]["fact_ids"]),
        "required_evaluation_basis": [i["application_context"]["population_ref"]],
        "unavailable_action": "preserve_unassessed" if executable else "no_rule",
        "unavailable_reason": "Rows without a numeric value stay unassessed.",
        "business_scope": "No business, incident or online meaning is confirmed.",
        "baseline_use_ref": b.get("id") if executable else None}]}


def cpu_checks(paths: dict[str, Path], expected: dict[str, Any], real: bool, layout_facts: dict[str, Any]) -> dict[str, Any]:
    """번들 코드로 Rule 입력(기준선)을 미리 만들고, 상태별 대조 결정을 번들 검증기에 넣어 본다(모델 0회)."""
    from specmodel.calculations import compile_feature
    from specmodel.dataset import Dataset
    from specmodel.rule_logic import make_rule_record, validate_rule
    import capture_layout as layout

    ds, ref = Dataset(paths["application"]), Dataset(paths["reference"])
    proposal, plan = read_json(paths["proposal"]), read_json(paths["rule_plan"])
    compiled = compile_feature(ds, proposal, "cpu_check")
    if not compiled["passed"]:
        return {"compiled": False, "error_codes": [e.get("code") for e in compiled["errors"]]}
    record = make_rule_record(ds, ref, proposal, compiled, plan)
    b, m = record["input"]["baseline"], record["input"]["measurement"]
    matrix = {}
    for state in STATES:
        needed = expected["needed"] if state in expected["states"] else (
            ["need_valid_observations"] if state == "deferred" else ["need_reference"] if state == "request_evidence" else [])
        matrix[state] = sorted({e["code"] for e in validate_rule(record, oracle_decision(record, state, needed))})
    independent = layout.canonical_number(layout_facts["p95"]) if b.get("state") == "verified" else None
    out = {"compiled": True, "baseline_state": b.get("state"), "baseline_needed": b.get("needed"),
           "reference_valid_n": b.get("reference_valid_n"), "minimum_n": b.get("minimum_n"),
           "comparison": b.get("comparison"), "current_valid": m["valid_count"], "current_rows": m["row_count"],
           "range_proof_max_present": record["input"]["range_proof"].get("max") is not None,
           "bundle_p95_matches_independent": None if independent is None else layout.canonical_number(b.get("value")) == independent,
           "oracle_validator_errors": matrix,
           "expected_states_accepted": all(matrix[s] == [] for s in expected["states"])}
    if not real:
        out["bundle_baseline_value"] = b.get("value")
        out["range_proof_max"] = record["input"]["range_proof"].get("max")
    return out


def extract_attempts(rule_dir: Path, case_dir: Path) -> list[dict[str, Any]]:
    attempts = []
    model = rule_dir / "model"
    for folder in sorted(model.glob("rule_*")) if model.is_dir() else []:
        n = folder.name.split("_", 1)[1]
        item: dict[str, Any] = {"attempt": int(n) if n.isdigit() else n}
        if (folder / "output.json").is_file():
            out = read_json(folder / "output.json")
            (case_dir / f"raw_attempt_{n}.txt").write_text(out.get("raw") or "", encoding="utf-8")
            write_json(case_dir / f"parsed_attempt_{n}.json", out.get("parsed"))
            tokens = out.get("tokens") or {}
            item.update(seconds=out.get("seconds"), input_tokens=tokens.get("input_tokens"),
                        output_tokens=out.get("output_tokens"), length_limit_hit=out.get("length_limit_hit"),
                        parse_error=out.get("parse_error") is not None,
                        gpu_peak_bytes=(out.get("GPU_memory") or {}).get("peak"),
                        raw_file=f"raw_attempt_{n}.txt", parsed_file=f"parsed_attempt_{n}.json")
        if (folder / "failure.json").is_file():
            failure = read_json(folder / "failure.json")
            item.update(failure=True, seconds=failure.get("seconds"))
        attempts.append(item)
    return attempts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases-dir", type=Path, required=True, help="index.json이 있는 폴더(cases/ 또는 export 출력)")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--cases", help="쉼표로 구분한 사례 ID 부분집합(GPU별 분할)")
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--base-path", type=Path, default=os.environ.get("SPEC_BASE_PATH"))
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    parser.add_argument("--max-model-calls", type=int, default=2)
    parser.add_argument("--reason-language", choices=["ko"])
    parser.add_argument("--repo-commit", help="실행 저장소 커밋 SHA(없으면 .git에서 읽음)")
    parser.add_argument("--min-free-gb", type=float, default=5.0)
    parser.add_argument("--foreign-gpu-delta-mib", type=int, default=1024)
    parser.add_argument("--stop-on-foreign-gpu-change", action="store_true")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args(argv)
    import locale
    if not sys.flags.utf8_mode and locale.getpreferredencoding(False).lower().replace("-", "") != "utf8":
        raise SystemExit("UTF-8 mode required: run with python -X utf8 (bundle reads files with the default encoding)")

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", args.run_name):
        raise SystemExit("run-name: [A-Za-z0-9._-]{1,64}")
    bundle, cases_dir, root = args.bundle.resolve(), args.cases_dir.resolve(), args.output_root.resolve()
    if inside(root, REPO) or inside(root, bundle):
        raise SystemExit("output-root must be outside the git repository")
    index = read_json(cases_dir / "index.json")
    by_id = {c["case_id"]: c for c in index["cases"]}
    selected = [c.strip() for c in args.cases.split(",")] if args.cases else list(by_id)
    unknown = [c for c in selected if c not in by_id]
    if unknown or not selected:
        raise SystemExit(f"unknown case ids: {unknown}")
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
    if not args.cpu_only:
        if not gpu_platform_ok():
            raise SystemExit("GPU runs are Linux-only (bundle uses fcntl)")
        if not cvd or "," in cvd or not cvd.strip():
            raise SystemExit("CUDA_VISIBLE_DEVICES must name exactly one GPU")
        if not args.base_path or not Path(args.base_path).is_dir():
            raise SystemExit("--base-path (or SPEC_BASE_PATH) must be the local Qwen base directory")
        if not 1 <= args.max_model_calls <= 2:
            raise SystemExit("--max-model-calls must be 1 or 2 (first attempt + at most one repair)")

    run_dir = create_run_dir(root, args.run_name)
    real = index.get("kind") == "real"
    run: dict[str, Any] = {
        "format": FORMAT, "status": "running", "mode": "cpu_only" if args.cpu_only else "gpu",
        "started_at": now(), "ended_at": None, "run_dir_name": run_dir.name, "cases_index": str(cases_dir / "index.json"),
        "cases_index_sha256": sha256_file(cases_dir / "index.json"), "cases_kind": index.get("kind"),
        "cases_format": index.get("format"), "input_assumptions": index.get("input_assumptions", []),
        "held_window": index.get("held_window"), "selected_cases": selected, "max_model_calls": args.max_model_calls,
        "variant": {"reason_language": args.reason_language,
                    "label": "번들 외 변형: reason-language=ko" if args.reason_language else "번들 그대로"},
        "repo_commit": repo_commit(REPO, args.repo_commit), "bundle_path": str(bundle),
        "CUDA_VISIBLE_DEVICES": cvd, "platform": platform.platform(), "libraries": library_versions(),
        "gpu_visible": gpu_snapshot(), "warnings": [], "cases": {}}
    write_json(run_dir / "run.json", run)

    run["prechecks"] = prechecks(bundle, run_dir)
    write_json(run_dir / "run.json", run)
    if run["prechecks"]["verify_package"]["exit"] != 0 or run["prechecks"]["bundle_tests"]["exit"] != 0:
        run.update(status="stopped_precheck_failed", ended_at=now())
        write_json(run_dir / "run.json", run)
        print("STOP: verify_package or bundle tests failed; see", run_dir)
        return 2

    cli, rule_logic = import_bundle(bundle)
    sys.path.insert(0, str(HERE))
    import capture_layout as layout
    manifest = read_json(bundle / "model_manifest.json")
    original_system, original_cli_models = rule_logic.rule_system, cli.LocalModels
    effective_system = original_system() + ("\n" + KO_LINE if args.reason_language == "ko" else "")
    run["package"] = {
        "artifact_manifest_sha256": sha256_file(bundle / "artifact_manifest.json"),
        "model_manifest_sha256": sha256_file(bundle / "model_manifest.json"),
        "rule_adapter_weight_sha256": sha256_file(bundle / "models/rule/adapter_model.safetensors"),
        "rule_adapter_config_sha256": sha256_file(bundle / "models/rule/adapter_config.json"),
        "rule_adapter_expected_weight_sha256": manifest["Rule"]["weight_sha256"],
        "feature_adapter_expected_weight_sha256": manifest["Feature"]["weight_sha256"],
        "rule_system_file_sha256": sha256_file(bundle / "contracts/rule_system.txt"),
        "rule_schema_sha256": sha256_file(bundle / "contracts/rule.schema.json"),
        "effective_rule_system_prompt_sha256": hashlib.sha256(effective_system.encode("utf-8")).hexdigest()}

    models = None
    if not args.cpu_only:
        from specmodel.local_models import LocalModels
        models = LocalModels(args.base_path, None, None)
        start = time.monotonic()
        models._cpu()  # 공식 Base 파일 SHA 검사·토크나이저·문법 컴파일러(실행당 1회)
        run["base_check_seconds"] = round(time.monotonic() - start, 3)
        run["base_integrity"] = models.integrity
        start = time.monotonic()
        models._load(run_dir)  # 공식 NF4 적재(실행당 1회). 이후 사례의 generate는 적재를 건너뛴다
        run["model_load_seconds"] = round(time.monotonic() - start, 3)
        run["gpu_name"] = (gpu_snapshot() or [{}])[0].get("name")
        write_json(run_dir / "run.json", run)

    try:
        # 번들 파일은 그대로 두고 이 실행 동안만 바꾼다. 둘 다 finally에서 원래대로 되돌린다.
        if models is not None:
            cli.LocalModels = lambda *a, **k: models  # 공식 CLI 경로 그대로, 인스턴스만 공유
        if args.reason_language == "ko":
            rule_logic.rule_system = lambda: effective_system
        for case_id in selected:
            spec = by_id[case_id]
            free_gb = shutil.disk_usage(run_dir).free / 1e9
            if free_gb < args.min_free_gb:
                run["warnings"].append({"case": case_id, "stop": "disk_free_below_minimum", "free_gb": round(free_gb, 1)})
                run["status"] = "stopped_disk"
                break
            case_dir = run_dir / case_id
            case_dir.mkdir(exist_ok=False)
            src = cases_dir / spec["path"]
            paths = {"application": src / "application/capture.json", "reference": src / "reference/capture.json",
                     "proposal": src / "proposal.json", "request": src / "request.json", "rule_plan": src / "rule_plan.json"}
            result: dict[str, Any] = {"case_id": case_id, "field": spec["field"], "kind": spec.get("kind"),
                                      "started_at": now(), "steps": {}, "attempts": []}
            mismatched = [rel for rel, digest in spec["files"].items() if sha256_file(src / rel) != digest]
            result["input_sha256"] = {"files_match_index": not mismatched, "mismatched": mismatched,
                                      **{k: sha256_file(p) for k, p in paths.items()}}
            before = gpu_snapshot()
            log = case_dir / "cli.log"
            if mismatched:
                result["steps"]["input_check"] = {"exit": 1}
            elif spec.get("held_window_overlap"):
                # 번들 입장 검사가 거부할 기간: 실행하지 않고 기록만(export를 --avoid-held-window로 다시 만든다)
                result["steps"]["held_window_check"] = {"exit": 1, "reason": "held_window_overlap"}
            else:
                result["steps"]["validate_feature"] = call_cli(cli, [
                    "validate-feature", "--input-manifest", paths["application"], "--request", paths["request"],
                    "--proposal", paths["proposal"], "--output", case_dir / "feature"], log)
                facts = layout.facts_from_case(src, spec["field"])
                expected = spec.get("expected") or facts["expected"]
                if result["steps"]["validate_feature"]["exit"] == 0:
                    try:
                        result["cpu_checks"] = cpu_checks(paths, expected, real, facts)
                    except Exception as exc:  # 사전 검사 실패도 기록하고 계속
                        result["cpu_checks"] = {"error": type(exc).__name__}
                    if not args.cpu_only:
                        result["steps"]["rule"] = call_cli(cli, [
                            "rule", "--input-manifest", paths["application"], "--reference-manifest", paths["reference"],
                            "--feature-bundle", case_dir / "feature/feature_bundle.json", "--rule-plan", paths["rule_plan"],
                            "--base-path", args.base_path, "--output", case_dir / "rule",
                            "--max-model-calls", args.max_model_calls], log)
                        result["attempts"] = extract_attempts(case_dir / "rule", case_dir)
                    replay_target = case_dir / ("feature" if args.cpu_only else "rule")
                    if (replay_target / "run_manifest.json").is_file():
                        result["steps"]["replay"] = call_cli(cli, [
                            "replay", "--run", replay_target, "--input-manifest", paths["application"],
                            "--reference-manifest", paths["reference"], "--output", case_dir / "replay"], log)
            after = gpu_snapshot()
            result.update(ended_at=now(), gpu_before=before, gpu_after=after)
            changes = foreign_changes(before, after, cvd, args.foreign_gpu_delta_mib)
            write_json(case_dir / "case_result.json", result)
            run["cases"][case_id] = {k: v.get("exit") for k, v in result["steps"].items()}
            if changes:
                run["warnings"].append({"case": case_id, "foreign_gpu_memory_change": changes})
                print(f"WARNING {case_id}: other GPU memory changed {changes}")
            print(case_id, {k: v.get("exit") for k, v in result["steps"].items()})
            write_json(run_dir / "run.json", run)
            if changes and args.stop_on_foreign_gpu_change:
                run["status"] = "stopped_foreign_gpu_change"
                break
        else:
            run["status"] = "completed"
    except KeyboardInterrupt:
        run["status"] = "interrupted"
    finally:
        rule_logic.rule_system, cli.LocalModels = original_system, original_cli_models
        if models is not None:
            try:
                import torch
                run["max_memory_allocated_bytes"] = torch.cuda.max_memory_allocated()
            except Exception as exc:  # 기록 실패가 실행 결과를 바꾸지 않게
                run["max_memory_allocated_bytes"] = f"unavailable:{type(exc).__name__}"
        run["ended_at"] = now()
        write_json(run_dir / "run.json", run)
    if not args.no_judge:
        import judge
        judge.judge_run(run_dir)
    print("run dir:", run_dir)
    return 0 if run["status"] == "completed" else 1


if __name__ == "__main__":
    _utf8_reexec()
    raise SystemExit(main())
