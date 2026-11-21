"""Isolated normal-reference calibration study; old release remains unchanged."""
from pathlib import Path
import argparse
import hashlib
from ..common import REPORT, sha256
from . import run
from .conditional import ConditionalWindowModel


def configure():
    run.OUT = REPORT / "five_minute_conditional"
    run.FineWindowModel = ConditionalWindowModel
    def source_hash():
        names = ("model.py", "conditional.py", "bank.py", "run.py", "refine.py")
        return hashlib.sha256("".join(sha256(Path(__file__).with_name(n)) for n in names).encode()).hexdigest()
    run.runtime_hash = source_hash


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("fit", "evaluate"))
    args = p.parse_args()
    configure()
    run.fit() if args.command == "fit" else run.evaluate()
