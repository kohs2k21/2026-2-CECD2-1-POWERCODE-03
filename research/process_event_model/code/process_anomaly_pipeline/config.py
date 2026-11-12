"""Explicit, validated experiment defaults, not claimed optimal parameters."""
from dataclasses import asdict, dataclass, fields
import math


@dataclass(frozen=True)
class PipelineConfig:
    version: str = "process-if-v1"
    n_estimators: int = 128
    max_samples: int = 512
    max_features: float = 1.0
    bootstrap: bool = False
    n_jobs: int = 2
    random_state: int = 42
    max_train_rows: int = 100_000
    max_calibration_rows: int = 100_000
    score_batch_rows: int = 50_000
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    score_quantile: float = 0.99
    global_weight: float = 0.5
    min_global_train: int = 200
    min_global_calibration: int = 200
    min_global_threshold: int = 200
    min_adapter_train: int = 500
    min_adapter_calibration: int = 100
    min_adapter_threshold: int = 100
    min_adapter_days: int = 3
    quantiles: tuple = (0.99, 0.999)
    quantile_columns: tuple = ("duration_ms", "totalCount", "retryCount")
    min_p99_support: int = 1000
    min_p999_support: int = 10_000
    time_tolerance_ms: float = 0.0
    exclude_statistical_from_training: bool = True

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        integers = ("n_estimators", "max_samples", "n_jobs", "max_train_rows",
                    "max_calibration_rows", "score_batch_rows", "random_state",
                    "min_global_train", "min_global_calibration", "min_global_threshold",
                    "min_adapter_train", "min_adapter_calibration", "min_adapter_threshold",
                    "min_adapter_days", "min_p99_support", "min_p999_support")
        for name in integers:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if name == "n_jobs":
                if value == 0 or value < -1:
                    raise ValueError("n_jobs must be -1 or positive")
            elif value < (0 if name == "random_state" else 1):
                raise ValueError(f"Invalid {name}")
        if self.max_train_rows < self.min_global_train:
            raise ValueError("max_train_rows must cover min_global_train")
        for name in ("bootstrap", "exclude_statistical_from_training"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be bool")
        for name in ("max_features", "train_fraction", "validation_fraction",
                     "score_quantile", "global_weight"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"Invalid {name}")
            if not 0 < v <= 1 or (name != "max_features" and v == 1):
                raise ValueError(f"Invalid {name}")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("A nonempty chronological test fraction is required")
        if isinstance(self.time_tolerance_ms, bool) or not isinstance(self.time_tolerance_ms, (float, int)) or not math.isfinite(self.time_tolerance_ms) or self.time_tolerance_ms < 0:
            raise ValueError("time_tolerance_ms must be finite and nonnegative")
        if not self.quantiles or any(q not in (0.99, 0.999) for q in self.quantiles):
            raise ValueError("quantiles must contain p99 and/or p99.9")
        if len(set(self.quantiles)) != len(self.quantiles):
            raise ValueError("Duplicate quantiles")
        if not self.quantile_columns or not set(self.quantile_columns).issubset(
                {"duration_ms", "totalCount", "retryCount"}):
            raise ValueError("Unsupported quantile_columns")
        if len(set(self.quantile_columns)) != len(self.quantile_columns):
            raise ValueError("Duplicate quantile_columns")

    @classmethod
    def from_dict(cls, values=None):
        values = dict(values or {})
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown configuration: {sorted(unknown)}")
        for key in ("quantiles", "quantile_columns"):
            if key in values:
                values[key] = tuple(values[key])
        return cls(**values)

    def to_dict(self):
        return asdict(self)
