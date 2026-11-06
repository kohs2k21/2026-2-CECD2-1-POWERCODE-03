"""Evaluate unchanged confirmation using the numerically identical portable artifact."""
import argparse
from . import confirmation
from .serialization import load_portable


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    confirmation.load_bank = lambda trusted_path: load_portable()
    confirmation.evaluate()
