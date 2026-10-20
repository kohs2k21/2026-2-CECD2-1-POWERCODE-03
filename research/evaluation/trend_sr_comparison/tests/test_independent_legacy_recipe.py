"""Clone R00 from immutable legacy feature code and independent SR arithmetic."""
import json
import hashlib
import pickle
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.evaluation.trend_sr_comparison import dataset, features as ft, methods
from research.evaluation.long_term_trend import features as legacy


def original_input(aggregates, split):
    frames, aliases = [], {}
    for group, a in aggregates.items():
        frame = a.source.copy()
        for key in ("bin_start", "endpoint", "available_at"):
            frame[key] = pd.DatetimeIndex(frame[key]).tz_convert("UTC")
        adapter, interface = "transport:"+group, "interface:"+group
        frame["adapter"], frame["interface"] = adapter, np.where(frame.category_supported, interface, "unseen")
        frame["status"], frame["split"] = "S", split
        frame["seed"] = 9501
        frame["minute_index"] = ((pd.DatetimeIndex(frame.bin_start)-a.start).total_seconds()/60).astype(int)
        aliases[json.dumps([adapter,interface],ensure_ascii=False)] = group
        frames.append(frame)
    joined = pd.concat(frames,ignore_index=True)
    joined.attrs["registry"] = {"aliases":aliases,"version":"synthetic_group_whitelist_v1"}
    joined.attrs["scored_end"] = max(a.end for a in aggregates.values())
    return joined


class LegacyRecipeContracts(unittest.TestCase):
    def test_r00_preserves_43_original_features_37_family_inputs_and_utc_calendar(self):
        train, selected = dataset.generate(9501,"train","normal"), dataset.generate(9501,"selection","normal")
        start = pd.Timestamp(selected.manifest["evaluation_start"])
        end = start+pd.Timedelta(days=2)
        aggs = ft.aggregate(train.frame,start=pd.Timestamp(train.manifest["evaluation_start"]),
                            end=pd.Timestamp(train.manifest["evaluation_end"]))
        test = ft.aggregate(selected.frame[selected.frame.bin_start < end],start=start,end=end)
        ref = methods.fit_method(aggs,methods.Configuration("R00",60),seed=9501)
        actual = methods.score_method(ref,test)
        protocol = json.loads(Path(legacy.__file__).with_name("protocol.json").read_text(encoding="utf-8"))
        protocol.update(max_window_minutes=1440,window_hours=[1.],cadence_candidates=[1,2,5,10,15,30,60])
        original_train, original_test = original_input(aggs,"train"), original_input(test,"selection")
        self.assertEqual(str(original_train.bin_start.dt.tz),"UTC")
        frozen = legacy.fit_reference(original_train,protocol)
        fitted = legacy.build_features(original_train,frozen,1.,5)
        pack = legacy.build_features(original_test,frozen,1.,5)
        x, valid = fitted["X"]["trend"], fitted["feature_validity"]["trend"]
        names = fitted["feature_names"]["trend"]
        self.assertEqual(len(names),43)
        self.assertEqual(names,ref.legacy["names"])
        train_mask = fitted["metadata"].ready.to_numpy(bool) & fitted["metadata"].train_mask.to_numpy(bool)
        selected_rows = np.flatnonzero(train_mask)
        self.assertEqual(len(selected_rows),ref.legacy["pre_cap_rows"])
        selected_rows = np.sort(np.random.default_rng(9501).choice(selected_rows,4096,replace=False))
        self.assertEqual(ref.legacy["sampled_rows"],4096)
        self.assertEqual(ref.legacy['sampled_row_id_sha256'],hashlib.sha256('\n'.join(fitted['metadata'].iloc[selected_rows].row_id).encode()).hexdigest())
        restored=pickle.loads(pickle.dumps(ref,protocol=5))
        with patch.object(methods,'fit_method',side_effect=AssertionError('refit during score')):
            replay=methods.score_method(restored,test)
        for group in actual:
            for family in actual[group].values:
                np.testing.assert_array_equal(actual[group].values[family],replay[group].values[family])
        centers, scales, active = [], [], []
        for column in range(43):
            keep = valid[selected_rows,column] & np.isfinite(x[selected_rows,column])
            values = x[selected_rows[keep],column].astype(float)
            enabled = names[column] not in legacy.NEUTRAL_NAMES and len(values)>0
            median = float(np.median(values)) if enabled else 0.
            width = max(float(np.quantile(values,.9)-np.quantile(values,.1)),.05) if enabled else 1.
            centers.append(median);scales.append(width);active.append(enabled)
            self.assertEqual(median,ref.legacy["statistics"][column]["median"])
            self.assertEqual(width,ref.legacy["statistics"][column]["range90"])
        old = Path(legacy.__file__).parents[1]/"trend_family_comparison"/"protocol.json"
        families = json.loads(old.read_text(encoding="utf-8"))["family_feature_names"]
        self.assertEqual(sum(map(len,families.values())),37)
        x = pack["X"]["trend"].astype(float)
        raw = abs((x-np.array(centers))/np.array(scales))
        valid, meta = pack["feature_validity"]["trend"],pack["metadata"]
        for group,a in test.items():
            mask = meta.group.eq(group).to_numpy()
            for family, labels in families.items():
                columns = [names.index(name) for name in labels]
                ready = valid[mask][:,columns] & np.isfinite(x[mask][:,columns]) & np.array(active)[columns]
                expected = np.max(np.where(ready,raw[mask][:,columns],-np.inf),axis=1)
                supported = ready.all(axis=1) if family == "relation" else ready.any(axis=1)
                expected[~supported] = np.nan
                expected = pd.Series(expected,index=pd.DatetimeIndex(meta.loc[mask,"window_end"])).reindex(a.index).to_numpy()
                key = "latency" if family == "latency" else family
                np.testing.assert_array_equal(actual[group].values[key],expected)


if __name__ == "__main__":
    unittest.main()
