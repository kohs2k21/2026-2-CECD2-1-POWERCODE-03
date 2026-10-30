from pathlib import Path
import argparse
import pandas as pd
from .model import load_bank,score_frame,HORIZONS

parser=argparse.ArgumentParser(description='Selected-batch-only quantile-aligned normal model')
parser.add_argument('--model',type=Path,required=True)
parser.add_argument('--input',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--analysis-hours',type=int,choices=HORIZONS,required=True)
parser.add_argument('--split-batches',action='store_true')
args=parser.parse_args()
if args.output.exists() or args.input.resolve()==args.output.resolve():raise FileExistsError('Do not overwrite existing files')
result=score_frame(load_bank(args.model),pd.read_csv(args.input),args.analysis_hours,args.split_batches)
args.output.parent.mkdir(parents=True,exist_ok=True)
result.to_csv(args.output,index=False,encoding='utf-8-sig')
print(f'{len(result)} hourly decisions, no monitoring history carry',flush=True)
