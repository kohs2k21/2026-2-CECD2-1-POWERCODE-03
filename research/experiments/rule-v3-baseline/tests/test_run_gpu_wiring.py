"""run_cases GPU 경로 배선 시험(CPU). 실제 모델 대신 가짜 LocalModels를 끼워 공식 CLI rule·repair·replay·judge를 지난다.

모델 성능 시험이 아니다. 가짜 응답은 Rule 입력의 코드 사실로 만든 결정이며, 첫 사례는 일부러 틀린 임계값을 내서
번들 수리 경로(최대 1회)를 지나게 한다. 실제 GPU 적재·생성·문법 제약·시간은 시험하지 않는다.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import run_cases  # noqa: E402
import capture_layout as layout  # noqa: E402
import make_cases  # noqa: E402


def decide(inp):
    b, m = inp["baseline"], inp["measurement"]
    upper = inp["range_proof"].get("max")
    if b["state"] != "verified":
        needed = {"unit_unconfirmed": ["need_verified_unit"], "insufficient": ["need_reference_minimum_n"]}[b["state"]]
        return "request_evidence", needed
    if upper is not None and upper <= b["value"]:
        return "no_applicable", []
    if m["valid_count"] == 0:
        return "deferred", ["need_valid_observations"]
    return "proposed", []


class FakeModels:
    """specmodel.local_models.LocalModels와 같은 메서드 이름. 출력 파일 형식은 실제 generate와 같은 키를 쓴다."""
    calls = []

    def __init__(self, *args, **kwargs):
        self.integrity = {"fake": True}

    def _cpu(self):
        pass

    def _load(self, dest):
        pass

    def generate(self, kind, messages, schema, dest, budget, call, maximum=1536, arm="Base"):
        from specmodel.io import digest, write
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        receipt = budget.reserve(kind, call)
        payload = json.loads(messages[1]["content"])
        inp = payload.get("verified_input", payload)
        state, needed = decide(inp)
        decision = run_cases.oracle_decision({"input": inp}, state, needed)
        if call == "rule_0" and state == "proposed" and not FakeModels.calls:
            decision["decisions"][0]["condition"]["value"] = 123456.5  # 첫 시도 일부러 틀림 → 수리
        FakeModels.calls.append(call)
        raw = json.dumps(decision)
        write(dest / "input.json", {"messages": messages, "schema": schema, "budget": receipt})
        out = {"kind": kind, "raw": raw, "parsed": json.loads(raw), "parse_error": None, "length_limit_hit": False,
               "messages_sha": digest(messages), "tokens": {"input_tokens": 1000}, "output_tokens": 200,
               "budget": receipt, "seconds": 0.01, "GPU_memory": {"peak": 0}}
        write(dest / "output.json", out)
        return out


class GpuWiringTests(unittest.TestCase):
    def test_gpu_path_with_fake_model(self):
        tmp = Path(tempfile.mkdtemp(prefix="rv3-wire-"))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                make_cases.build(tmp / "cases")
            base = tmp / "fake-base"
            base.mkdir()
            saved = (run_cases.gpu_platform_ok, os.environ.get("CUDA_VISIBLE_DEVICES"))
            run_cases.gpu_platform_ok = lambda: True
            os.environ["CUDA_VISIBLE_DEVICES"] = "0"
            run_cases.import_bundle(run_cases.BUNDLE)
            import specmodel.local_models as local_models
            import specmodel.cli as cli
            real_models, real_cli_models = local_models.LocalModels, cli.LocalModels
            local_models.LocalModels = FakeModels
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run_cases.main(["--cases-dir", str(tmp / "cases"), "--output-root", str(tmp / "runs"),
                                       "--run-name", "wire", "--base-path", str(base),
                                       "--cases", "syn-001,syn-004,syn-007,syn-009", "--reason-language", "ko"])
            self.assertEqual(code, 0, out.getvalue())
            run_dir = next((tmp / "runs").glob("*-wire"))
            run = layout.read_json(run_dir / "run.json")
            self.assertEqual(run["mode"], "gpu")
            self.assertEqual(run["variant"]["label"], "번들 외 변형: reason-language=ko")
            self.assertNotEqual(run["package"]["effective_rule_system_prompt_sha256"], run["package"]["rule_system_file_sha256"])
            summary = layout.read_json(run_dir / "summary.json")
            self.assertEqual(summary["passed"], summary["total"], summary["cases"])
            first = summary["cases"][0]["row"]
            self.assertEqual(first["수리 전/후"], "1차 실패→수리 통과")
            self.assertTrue((run_dir / "syn-001" / "raw_attempt_0.txt").is_file())
            self.assertTrue((run_dir / "syn-001" / "raw_attempt_1.txt").is_file())
            prompt = layout.read_json(run_dir / "syn-001/rule/model/rule_0/input.json")["messages"][0]["content"]
            self.assertTrue(prompt.endswith(run_cases.KO_LINE))
            import specmodel.rule_logic as rule_logic
            self.assertFalse(rule_logic.rule_system().endswith(run_cases.KO_LINE))  # 실행 뒤 원래 프롬프트로 복원
            self.assertIs(cli.LocalModels, real_cli_models)
            stamp, name = run_dir.name.split("-wire")[0], "wire"
            with self.assertRaises(FileExistsError):  # run_cases의 실제 생성 함수: 같은 시각·이름이면 덮어쓰지 않고 실패
                run_cases.create_run_dir(tmp / "runs", name, stamp)
            self.assertTrue((run_dir / "run.json").is_file())  # 기존 실행 폴더는 그대로
        finally:
            run_cases.gpu_platform_ok = saved[0]
            if saved[1] is None:
                os.environ.pop("CUDA_VISIBLE_DEVICES", None)
            else:
                os.environ["CUDA_VISIBLE_DEVICES"] = saved[1]
            local_models.LocalModels, cli.LocalModels = real_models, real_cli_models
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
