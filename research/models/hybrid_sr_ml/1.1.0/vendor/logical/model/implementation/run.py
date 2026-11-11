from pathlib import Path
import os,sys
PROJECT=Path(__file__).resolve().parents[1]
PACKAGE=PROJECT
sys.path.insert(0,str(PACKAGE))
BASE=Path(os.environ.get('SPEC_BASE_PATH',str(PROJECT/'models/base-not-bundled')))
DESIGN=PROJECT/'examples/development'
