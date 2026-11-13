"""Rule v3 운영 후보 이미지 GPU 확인(일회성). 서비스 구성 그대로 모델을 올리고 rule_v3 경로를 실제로 돌린다.

출력은 표준 출력의 JSON 요약 하나이며 값(임계값·p95·근거 문장·행 값)을 담지 않는다: 상태·사유 코드·시도 수·초·
토큰 수·참/거짓·최대 VRAM·행 개수만. DB에는 쓰지 않는다(실데이터 단계는 읽기 전용 트랜잭션).

단계
1. synthetic: 고정 시드 합성 행(보류 창 뒤 날짜)으로 필드 6종 rule_v3 추천 1회
2. rule_v2: 같은 합성 행으로 기존 rule_v2 경로 1필드(Rule v3 어댑터를 올린 뒤에도 기존 어댑터가 도는지)
3. explain: 설명 생성 1회(어댑터 끈 Base)
4. max_time probe(`--max-time-probe 초`, 0이면 생략): 상한을 짧게 두고 1필드 → `generation_timeout`과 시도 초로
   `generation_config.max_time` 적용 여부를 본다
5. (선택) real: `--snapshot-id`와 `DATABASE_URL`이 있으면 그 스냅샷 train 멤버로 필드 6종 rule_v3 추천

일요일 실행 예(서버 값은 운영 세션이 채움, 저장소에 적지 않음):

    docker build -f server/worker/Dockerfile.generation -t powercode-llm:rulev3-cand .     # latest 덮어쓰지 않음
    python3 server/worker/research_bundle.py --rule-v3 --output $W/rule-v3-runtime          # 새 폴더(CPU)
    docker run --rm --gpus device=$GPU --network none --read-only --tmpfs /tmp:rw,size=8g \\
      -e CUDA_VISIBLE_DEVICES=0 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
      -e HOME=/tmp -e HF_HOME=/tmp/hf -e XDG_CACHE_HOME=/tmp/cache \\
      -e RESEARCH_BUNDLE_ROOT=/research-runtime -e RULE_V3_BUNDLE_ROOT=/rule-v3-runtime \\
      -e RULE_MODEL_BASE_PATH=/qwen-base \\
      -v $RESEARCH_BUNDLE_PATH:/research-runtime:ro -v $W/rule-v3-runtime:/rule-v3-runtime:ro \\
      -v $QWEN_BASE_PATH:/qwen-base:ro \\
      powercode-llm:rulev3-cand python -m rule_v3_check --max-time-probe 10 > $W/rule-v3-check-synthetic.json
    # 실데이터(선택): --network <DB 네트워크> -e DATABASE_URL(읽기 전용 역할 권장) ... --snapshot-id <UUID>

운영 배포(이 확인 뒤)는 compose override로 켠다: `docker compose -f compose.yaml -f compose.rule-v3.yaml up -d llm`.
컨테이너는 운영 llm 컨테이너와 별개이고 운영 DB에 쓰지 않는다. 할당 GPU 여유가 20GB 미만이면 시작하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

FIELDS = ("elapsed_ms", "error_count", "error_ratio", "retry_count", "success_count", "total_count")
HASH = "0" * 64


def synthetic_rows(count: int = 200, seed: int = 20261115) -> list[tuple[Any, dict[str, Any], str, None]]:
    """보류 창 뒤(2026-10-10 KST) 날짜의 합성 PROCESS 행. 모든 필드가 분산을 가져 LLM을 부른다."""
    rng = random.Random(seed)
    rows = []
    start = datetime(2026, 10, 10, 9, 0)
    for index in range(count):
        begin = start + timedelta(minutes=index, seconds=rng.randint(0, 50))
        total = rng.randint(800, 5000)
        errors = rng.choice((0, 0, 0, 1, 2, 3))
        raw = {"status": "S", "transaction_id": f"syn{index:05d}", "start_time_raw": begin.isoformat(),
               "end_time_raw": (begin + timedelta(milliseconds=int(rng.lognormvariate(6.0, 0.6)))).isoformat(),
               "total_count": str(total), "success_count": str(total - errors), "error_count": str(errors),
               "retry_count": str(rng.choice((0, 0, 0, 0, 1, 2)))}
        rows.append((uuid.UUID(int=rng.getrandbits(128)), raw, "train", None))
    return rows


def plan_for(provider: str, fields=FIELDS):
    from threshold_recommendation import ThresholdPlan, MODE_DATA_P95
    targets = {field: [{"id": f"soft:{field}-check", "field": field, "threshold": None}] for field in fields}
    return ThresholdPlan(MODE_DATA_P95, {"revision": 0, "contentHash": HASH, "contentHashVersion": 2},
                         targets, provider)


def error_summary(exc: BaseException) -> dict[str, Any]:
    text = str(exc).splitlines()[0][:200] if str(exc) else ""
    return {"error": type(exc).__name__, "code": text if text.replace("_", "").replace(":", "").isalnum() else None,
            "where": [f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)[-4:]]}


def run_v3(model, rows, snapshot_id, source_tz=None, fields=FIELDS) -> dict[str, Any]:
    """rule_v3 추천 1회. 필드별 시도 기록·CPU 초를 값 없이 모은다."""
    import torch
    from threshold_recommendation import recommend
    runtime = model.rule_v3
    per_field: dict[str, dict[str, Any]] = {}
    original = runtime.run_field

    def recorded(paths, out):
        field = Path(paths["proposal"]).parent.parent.name
        started = time.monotonic()
        try:
            return original(paths, out)
        finally:
            attempts = list(runtime.models.records)
            total = time.monotonic() - started
            generated = sum(float(a.get("seconds") or 0) for a in attempts)
            per_field[field] = {"attemptRecords": [{k: a.get(k) for k in ("seconds", "outputTokens", "lengthLimitHit",
                                                                     "parseError", "timedOut", "limitSeconds")}
                                             for a in attempts],
                                "fieldSeconds": round(total, 1), "cpuSeconds": round(total - generated, 1)}

    runtime.run_field = recorded
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    try:
        result = recommend(plan_for("rule_v3", fields), rows, snapshot_id, "persisted_at", source_tz=source_tz)
    except BaseException as exc:  # 확인 결과로 남기고 다음 단계로 간다
        return {"status": "failed", **error_summary(exc), "fields": per_field,
                "seconds": round(time.monotonic() - started, 1), "maxAllocatedBytes": torch.cuda.max_memory_allocated()}
    finally:
        runtime.run_field = original
    from rule_v3_runtime import hangul_ratio
    items = []
    for item in result["items"]:
        items.append({"field": item["field"], "status": item["status"], "rejectedReason": item.get("rejectedReason"),
                      "attempts": item.get("attempts"), "reviews": item.get("reviews"),
                      "rationaleKorean": None if not item.get("rationale") else float(hangul_ratio(item["rationale"])) >= 0.5,
                      **per_field.get(item["field"], {})})
    return {"status": "completed", "counts": result["counts"], "provider": result["provider"],
            "referenceRows": result["data"].get("referenceRows"), "currentRows": result["data"].get("currentRows"),
            "items": items, "seconds": round(time.monotonic() - started, 1),
            "maxAllocatedBytes": torch.cuda.max_memory_allocated(), "maxReservedBytes": torch.cuda.max_memory_reserved()}


def run_v2(rows) -> dict[str, Any]:
    from threshold_recommendation import recommend
    started = time.monotonic()
    try:
        result = recommend(plan_for("rule_v2", ("elapsed_ms",)), rows, uuid.uuid4(), "persisted_at")
    except BaseException as exc:
        return {"status": "failed", **error_summary(exc)}
    item = result["items"][0]
    return {"status": "completed", "itemStatus": item["status"], "rejectedReason": item.get("rejectedReason"),
            "seconds": round(time.monotonic() - started, 1)}


def run_explain(model) -> dict[str, Any]:
    started = time.monotonic()
    try:
        output = model.generate("explain", {"facts": [{"id": "f1", "text": "synthetic check fact"}],
                                            "checks": ["review_rule_settings"]}, report_timeout=True)
    except BaseException as exc:
        return {"status": "failed", **error_summary(exc), "seconds": round(time.monotonic() - started, 1)}
    return {"status": "completed", "truncated": output.get("truncated"), "seconds": round(time.monotonic() - started, 1)}


def run_probe(model, rows, seconds: float) -> dict[str, Any]:
    """짧은 상한으로 1필드. 상한 근처에서 `generation_timeout`이면 max_time이 적용된 것이다."""
    previous = os.environ.get("LLM_RECOMMENDATION_SECONDS")
    os.environ["LLM_RECOMMENDATION_SECONDS"] = str(seconds)
    try:
        outcome = run_v3(model, rows, uuid.uuid4(), fields=("elapsed_ms",))
    finally:
        if previous is None:
            os.environ.pop("LLM_RECOMMENDATION_SECONDS", None)
        else:
            os.environ["LLM_RECOMMENDATION_SECONDS"] = previous
    attempts = (outcome.get("fields", {}).get("elapsed_ms") or (outcome.get("items") or [{}])[0]).get("attemptRecords") or []
    first = attempts[0] if attempts else {}
    applied = outcome.get("code") == "generation_timeout" and first.get("seconds") is not None and \
        float(first["seconds"]) < seconds + 30
    took = float(first["seconds"]) if first.get("seconds") is not None else None
    # 상한을 넘겨 끝까지 생성했으면 적용 안 됨, 상한 전에 끝났으면 판단 불가(상한을 더 줄여 다시).
    verdict = "applied" if applied else "not_applied" if took is not None and took >= seconds + 30 else "inconclusive"
    return {"limitSeconds": seconds, "outcome": outcome.get("code") or outcome.get("status"),
            "firstAttemptSeconds": first.get("seconds"), "maxTimeApplied": applied, "verdict": verdict,
            "attempts": len(attempts)}


def read_snapshot(snapshot_id: uuid.UUID):
    """읽기 전용 트랜잭션으로 train 멤버와 원본 시간대만 읽는다."""
    import psycopg
    from rule_v3_capture import source_timezone
    from trainer.runner import _snapshot_rows
    with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as conn:
        rows = _snapshot_rows(conn, snapshot_id, "train")
        with conn.cursor() as cur:
            cur.execute("""SELECT s.clock_basis FROM data_snapshots AS d JOIN ops_sources AS s ON s.id = d.source_id
                            WHERE d.id = %s""", (snapshot_id,))
            found = cur.fetchone()
        conn.rollback()
    return rows, source_timezone(found[0] if found else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-time-probe", type=float, default=10.0, help="seconds; 0 skips the probe")
    parser.add_argument("--snapshot-id", type=uuid.UUID, help="optional real snapshot (DATABASE_URL, read-only)")
    parser.add_argument("--skip-synthetic", action="store_true")
    args = parser.parse_args(argv)
    import torch
    import generation_client
    from generation_service import DesignModel
    from importlib import metadata
    summary: dict[str, Any] = {"format": "rule-v3-gpu-check-v1", "startedAt": datetime.now().astimezone().isoformat(),
                               "python": sys.version.split()[0],
                               "libraries": {name: _version(metadata, name) for name in (
                                   "torch", "transformers", "peft", "bitsandbytes", "accelerate", "xgrammar",
                                   "jsonschema", "huggingface-hub", "triton")},
                               "cudaVisibleDevices": os.environ.get("CUDA_VISIBLE_DEVICES")}
    started = time.monotonic()
    model = DesignModel(Path(os.environ["RESEARCH_BUNDLE_ROOT"]), Path(os.environ["RULE_MODEL_BASE_PATH"]),
                        Path(os.environ["RULE_V3_BUNDLE_ROOT"]))
    generation_client.set_local_model(model)
    summary["modelLoadSeconds"] = round(time.monotonic() - started, 1)
    summary["gpuName"] = torch.cuda.get_device_name(0)
    summary["afterLoadAllocatedBytes"] = torch.cuda.memory_allocated()
    # 서비스는 v3를 붙이지 못해도 뜬다. 확인에서는 그 사유를 남기고 v3 단계를 건너뛴다.
    summary["ruleV3Attached"] = model.rule_v3 is not None
    summary["ruleV3Error"] = model.rule_v3_error
    rows = synthetic_rows()
    if model.rule_v3 is None:
        summary["ruleV2"] = run_v2(rows)
        summary["explain"] = run_explain(model)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    if not args.skip_synthetic:
        summary["synthetic"] = run_v3(model, rows, uuid.uuid4())
        summary["ruleV2AfterV3"] = run_v2(rows)
        summary["explainAfterV3"] = run_explain(model)
    if args.max_time_probe > 0:
        summary["maxTimeProbe"] = run_probe(model, rows, args.max_time_probe)
    if args.snapshot_id:
        try:
            real_rows, source_tz = read_snapshot(args.snapshot_id)
            summary["real"] = {"trainRows": len(real_rows),
                               **run_v3(model, real_rows, args.snapshot_id, source_tz=source_tz)}
        except BaseException as exc:
            summary["real"] = {"status": "failed", **error_summary(exc)}
    peaks = [summary["afterLoadAllocatedBytes"], torch.cuda.max_memory_allocated()]
    peaks += [stage.get("maxAllocatedBytes") for stage in (summary.get("synthetic"), summary.get("real")) if stage]
    summary["maxAllocatedBytesOverall"] = max(value for value in peaks if isinstance(value, int))
    summary["endedAt"] = datetime.now().astimezone().isoformat()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _version(metadata, name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
