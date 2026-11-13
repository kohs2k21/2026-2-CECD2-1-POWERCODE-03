"""키트 시험: 합성 사례 재생성, CPU 실행 1회, 실데이터 형식 export(DB 없이) 요약 값 노출, GPU 배선(가짜 모델).

출력은 저장소 밖 임시 폴더(TEMP)에만 쓴다. DB·모델·네트워크 없음.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import export_cases  # noqa: E402
import kit  # noqa: E402
import make_cases  # noqa: E402
import run_cases  # noqa: E402

CASES = HERE / "cases"


def latest_run(root: Path) -> Path:
    return sorted(path for path in root.iterdir() if path.is_dir())[-1]


class KitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="protocol-explain-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.assertFalse(kit.inside(self.tmp, kit.REPO))

    def test_make_cases_reproduces_committed_cases(self):
        index = make_cases.make(self.tmp / "cases")
        committed = kit.read_json(CASES / "index.json")
        self.assertEqual(index, committed)
        for case in committed["cases"]:
            for name, digest in case["files"].items():
                self.assertEqual(kit.sha256_file(self.tmp / "cases" / case["path"] / name), digest)

    def test_cpu_run_synthetic_once(self):
        code = run_cases.main(["--cases-dir", str(CASES), "--output-root", str(self.tmp), "--run-name", "cpu",
                               "--mode", "cpu"])
        self.assertEqual(code, 0)
        summary = kit.read_json(latest_run(self.tmp) / "summary.json")
        self.assertEqual((summary["passed"], summary["total"]), (8, 8))
        self.assertTrue(all(row["facts_match"] for row in summary["cases"]))
        self.assertIsNotNone(summary["cases"][0]["output"])  # 합성은 문장을 그대로 남긴다

    def real_export(self):
        drafts = make_cases.ProtocolPathDrafts()
        for bundle in make_cases.bundles():
            drafts.add(bundle)
        result = drafts.result()
        alerts = [(f"decision-{n}", source["review"]) for n, (_, _, _, source) in enumerate(make_cases.alert_sources())]
        alerts.append(("decision-dup", alerts[0][1]))  # 같은 묶음은 한 번만
        fetched = {"drafts": result, "period": make_cases.PERIOD, "alerts": alerts,
                   "protocol_rules": list(make_cases.RULES.values()) + [make_cases.rule("IF_SYN_Z", [["SYN_GW"]])],
                   "rule_revisions": {7: list(make_cases.RULES.values())}, "source_info": {"mode": "job"}}
        return export_cases.build_export(self.tmp / "export", fetched, top=3, small=2, unobserved=1, alerts=4)

    def test_real_export_and_summary_hide_values(self):
        index = self.real_export()
        self.assertEqual(index["counts"]["observed_paths"], 6)
        self.assertEqual(index["counts"]["alert"], 2)
        selections = [case["selection"] for case in index["cases"]]
        self.assertEqual(selections.count("registered_not_observed"), 1)
        alias_map = kit.read_json(self.tmp / "export" / "private" / "alias_map.json")
        self.assertIn("IF_SYN_A", json.dumps(alias_map))
        for case in index["cases"]:
            facts_text = (self.tmp / "export" / case["path"] / "facts.json").read_text(encoding="utf-8")
            self.assertNotIn("SYN_", facts_text)  # 사실은 별칭만
        runs = self.tmp / "runs"
        self.assertEqual(run_cases.main(["--cases-dir", str(self.tmp / "export"), "--output-root", str(runs),
                                         "--run-name", "real-cpu", "--mode", "cpu"]), 0)
        run_dir = latest_run(runs)
        summary = kit.read_json(run_dir / "summary.json")
        self.assertEqual(summary["passed"], summary["total"])
        for name in ("summary.json", "summary.md"):
            text = (run_dir / name).read_text(encoding="utf-8")
            for value in ("SYN_", "IF-01", "CH-01", "인터페이스 ", "건이고", '"output"', '"details"', "250"):
                self.assertNotIn(value, text, (name, value))

    def test_only_explicit_synthetic_shows_values(self):
        self.assertFalse(run_cases.is_real_index({"kind": "synthetic"}))
        for index in ({"kind": "real"}, {}, {"kind": "Synthetic"}, {"kind": None}):
            self.assertTrue(run_cases.is_real_index(index), index)

    def test_gpu_wiring_with_fake_model(self):
        class Fake:
            def __init__(self, raws):
                self.raws = iter(raws)

            def count_tokens(self, messages):
                return 100

            def generate(self, messages, max_new_tokens, time_limit):
                raw, length_hit = next(self.raws)
                return {"raw": raw, "seconds": 1.5, "input_tokens": 100, "output_tokens": 40, "finished": not length_hit,
                        "length_limit_hit": length_hit, "time_limit_hit": False}

        facts = kit.read_json(CASES / "syn-003" / "facts.json")
        raws = [(kit.pe.template_output(facts), False),
                (json.dumps({"explanation": "허용 경로에 없는 경로가 약 7%입니다.", "reviewPoints": []},
                            ensure_ascii=False), False),
                ('{"explanation": "관측', True)]
        fake = Fake(raws)
        code = run_cases.main(["--cases-dir", str(CASES), "--output-root", str(self.tmp), "--run-name", "gpu-fake",
                               "--mode", "gpu", "--cases", "syn-003,syn-004,syn-005"], model_factory=lambda _: fake)
        self.assertEqual(code, 0)
        rows = {row["case_id"]: row for row in kit.read_json(latest_run(self.tmp) / "summary.json")["cases"]}
        self.assertTrue(rows["syn-003"]["passed"])
        self.assertIn("numbers_subset", rows["syn-004"]["reasons"])
        self.assertIn("length_limit_hit", rows["syn-005"]["reasons"])
        self.assertIn("json_parsed", rows["syn-005"]["reasons"])


if __name__ == "__main__":
    unittest.main()
