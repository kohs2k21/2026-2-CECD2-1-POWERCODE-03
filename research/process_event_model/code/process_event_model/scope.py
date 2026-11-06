"""The active row-prediction contract, separate from archived benchmarks."""

ACTIVE_TASKS = (
    "individual_delay", "group_delay", "time_burst", "gradual_degradation",
)
EXCLUDED_TASKS = {"input_stop": "User excluded silence detection; no clock monitor is added."}


def evaluation_tasks(include_input_stop=False):
    return (*ACTIVE_TASKS, "input_stop") if include_input_stop else ACTIVE_TASKS
