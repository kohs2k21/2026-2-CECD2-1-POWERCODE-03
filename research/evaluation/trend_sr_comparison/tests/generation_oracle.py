"""Specification values and interval oracles independent of generator code."""
from itertools import combinations

import pandas as pd


MAIN = tuple(f"SC-{family}{number:02}" for family in "BZLGR" for number in range(1, 4))
ERROR = ("SC-E01", "SC-E02", "SC-E03")
PULSES = {"SC-B03": (10, 3, 20), "SC-Z03": (5, 4, 20), "SC-E03": (5, 4, 20)}
SINGLE_LENGTHS = {
    "SC-B01": 5, "SC-B02": 120, "SC-Z01": 30, "SC-Z02": 120,
    "SC-L01": 60, "SC-L02": 5, "SC-L03": 120,
    "SC-G01": 180, "SC-G02": 180, "SC-G03": 180,
    "SC-R01": 180, "SC-R02": 180, "SC-R03": 180,
    "SC-E01": 60, "SC-E02": 5,
}
COMPOSITES = {
    "MX01": (("B02", 0, 120), ("L01", 0, 60)),
    "MX02": (("L01", 0, 60), ("E01", 0, 60)),
    "MX03": (("B02", 0, 120), ("L01", 30, 90)),
    "MX04": (("B02", 0, 120), ("L01", 90, 150)),
    "MX05": (("B02", 0, 120), ("L01", 180, 240)),
    "MX06": (("B02", 0, 120), ("L01", 0, 60)),
    "MX07": (("Z02", 0, 120), ("L01", 0, 60)),
    "MX08": (("B02", 0, 120), ("L01", 0, 60), ("E01", 0, 60)),
}


def expected_arms(size):
    letters = "ABC"[:size]
    return {"0"} | {"".join(part) for length in range(1, size + 1)
                    for part in combinations(letters, length)}


def active_rows(frame, events):
    """Truth is evaluated at bin start; endpoints cannot shift GT by one bin."""
    result = pd.Series(False, index=range(len(frame)))
    starts = pd.to_datetime(frame["bin_start"]).reset_index(drop=True)
    groups = frame["group"].reset_index(drop=True)
    for event in events.itertuples():
        result |= groups.eq(event.group) & starts.ge(event.start) & starts.lt(event.end)
    return result.to_numpy()


def connected_intervals(intervals):
    result = []
    for start, end in sorted(intervals):
        if start >= end:
            raise AssertionError("nonpositive truth interval")
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def component_segments(components, start, end):
    """Partition a single group's timeline, including normal and C-only spans."""
    boundaries = sorted({start, end} | {t for _, left, right in components
                                       for t in (max(start, left), min(end, right))
                                       if start <= t <= end})
    return [(left, right, frozenset(name for name, a, b in components
                                   if a < right and b > left))
            for left, right in zip(boundaries, boundaries[1:]) if left < right]
