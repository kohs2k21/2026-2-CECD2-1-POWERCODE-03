"""Temporal changes of COUNT-CONDITIONED residuals, not absolute duration."""
from model_research_v15.features import augment as observed_temporal


def augment(frame):
    original_duration = frame.log_max_duration_1m.copy()
    result = observed_temporal(frame.assign(log_max_duration_1m=frame.residual_mean_1m))
    result["log_max_duration_1m"] = original_duration.to_numpy()
    return result
