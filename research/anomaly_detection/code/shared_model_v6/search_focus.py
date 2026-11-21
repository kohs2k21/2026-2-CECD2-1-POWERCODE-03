import argparse
from shared_model_v6 import search
from shared_model_v6.focus import Setting,Detector
from shared_model_v6.protocol import OUT,ALGORITHMS

def settings(a):
    for view in ('radial_pair','peaks_joint'):
        for self_only in (False,True):
            for gap,burst in ((2.,1.),(2.,2.),(1.,2.)):
                yield Setting(a,view=view,tail=True,samples=128,neighbors=300,gap_gain=gap,burst_gain=burst,self_cadence_only=self_only)
    for white in (False,True):
        yield Setting(a,view='compact',white=white,neighbors=600,self_cadence_only=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--algorithms',nargs='+',default=list(ALGORITHMS));args=p.parse_args()
    search.settings=settings;search.Detector=Detector;search.run(args.algorithms,OUT/'focus_search')
