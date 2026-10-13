"""Failure-state fixture for the agreed modes, separate from quality fusion.

This research harness tests finalization; it is not an operational worker or
an adopted alert/severity policy. Event-level path union lives in evaluation.
"""
from concurrent.futures import TimeoutError
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class Result:
    status: str
    hit: bool | None = None
    reason: str | None = None

    def __post_init__(self):
        if self.status not in ("available", "unavailable", "timeout", "failed"):
            raise ValueError("Invalid result status")
        if self.status == "available" and type(self.hit) is not bool:
            raise ValueError("Available result requires a boolean")
        if self.status != "available" and self.hit is not None:
            raise ValueError("Missing inference cannot be a normal/anomaly result")


def infer_with_deadline(executor, score, timeout_seconds):
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Positive finite timeout required")
    future = executor.submit(score)
    try:
        hit = future.result(timeout=timeout_seconds)
        return Result("available", hit)
    except TimeoutError:
        if future.done() and isinstance(future.exception(), TimeoutError):
            return Result("failed", reason="TimeoutError")
        future.cancel()
        return Result("timeout", reason="inference_deadline")
    except Exception as error:
        return Result("failed", reason=type(error).__name__)


class DecisionGate:
    def __init__(self, mode, deadline, *, ml_ready=True):
        if mode not in ("rule_only", "ml_only", "parallel") or not math.isfinite(deadline):
            raise ValueError("Valid mode and finite deadline required")
        self.mode, self.deadline = mode, deadline
        self.selected = ("rule",) if mode == "rule_only" else ("ml",) if mode == "ml_only" else ("rule", "ml")
        self.fallback_rule_only = mode == "parallel" and not ml_ready
        if self.fallback_rule_only:
            self.selected = ("rule",)
        self.results = {}
        self.final = None
        self.clock = None
        if not ml_ready and "ml" in self.selected:
            self.results["ml"] = Result("unavailable", reason="model_not_ready")

    def accept(self, path, result, observed_at):
        if path not in self.selected or not isinstance(result, Result) or not math.isfinite(observed_at):
            raise ValueError("Invalid path/result/observation")
        if self.final is not None or observed_at > self.deadline or path in self.results:
            return False
        self._advance(observed_at)
        self.results[path] = result
        return True

    def _advance(self, clock):
        if self.clock is not None and clock < self.clock:
            raise ValueError("Observation clock cannot move backwards")
        self.clock = clock

    def finalize(self, clock):
        if not math.isfinite(clock):
            raise ValueError("Finite clock required")
        self._advance(clock)
        if self.final is not None:
            return self.final
        if clock < self.deadline and any(path not in self.results for path in self.selected):
            return None
        for path in self.selected:
            self.results.setdefault(path, Result("timeout", reason="decision_deadline"))
        available = [result for result in self.results.values() if result.status == "available"]
        missing = len(available) != len(self.selected)
        # A known hit is final; a partial negative cannot certify full normality.
        hit = any(result.hit for result in available)
        if hit:
            self.final = Result("available", True)
        elif missing:
            self.final = Result("unavailable", reason="partial_or_no_coverage")
        else:
            self.final = Result("available", False)
        return self.final

    def snapshot(self):
        return {"mode": self.mode, "deadline": self.deadline,
                "clock": self.clock,
                "fallback_rule_only": self.fallback_rule_only,
                "results": {key: asdict(value) for key, value in self.results.items()},
                "final": asdict(self.final) if self.final else None}

    @classmethod
    def restore(cls, payload):
        gate = cls(payload["mode"], payload["deadline"], ml_ready=not payload.get("fallback_rule_only", False))
        if set(payload["results"]) - set(gate.selected):
            raise ValueError("Unexpected result path")
        gate.results = {key: Result(**value) for key, value in payload["results"].items()}
        gate.final = Result(**payload["final"]) if payload["final"] else None
        gate.clock = payload.get("clock")
        return gate
