"""Research-only Trainer/Detector on approved raw T/P/M CSV captures."""
import argparse
import json
import sys
from .artifacts import DEFAULT, profile
from ..common import InputFailure


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest='command',required=True)
    for name in ('inspect','capture','detect','train','demo'):
        q = s.add_parser(name)
        q.add_argument('--release',default=str(DEFAULT))
        if name in ('capture','detect','train','demo'):
            q.add_argument('--output',required=True)
        if name == 'capture':
            for arg in ('transactions','processes','messages','start','end'):
                q.add_argument('--'+arg,required=True)
            q.add_argument('--split',required=True,choices=('train','calibration','threshold','test','application'))
            q.add_argument('--data-class',required=True,choices=('approved_real','controlled'))
            q.add_argument('--complete-family-capture',action='store_true')
            q.add_argument('--accept-frozen-reference',action='store_true')
            q.add_argument('--held-lineage-excluded',action='store_true')
            q.add_argument('--confirm-same-hr-contract',action='store_true')
        if name == 'detect':
            q.add_argument('--capture',required=True)
            q.add_argument('--models')
            q.add_argument('--trust-local-model',action='store_true')
        if name == 'train':
            for k in ('train','calibration','threshold'):
                q.add_argument('--'+k,required=True)
    a = p.parse_args(argv)
    try:
        if a.command == 'inspect':
            folder,pr = profile(a.release)
            r = dict(release=str(folder),definition_sha=pr['definition_sha'],features=len(pr['definition']['features']),rules=len(pr['definition']['rules']),ML_features={k:len(v['features']) for k,v in pr['definition']['ml_contracts'].items()},model_count=9)
        elif a.command == 'capture':
            from .importer import capture_csv
            r = capture_csv(a.transactions,a.processes,a.messages,a.output,a.start,a.end,a.split,a.data_class,a.complete_family_capture,a.accept_frozen_reference,a.confirm_same_hr_contract,a.held_lineage_excluded,a.release)
        elif a.command == 'detect':
            from .detector import detect
            r = detect(a.capture,a.output,a.release,a.models,a.trust_local_model)
        elif a.command == 'train':
            from .trainer import train
            r = train(a.train,a.calibration,a.threshold,a.output,a.release)
        else:
            from .example import demo
            r = demo(a.output,a.release)
        print(json.dumps(r,ensure_ascii=False,allow_nan=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as e:
        print(json.dumps(dict(complete=False,error=str(e)),ensure_ascii=False),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
