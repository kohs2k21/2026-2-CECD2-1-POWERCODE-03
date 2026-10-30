"""A continuous strongest-evidence coordinate, not a new rule/score ensemble."""
import argparse
from shared_model_v6 import search
from shared_model_v6.models import Setting
from shared_model_v6.protocol import OUT,ALGORITHMS

def settings(a):
    for view in ('radial','radial_pair','peaks_joint','compact_joint'):
        for trans in ('linear','asinh'):
            for white in (False,True):
                if view=='radial'and white:continue
                yield Setting(a,view=view,transform=trans,white=white,samples=64,neighbors=100)
    if a=='if':
        for n in (32,128,256):yield Setting(a,view='peaks_joint',samples=n)
    elif a=='lof':
        for n in (30,300,600):yield Setting(a,view='peaks_joint',neighbors=n)
    else:
        for n in (2,16,32):yield Setting(a,view='peaks_joint',clusters=n)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS))
    search.settings=settings;search.run(p.parse_args().algorithms,OUT/'joint_search')
