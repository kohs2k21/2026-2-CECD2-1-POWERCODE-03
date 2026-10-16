"""Pinned official IF/EIF/DeepIF components; frozen inductive DeepIF-I adapter."""
from __future__ import annotations
import ast
import contextlib
import hashlib
import io
import json
import random
import sys
import types
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

THIRD_PARTY = Path(__file__).with_name('third_party')
SETTINGS = {'trees':128, 'max_samples':256, 'cpu_threads':1,
            'eif_extension':'full (dimension - 1)', 'dif_ensembles':8,
            'dif_trees_per_ensemble':16, 'dif_hidden':[64,32], 'dif_rep_dim':20,
            'dif_activation':'tanh','dif_CERE':True,'dif_DEAS':True}

def verify_sources():
    records=json.loads((THIRD_PARTY/'manifest.json').read_text())
    for r in records:
        assert hashlib.sha256((THIRD_PARTY/r['path']).read_bytes()).hexdigest()==r['sha256']
    return records

def _official_module(name, path, injected=None, omit_graph_imports=False):
    """Original AST except optional graph imports; no graph algorithm is run."""
    if name in sys.modules:return sys.modules[name]
    tree=ast.parse(path.read_text(encoding='utf-8'))
    if omit_graph_imports:
        tree.body=[node for node in tree.body if not (
            isinstance(node,ast.Import) and any(x.name.startswith('torch_geometric') for x in node.names)
            or isinstance(node,ast.ImportFrom) and (node.module or '').startswith(('torch_geometric','algorithms')))]
    module=types.ModuleType(name);module.__file__=str(path)
    if injected:module.__dict__.update(injected)
    sys.modules[name]=module
    exec(compile(tree,str(path),'exec'),module.__dict__)
    return module

def load_eif():
    verify_sources()
    # Upstream imports the sibling version module without a package prefix.
    with_version='version' not in sys.modules
    if with_version:_official_module('version',THIRD_PARTY/'eif/version.py')
    return _official_module('if_lab_official_eif',THIRD_PARTY/'eif/eif_old.py')

def load_dif():
    verify_sources()
    net=_official_module('if_lab_official_network',THIRD_PARTY/'deep_iforest/algorithms/net_torch.py',omit_graph_imports=True)
    return _official_module('if_lab_official_dif',THIRD_PARTY/'deep_iforest/algorithms/dif.py',{'net_torch':net},True)

def _finite(X):
    X=np.asarray(X,dtype=np.float32)
    if X.ndim!=2 or not np.isfinite(X).all():raise ValueError('Finite 2D features required')
    return X

class StandardIF:
    label='IF (scikit-learn)'
    def __init__(self,seed):self.seed=seed
    def fit(self,X):
        X=_finite(X)
        self.model=IsolationForest(n_estimators=SETTINGS['trees'],max_samples=min(SETTINGS['max_samples'],len(X)),
                                   contamination='auto',n_jobs=1,random_state=self.seed).fit(X)
        return self
    def score_samples(self,X):return -self.model.score_samples(_finite(X))

class ExtendedIF:
    label='EIF (authors trees with equivalent vectorized inference)'
    def __init__(self,seed):self.seed=seed
    def fit(self,X):
        X=_finite(X);np.random.seed(self.seed);random.seed(self.seed)
        self.model=load_eif().iForest(X,ntrees=SETTINGS['trees'],sample_size=min(SETTINGS['max_samples'],len(X)),ExtensionLevel=X.shape[1]-1)
        return self
    def paths(self,X,with_leaves=False):
        X=_finite(X);paths=np.empty((len(X),self.model.ntrees));leaf_ids=np.empty_like(paths,dtype=np.int64)
        c_factor=load_eif().c_factor
        for t,tree in enumerate(self.model.Trees):
            stack=[(tree.root,np.arange(len(X)),0,1)]
            while stack:
                node,ids,depth,leaf_id=stack.pop()
                if not len(ids):continue
                if node.ntype=='exNode':
                    paths[ids,t]=depth+(c_factor(node.size) if node.size>1 else 0.0)
                    leaf_ids[ids,t]=leaf_id
                else:
                    left=(X[ids]-node.p).dot(node.n)<0
                    stack.append((node.left,ids[left],depth+1,leaf_id*2))
                    stack.append((node.right,ids[~left],depth+1,leaf_id*2+1))
        return (paths,leaf_ids) if with_leaves else paths
    def score_samples(self,X):
        paths=self.paths(X)
        # Sequential tree accumulation matches the authors' reference addition order.
        total=np.zeros(len(paths))
        for j in range(self.model.ntrees):total+=paths[:,j]
        return 2.0**(-(total/self.model.ntrees)/self.model.c)

