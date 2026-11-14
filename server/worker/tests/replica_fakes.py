"""`generation_replicas` 시험용 가짜 적재기·runtime(torch·GPU 없음). spawn 자식이 import하므로 의존을 최소로 둔다."""
from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

SHA = "a" * 64


class InputFailure(ValueError):
    pass


class ModelFailure(RuntimeError):
    pass


PACKAGE = SimpleNamespace(io=SimpleNamespace(InputFailure=InputFailure, ModelFailure=ModelFailure))
_ERRORS = {"timeout": lambda: TimeoutError("cut"), "input": lambda: InputFailure("bad input"),
           "model": lambda: ModelFailure("bad model"), "other": lambda: KeyError("boom")}


class FakeRuntime:
    def __init__(self, adapter_sha: str = SHA):
        self.adapter_sha = adapter_sha
        self.package = PACKAGE

    def run_field(self, paths, out):
        action = paths.get("raise")
        if action == "exit":
            os._exit(3)  # 자식이 생성 중에 죽은 경우
        if action in _ERRORS:
            raise _ERRORS[action]()
        return {"result": {"gpu": os.environ.get("CUDA_VISIBLE_DEVICES"), "value": Decimal("1.50"),
                           "out": str(out)}, "attempts": [{"adapterSha": self.adapter_sha}]}


def load(roots):
    """GPU 번호로 결과를 고른다: 1 = 정상, 2 = 적재 실패, 3 = 다른 어댑터, 그 밖 = 정상."""
    gpu = os.environ.get("CUDA_VISIBLE_DEVICES")
    if gpu == "2":
        raise RuntimeError("qwen_base_metadata_mismatch")
    if gpu == "3":
        return FakeRuntime("b" * 64)
    return FakeRuntime()
