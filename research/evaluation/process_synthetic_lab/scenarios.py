"""Frozen train-derived ESB mutations with parent/dependency bounds checks."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from process_anomaly_pipeline.data import JOIN_KEYS
from .data import reference_mask


@dataclass
class Generation:
    frame: pd.DataFrame
    truth: pd.DataFrame
    changes: pd.DataFrame
    rejects: pd.DataFrame
    metadata: dict
    parent_changes: pd.DataFrame


def adapter_baselines(train):
    ref = train.loc[reference_mask(train)]
    return {str(key): float(group.duration_ms.median())
            for key, group in ref.groupby("adapterType", observed=True, dropna=True)
            if len(group) and np.isfinite(group.duration_ms.median())}


def safe_end_limits(frame, *, allow_parent_extension=False):
    """Dependency bounds never move; optional parent-end propagation is explicit."""
    limits = (pd.Series(pd.Timestamp.max, index=frame.index) if allow_parent_extension
              else frame.parentEndTime.copy())
    # Existing dependent PROCESS must not begin before the prolonged predecessor ends.
    dependencies = frame.loc[frame.dependProcessId.notna(), [*JOIN_KEYS, "dependProcessId", "startTime"]]
    if len(dependencies):
        grouped = dependencies.groupby([*JOIN_KEYS, "dependProcessId"], observed=True).startTime
        caps = grouped.min().rename("dependencyStart")
        unknown = grouped.agg(lambda values: values.isna().any()).rename("unknownDependencyStart")
        keys = frame[[*JOIN_KEYS, "processId"]].rename(columns={"processId": "dependProcessId"})
        joined = keys.join(caps, on=[*JOIN_KEYS, "dependProcessId"]).join(unknown, on=[*JOIN_KEYS, "dependProcessId"])
        limits = pd.concat([limits, joined.dependencyStart], axis=1).min(axis=1)
        limits.loc[frame.parentEndTime.isna()] = pd.NaT
        limits.loc[joined.unknownDependencyStart.fillna(False).astype(bool)] = pd.NaT
    return limits


def propagate_parent_ends(base, output, changed_indices):
    """Return exact parent overlay journal; every copied sibling sees one end.

    Original TRANSACTION input stays immutable. Reconstruct evaluated parents by
    joining this journal to the original parent input on JOIN_KEYS and replacing
    endTime with after. Unlisted parents and all other fields remain unchanged.
    """
    journal = []
    touched = output.loc[changed_indices, list(JOIN_KEYS)].drop_duplicates()
    for values in touched.itertuples(index=False, name=None):
        mask = pd.Series(True, index=output.index)
        for key, value in zip(JOIN_KEYS, values):
            mask &= output[key].eq(value).fillna(False)
        original_end = base.loc[mask, "parentEndTime"].iloc[0]
        new_end = max(original_end, output.loc[mask, "endTime"].max())
        if new_end > original_end:
            output.loc[mask, "parentEndTime"] = new_end
            journal.append({**dict(zip(JOIN_KEYS, values)), "field": "endTime",
                            "before": str(original_end), "after": str(new_end),
                            "affected_process_rows": int(mask.sum())})
    return pd.DataFrame(journal, columns=[*JOIN_KEYS, "field", "before", "after", "affected_process_rows"])


def generate(test, baselines, config, *, scenario, severity, seed):
    if scenario not in ("P01", "P02", "P03"):
        raise ValueError("Unknown PROCESS scenario")
    if severity not in range(len(config.delay_multipliers)):
        raise ValueError("Invalid severity")
    base = test.copy(deep=True).reset_index(drop=True)
    output = base.copy(deep=True)
    multiplier = config.delay_multipliers[severity]
    rng = np.random.default_rng(seed)
    changes, rejected, accepted = [], [], []
    meta = {"scenario": scenario, "severity": severity + 1, "seed": seed,
            "delay_multiplier": multiplier, "requested_rows": config.target_rows,
            "parent_policy": config.parent_policy,
            "scenario_meaning": ("process_delay_with_parent_completion_propagation" if scenario != "P01" and config.parent_policy == "propagate_parent_end"
                                 else "process_mutation_with_fixed_parent"),
            "truth_meaning": "controlled_injected_deviation_not_proven_customer_incident",
            "selection": "before_scores_no_detector_output_used",
            "status": "completed"}
    if scenario == "P01" and not config.failure_status:
        meta.update(status="unsupported", reason="Failure status semantics not confirmed")
        return Generation(output, pd.DataFrame(columns=["row_id", "label", "scenario"]), pd.DataFrame(),
                          pd.DataFrame([{"row_id": None, "reason": meta["reason"]}]), meta,
                          pd.DataFrame(columns=[*JOIN_KEYS, "field", "before", "after", "affected_process_rows"]))
    eligible = reference_mask(base)
    limits = safe_end_limits(base, allow_parent_extension=config.parent_policy == "propagate_parent_end")
    context_bad = ((base.startTime.notna() & base.parentStartTime.notna() & base.startTime.lt(base.parentStartTime))
                   | (base.endTime.notna() & base.parentEndTime.notna() & base.endTime.gt(base.parentEndTime)))
    invalid_parent_context = context_bad.groupby([base[key] for key in JOIN_KEYS], dropna=False).transform("any")
    reasons = {}
    for idx in base.index:
        if not eligible.loc[idx]:
            reasons[idx] = "not_successful_quality_reference"
        elif pd.isna(base.at[idx, "adapterType"]) or str(base.at[idx, "adapterType"]) not in baselines:
            reasons[idx] = "unseen_or_missing_adapter_train_baseline"
        elif pd.isna(base.at[idx, "parentStartTime"]) or pd.isna(base.at[idx, "parentEndTime"]):
            reasons[idx] = "missing_or_ambiguous_parent"
        elif pd.isna(limits.loc[idx]):
            reasons[idx] = "unknown_dependent_process_start"
        elif config.parent_policy == "propagate_parent_end" and invalid_parent_context.loc[idx]:
            reasons[idx] = "existing_sibling_parent_time_violation"
        elif (base.at[idx, "startTime"] < base.at[idx, "parentStartTime"]
              or base.at[idx, "endTime"] > base.at[idx, "parentEndTime"]
              or base.at[idx, "endTime"] > limits.loc[idx]):
            reasons[idx] = "existing_parent_or_dependency_time_violation"
        elif scenario == "P01" and (pd.isna(base.at[idx, "totalCount"]) or base.at[idx, "totalCount"] < 1):
            reasons[idx] = "no_count_evidence_for_failure_mutation"
        elif scenario == "P01" and pd.notna(base.at[idx, "responseCode"]) and not config.failure_response_code:
            reasons[idx] = "failure_response_code_contract_missing"
    candidates = [idx for idx in base.index if idx not in reasons]
    if scenario == "P03":
        # Choose one consecutive block within an adapter/interface timeline. A
        # broken block is rejected as a whole, never relabelled as consecutive.
        blocks = []
        for _, group in base.groupby(["source_period", "adapterType", "parentInterfaceId"], dropna=False, observed=True):
            order = group.sort_values(["startTime", "row_id"], kind="stable").index.tolist()
            for start in range(max(0, len(order) - config.target_rows + 1)):
                block = order[start:start + config.target_rows]
                if all(idx not in reasons for idx in block):
                    blocks.append(block)
        selected = blocks[int(rng.integers(len(blocks)))] if blocks else []
    else:
        selected = rng.permutation(candidates)[:config.target_rows].tolist()
    planned = {}
    for idx in selected:
        row = base.loc[idx]
        if scenario == "P01":
            error_count = min(float(row.totalCount), max(1, round(float(row.totalCount) * (severity + 1) / 3)))
            values = {"status": config.failure_status, "errorCount": error_count,
                      "successCount": float(row.totalCount) - error_count}
            if config.failure_response_code:
                values["responseCode"] = config.failure_response_code
        else:
            # Increment over the selected duration using train-only adapter scale.
            # The multiplier never derives from SR quantiles or model scores.
            delta = max(1.0, baselines[str(row.adapterType)]) * (multiplier - 1)
            end = row.endTime + pd.to_timedelta(delta, unit="ms")
            if end > limits.loc[idx]:
                reasons[idx] = "insufficient_parent_or_dependency_headroom"
                continue
            values = {"endTime": end, "duration_ms": float((end - row.startTime).total_seconds() * 1000)}
        planned[idx] = values
    if scenario == "P03" and len(planned) != config.target_rows:
        for idx in selected:
            reasons.setdefault(idx, "consecutive_block_rejected_as_unit")
        planned = {}
    for idx, values in planned.items():
        for field, value in values.items():
            old = base.at[idx, field]
            output.at[idx, field] = value
            changes.append({"row_id": base.at[idx, "row_id"], "field": field,
                            "before": str(old), "after": str(value)})
        accepted.append({"row_id": base.at[idx, "row_id"], "label": 1, "scenario": scenario,
                         "adapter": str(base.at[idx, "adapterType"]),
                         "interface": str(base.at[idx, "parentInterfaceId"])})
    for idx, reason in reasons.items():
        rejected.append({"row_id": base.at[idx, "row_id"], "reason": reason,
                         "selected": idx in selected})
    parent_changes = (propagate_parent_ends(base, output, list(planned))
                      if config.parent_policy == "propagate_parent_end" and planned and scenario != "P01"
                      else pd.DataFrame(columns=[*JOIN_KEYS, "field", "before", "after", "affected_process_rows"]))
    meta.update(accepted_rows=len(accepted), selected_rows=len(selected), candidate_rows=len(candidates),
                changed_parent_rows=len(parent_changes),
                rejection_counts=pd.Series(list(reasons.values()), dtype="string").value_counts().astype(int).to_dict(),
                unfilled_requested_rows=config.target_rows - len(accepted),
                failure_semantics_evidence=config.failure_semantics_evidence if scenario == "P01" else None)
    if not accepted:
        meta["status"] = "no_valid_injection"
    return Generation(output, pd.DataFrame(accepted, columns=["row_id", "label", "scenario", "adapter", "interface"]),
                      pd.DataFrame(changes, columns=["row_id", "field", "before", "after"]),
                      pd.DataFrame(rejected, columns=["row_id", "reason", "selected"]), meta, parent_changes)
