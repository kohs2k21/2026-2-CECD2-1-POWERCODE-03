"""프로토콜 경로 설명 시험 키트 공통: 사례 폴더 형식 `protocol-explain-export-v1`, 사실 다시 만들기, 파일 도구.

사례 폴더 = `index.json` + 사례마다 `source.json`(코드 산출물 원본: Trainer 초안 인터페이스 항목·룰 세트 항목 또는
Detector protocolReview) + `facts.json`(server/worker/protocol_explanation.py가 만든 모델 입력 사실, 별칭 적용).
실행기는 source.json으로 사실을 다시 만들어 facts.json과 같은지 확인한 뒤 facts.json만 모델에 넣는다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORKER = REPO / "server" / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

import protocol_explanation as pe  # noqa: E402

FORMAT = "protocol-explain-export-v1"
SOURCE_KINDS = ("observed_paths", "alert")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, default=str) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(value).encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def inside(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def build_facts(source: dict[str, Any], alias: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """source.json → (사실, 별칭 표). 집계는 하지 않는다(코드 산출물을 사실 형식으로 옮길 뿐)."""
    kind = source.get("kind")
    if kind == "observed_paths":
        return pe.observed_paths_facts(source["interfaceId"], source.get("draftInterface"), source.get("ruleEntry"),
                                       period=source.get("period"), parameters=source.get("parameters"), alias=alias)
    if kind == "alert":
        return pe.alert_facts(source["review"], source.get("ruleEntry"), alias=alias)
    raise ValueError("protocol_explain_source_kind_invalid")


def write_case(root: Path, case_id: str, source: dict[str, Any], alias: bool, extra: dict[str, Any] | None = None
               ) -> tuple[dict[str, Any], dict[str, Any]]:
    """사례 폴더 1개를 쓰고 (index 항목, 별칭 표)를 돌려준다."""
    facts, mapping = build_facts(source, alias)
    folder = root / case_id
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "source.json", source)
    write_json(folder / "facts.json", facts)
    entry = {"case_id": case_id, "kind": source["kind"], "path": case_id,
             "files": {name: sha256_file(folder / name) for name in ("source.json", "facts.json")}}
    entry.update(extra or {})
    return entry, mapping
