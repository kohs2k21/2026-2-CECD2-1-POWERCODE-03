"""One fresh final confirmation after all final choices are frozen."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import model_research_v17.confirm as confirmation
from model_research_v15.features import augment
from model_research_v18.run import OUTPUT

if __name__ == "__main__":
    confirmation.OUTPUT, confirmation.augment = OUTPUT, augment
    confirmation.confirm()
