"""Explicit admission callbacks for future adapters; no old execution rewrite."""
from common_io import ROOT
from generalized_source_policy_v21 import assert_source_admission,assert_source_paths
from generalized_content_policy_v16 import assert_profile_admission

VERSION='v25-source-stage-gate-1.0.0'

def guarded(stage,policy,action,*,inventory=None,pins=None):
    if stage not in {'profile','model_input','measurement','baseline','teacher'}:raise ValueError('Explicit supported stage required')
    if inventory is None:
        receipt=assert_source_paths(pins,policy,stage,project_root=ROOT)
    else:
        receipt=assert_source_admission(inventory,policy,stage,project_root=ROOT)
        assert_profile_admission(inventory)
    # No statistics, model projection, reread or teacher action before receipt.
    return action(),{'version':VERSION,'stage':stage,'source_receipt':receipt,'callback_executed_after_admission':True}