class DeepIFInductive:
    label='DeepIF-I (official Deep IF with frozen preprocessing research adapter)'
    def __init__(self,seed):self.seed=seed
    def fit(self,X):
        import torch
        torch.set_num_threads(1)
        official=load_dif()
        # Only train/test representation scaling and the shape cache differ.
        class InductiveDIF(official.DIF):
            @staticmethod
            def set_seed(seed):
                # Python 3.12 random.seed rejects NumPy integer scalars.
                official.DIF.set_seed(int(seed))
            def deep_transfer_batch_ensemble(this,X,net):
                outputs=[]
                with torch.no_grad():
                    for batch in torch.utils.data.DataLoader(X,batch_size=this.batch_size,drop_last=False,shuffle=False):
                        outputs.append(net(batch.float().to(this.device)).reshape([this.n_ensemble,len(batch),-1]))
                reduced=[torch.cat([chunk[j] for chunk in outputs]).cpu().numpy() for j in range(this.n_ensemble)]
                if this._fit_representation:
                    this.frozen_scalers=[StandardScaler().fit(z) for z in reduced]
                return [np.tanh(s.transform(z)) for s,z in zip(this.frozen_scalers,reduced)]
            def _inference_transfer(this,X):
                return this.deep_transfer_batch_ensemble(X,this.net_lst[0])
        # Save the network/forests/scalers as plain state, avoiding a local-class pickle.
        np.random.seed(self.seed);random.seed(self.seed);torch.manual_seed(self.seed)
        with contextlib.redirect_stdout(io.StringIO()):
            self.model=InductiveDIF(n_ensemble=SETTINGS['dif_ensembles'],n_estimators=SETTINGS['dif_trees_per_ensemble'],
                max_samples=min(SETTINGS['max_samples'],len(X)),hidden_dim=SETTINGS['dif_hidden'],rep_dim=SETTINGS['dif_rep_dim'],
                activation=SETTINGS['dif_activation'],batch_size=256,new_score_func=True,new_ensemble_method=True,
                random_state=self.seed,device='cpu',n_processes=1,verbose=0)
        self.model._fit_representation=True
        with threadpool_limits(1):self.model.fit(_finite(X))
        self.model._fit_representation=False
        return self
    def score_samples(self,X):
        X=_finite(X)
        with threadpool_limits(1):
            # DEAS allocates sample-by-node matrices. A fixed frozen scaler makes chunking valid.
            return np.concatenate([self.model.decision_function(X[start:start+512]) for start in range(0,len(X),512)])
    def export_state(self):
        return {'configuration':SETTINGS,'seed':self.seed,'net':self.model.net_lst[0],
                'forests':self.model.clf_lst,'scalers':self.model.frozen_scalers,
                'official_sources':verify_sources(),'adapter':'train representation scalers frozen; same-length input cache removed'}

def build_model(name,seed):
    return {'if':StandardIF,'eif':ExtendedIF,'dif':DeepIFInductive}[name](seed)

def smoke():
    import time
    rng=np.random.default_rng(97);train=rng.normal(size=(512,12)).astype(np.float32);test=rng.normal(size=(137,12)).astype(np.float32)
    reports=[]
    for name in ('if','eif','dif'):
        t=time.perf_counter();model=build_model(name,11).fit(train);fit_s=time.perf_counter()-t
        t=time.perf_counter();scores=model.score_samples(test);score_s=time.perf_counter()-t
        assert scores.shape==(len(test),) and np.isfinite(scores).all()
        chunks=np.concatenate([model.score_samples(test[:53]),model.score_samples(test[53:])])
        np.testing.assert_allclose(scores,chunks,rtol=2e-6,atol=2e-6)
        same_len=train.copy();same_len[:]=100.0
        assert not np.array_equal(model.score_samples(same_len),model.score_samples(train))
        frozen=hashlib.sha256(__import__('pickle').dumps(getattr(model.model,'frozen_scalers',None))).hexdigest()
        model.score_samples(test[::-1].copy())
        assert frozen==hashlib.sha256(__import__('pickle').dumps(getattr(model.model,'frozen_scalers',None))).hexdigest()
        reports.append({'algorithm':name,'label':model.label,'fit_seconds':fit_s,'score_seconds_137':score_s,
                        'chunk_equal':True,'same_length_changed_input_changes_scores':True,'frozen_scalers_unchanged':True})
    return reports

def eif_equivalence():
    records=[]
    for seed in (11,29,47):
        rng=np.random.default_rng(seed);train=rng.normal(size=(512,12)).astype(np.float32)
        model=ExtendedIF(seed).fit(train)
        roots=[t.root for t in model.model.Trees[:8]]
        boundary=np.vstack([n.p+step*n.n/np.linalg.norm(n.n) for n in roots for step in (0,1e-5,-1e-5)])
        probes=np.vstack([rng.normal(size=(37,12)),np.zeros((1,12)),np.full((1,12),1e6),np.full((1,12),-1e6),boundary]).astype(np.float32)
        paths,leaves=model.paths(probes,True);official=load_eif()
        reference=np.array([[official.PathFactor(row,t).path for t in model.model.Trees] for row in probes])
        np.testing.assert_allclose(paths,reference,rtol=0,atol=0)
        # Reference branch decisions use its scalar dot, compared to batch traversal leaf IDs.
        for i,row in enumerate(probes):
            for j,tree in enumerate(model.model.Trees):
                node=tree.root;leaf_id=1
                while node.ntype!='exNode':
                    left=(row-node.p).dot(node.n)<0
                    node=node.left if left else node.right;leaf_id=leaf_id*2+(0 if left else 1)
                assert leaves[i,j]==leaf_id
        scores=model.score_samples(probes);reference_scores=model.model.compute_paths(probes)
        np.testing.assert_allclose(scores,reference_scores,rtol=1e-14,atol=1e-14)
        records.append({'seed':seed,'probe_rows':len(probes),'trees':128,'path_exact':True,'leaf_exact':True,
                        'score_max_absolute_difference':float(np.max(np.abs(scores-reference_scores)))})
    return records

if __name__=='__main__':print(json.dumps(smoke(),indent=2))
