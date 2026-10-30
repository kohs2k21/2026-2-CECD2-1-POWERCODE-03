"""Normal empirical-tail features: no anomaly fit or task-type dispatch."""
import argparse
from shared_model_v6 import search
from shared_model_v6.models import Setting
from shared_model_v6.protocol import OUT,ALGORITHMS

def settings(a):
    for view in ('radial','radial_pair','peaks','compact','peaks_joint','raw'):
        for white in (False,True):
            if view=='radial'and white:continue
            yield Setting(a,view=view,white=white,tail=True,samples=128,neighbors=100)
    if a=='if':
        for n in (32,64,256,512):yield Setting(a,view='peaks_joint',tail=True,samples=n)
    elif a=='lof':
        for n in (20,50,300,600):yield Setting(a,view='peaks_joint',tail=True,neighbors=n)
    else:
        for n in (1,2,16,32):yield Setting(a,view='peaks_joint',tail=True,clusters=n)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS))
    search.settings=settings;search.run(p.parse_args().algorithms,OUT/'tail_search')
