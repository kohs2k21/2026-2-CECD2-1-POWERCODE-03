import numpy as np

from .models import metrics
from .refine_four_tasks import search_cutoffs
from .scope import ACTIVE_TASKS


def test_refinement_preserves_cold_cuts_and_feasible_perfect_baseline():
    y = np.tile([False, False, False, False, True, True, True, True], 4)
    score = np.tile([0, .1, .2, .3, 1.1, 1.2, 1.3, 1.4], 4)
    groups = np.tile([0, 0, 4, 4, 0, 0, 4, 4], 4)
    tasks = np.repeat(np.arange(4), 8)
    initial = np.ones(8)
    baseline = {t:metrics(y[tasks==i],score[tasks==i]>1) for i,t in enumerate(ACTIVE_TASKS)}
    cuts, objective, safe = search_cutoffs(groups,score,tasks,y,baseline,initial,
                                          np.zeros(12,dtype=int),np.zeros(12))
    np.testing.assert_array_equal(cuts[4:],initial[4:])
    assert safe and objective == 1.
    np.testing.assert_array_equal(score>cuts[groups],y)
