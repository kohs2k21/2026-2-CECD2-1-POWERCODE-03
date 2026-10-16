"""Pre-registered experiment configuration, not an optimized model policy."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class ExperimentConfig:
    version: str = "process-synthetic-lab-v1"
    seeds: tuple = (11, 23, 47)
    delay_multipliers: tuple = (1.5, 2.0, 3.0)
    target_rows: int = 10
    min_train_rows: int = 200
    min_threshold_rows: int = 30
    min_adapter_rows: int = 20
    train_fraction: float = 0.7
    validation_fraction: float = 0.15
    model_seed: int = 42
    threshold_quantile: float = 0.99
    failure_status: str | None = None
    failure_response_code: str | None = None
    failure_semantics_evidence: str | None = None
    parent_policy: str = "strict_parent"
    scenarios: tuple = ("P01", "P02", "P03")
    count_increments: tuple = (1, 2, 3)
    count_conservation_contract: bool = False
    run_noop_control: bool = False
    run_adbench: bool = True

    def __post_init__(self):
        for name in ("target_rows", "min_train_rows", "min_threshold_rows", "min_adapter_rows"):
            v = getattr(self, name)
            if type(v) is not int or v < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not self.seeds or len(set(self.seeds)) != len(self.seeds) or any(type(v) is not int or not 0 <= v < 2**32 for v in self.seeds):
            raise ValueError("seeds must be unique nonnegative uint32 integers")
        if type(self.model_seed) is not int or not 0 <= self.model_seed < 2**32:
            raise ValueError("invalid model_seed")
        if not self.delay_multipliers or any(isinstance(v, bool) or not math.isfinite(v) or v <= 1 for v in self.delay_multipliers):
            raise ValueError("delay_multipliers must be finite and > 1")
        for name in ("train_fraction", "validation_fraction", "threshold_quantile"):
            v = getattr(self, name)
            if isinstance(v, bool) or not math.isfinite(v) or not 0 < v < 1:
                raise ValueError(f"invalid {name}")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("test fraction must be positive")
        if self.failure_status not in (None, "F"):
            raise ValueError("only existing status_failed rule's F is supported")
        if self.failure_status and not self.failure_semantics_evidence:
            raise ValueError("failure status requires a documented semantics source")
        if self.failure_response_code and not self.failure_status:
            raise ValueError("failure_response_code requires confirmed failure status")
        if type(self.run_adbench) is not bool:
            raise ValueError("run_adbench must be bool")
        if self.parent_policy not in ("strict_parent", "propagate_parent_end"):
            raise ValueError("parent_policy must be strict_parent or propagate_parent_end")
        if (not self.scenarios or len(set(self.scenarios)) != len(self.scenarios)
                or not set(self.scenarios).issubset({"P01", "P02", "P03", "P04"})):
            raise ValueError("scenarios must be unique supported scenario IDs")
        if not self.count_increments or any(type(v) is not int or not 1 <= v < 2**53 for v in self.count_increments):
            raise ValueError("count_increments must be positive exactly representable integers")
        if type(self.count_conservation_contract) is not bool or type(self.run_noop_control) is not bool:
            raise ValueError("count_conservation_contract and run_noop_control must be bool")

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        for name in ("seeds", "delay_multipliers", "scenarios", "count_increments"):
            if name in value:
                value[name] = tuple(value[name])
        return cls(**value)

    def to_dict(self):
        return asdict(self)
