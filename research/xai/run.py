"""Portable CPU explanation CLI; no private CSV, GPU or implicit model call."""
import argparse
from pathlib import Path
from xai_runtime.artifacts import read, write
from xai_runtime.engine import render
from service_bridge.adapter import explain_detail


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--selection',help='Optional validated evidence-ID selection for service input')
    args=parser.parse_args()
    data=read(args.input)
    if 'decisionId' in data and isinstance(data.get('evidence'),dict):
        result=explain_detail(data,read(args.selection) if args.selection else None)
    else:
        if args.selection:parser.error('--selection is supported for service input only')
        result=render(data)
    write(Path(args.output),result)


if __name__=='__main__':main()
