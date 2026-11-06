from .scope import ACTIVE_TASKS, EXCLUDED_TASKS, evaluation_tasks


def test_default_scope_has_only_four_execution_scenarios():
    assert evaluation_tasks() == ACTIVE_TASKS
    assert len(ACTIVE_TASKS) == 4
    assert 'input_stop' not in ACTIVE_TASKS
    assert 'input_stop' in EXCLUDED_TASKS


def test_old_silence_benchmark_requires_explicit_opt_in():
    assert set(evaluation_tasks(True)) == {*ACTIVE_TASKS, 'input_stop'}
