"""Load one independent case without feeding oracle columns to a detector."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_case(dataset, case_id, *, normal=False, chunksize=100_000):
    """Return the unchanged 14-column Process input for one case.

    Observed IDs come from row labels. A normal counterpart also includes
    executions removed by input_stop, whose IDs exist only in event metadata.
    Do not concatenate separate cases into one traffic time series.
    """
    dataset = Path(dataset)
    transaction_ids = set()
    for labels in pd.read_csv(dataset/"process_labels.csv",encoding="utf-8-sig",dtype="string",chunksize=chunksize):
        transaction_ids.update(labels.loc[labels.case_id.eq(case_id),"transactionId"])
    cases = pd.read_csv(dataset/"cases.csv",encoding="utf-8-sig",dtype="string")
    if case_id not in set(cases.case_id):
        raise KeyError(f"Unknown case_id: {case_id}")
    if normal:
        events = pd.read_csv(dataset/"events.csv",encoding="utf-8-sig",dtype="string")
        for evidence in events.loc[events.case_id.eq(case_id)&events.scenario.eq("input_stop"),"evidence_json"]:
            transaction_ids.update(json.loads(evidence)["missing_transaction_ids"])
        # Burst-only executions must NOT appear in the normal counterpart.
        filename = "normal_processes.csv"
    else:
        filename = "processes.csv"
    parts = []
    for rows in pd.read_csv(dataset/filename,encoding="utf-8-sig",dtype="string",chunksize=chunksize):
        selected = rows.loc[rows.transactionId.isin(transaction_ids)]
        if len(selected):
            parts.append(selected)
    if not parts:
        raise ValueError("Case has no observed rows; use its event/window labels instead")
    # These are completed S logs: replay in completion order, not CSV storage order.
    return pd.concat(parts,ignore_index=True).sort_values(["endTime","transactionId","processId"]).reset_index(drop=True)
