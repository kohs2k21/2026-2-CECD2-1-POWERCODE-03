from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from shared_model_v7 import search_features as search
from shared_model_v7.expanded import prepare
from shared_model_v7.multimodal_models import Setting,Detector


def settings(a):
    for mode in('replace','joint','append'):
        for work in(0.,1.):yield Setting(a,view='multimodal',mode=mode,work_weight=work,samples=128,neighbors=600)
    for mode in('replace','joint'):
        yield Setting(a,view='radial',mode=mode,work_weight=1.,samples=64,neighbors=600)
        yield Setting(a,view='multimodal',mode=mode,work_weight=1.,white=True,transform='asinh',samples=128,neighbors=800)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(search.ALGORITHMS));args=p.parse_args()
    search.Detector=Detector;search.settings=settings
    search.run(args.algorithms,draws=1000,output=search.OUT/'multimodal_search',development=prepare())
