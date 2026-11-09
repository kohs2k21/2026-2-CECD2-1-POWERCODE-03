import argparse
import joblib
from shared_model_v6 import search
from shared_model_v6.hybrid import Setting,Detector
from shared_model_v6.protocol import OUT,BASE

def settings(a):
    for view in ('radial_pair','peaks'):
        for gain in (0.,.1,.25,.5,1.):yield Setting(a,evidence_gain=gain,evidence_view=view,clusters=8)
    for k in (4,16,32):yield Setting(a,evidence_gain=.25,clusters=k)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--algorithm',default='kmeans');a=parser.parse_args().algorithm
    parent=joblib.load(BASE/'models.joblib')['models'][a].base
    search.settings=settings;search.Detector=lambda s,h:Detector(s,h,parent)
    search.run([a],OUT/'hybrid_search')
