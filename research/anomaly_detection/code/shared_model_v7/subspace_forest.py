"""A transparent score-aggregation IF variant, NOT unmodified sklearn IF.

One fitted IsolationForest with one feature per tree. Trees are grouped by
their sampled input, scores are normal-CDF calibrated, and group evidence is
max-pooled. This is within-forest feature-view aggregation, not Adapter experts
or an IF/K-means/LOF ensemble. Feature-bagging is an inspiration, not a claim to
reproduce Lazarevic & Kumar's original algorithm.
"""
import numpy as np

REFERENCES=(
 'https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html',
 'https://experts.umn.edu/en/publications/feature-bagging-for-outlier-detection/',
 'https://doi.org/10.1145/1081870.1081891',
)


def expected_path(n):
    n=np.asarray(n,float);out=np.zeros_like(n);out[n==2]=1.;mask=n>2
    out[mask]=2*(np.log(n[mask]-1)+np.euler_gamma)-2*(n[mask]-1)/n[mask]
    return out


class Paths:
    def __init__(self,forest):
        self.forest=forest;self.leaf_paths=[];self.counts=np.zeros(forest.n_features_in_,int)
        for tree,columns in zip(forest.estimators_,forest.estimators_features_):
            if len(columns)!=1:raise ValueError('This ablation requires exactly one feature per tree')
            self.counts[columns[0]]+=1;n=tree.tree_.node_count;depth=np.ones(n,int)
            for node,(left,right)in enumerate(zip(tree.tree_.children_left,tree.tree_.children_right)):
                if left>=0:depth[left]=depth[node]+1;depth[right]=depth[node]+1
            self.leaf_paths.append(depth+expected_path(tree.tree_.n_node_samples)-1.)
        if np.any(self.counts==0):raise ValueError('A feature received no isolation trees')
        self.c=float(expected_path([forest.max_samples_])[0])
    def lengths(self,x):
        sums=np.zeros((len(x),len(self.counts)))
        for tree,cols,length in zip(self.forest.estimators_,self.forest.estimators_features_,self.leaf_paths):
            view=np.asarray(x[:,cols],dtype=np.float32,order='C');leaf=tree.apply(view,check_input=False)
            sums[:,cols[0]]+=length[leaf]
        return sums
    def group_scores(self,x):
        depths=self.lengths(x);denominator=self.counts*self.c
        return 2.**(-np.divide(depths,denominator,out=np.ones_like(depths),where=denominator!=0))
    def ordinary_score(self,x):
        depths=self.lengths(x).sum(axis=1);denominator=len(self.forest.estimators_)*self.c
        return 2.**(-np.divide(depths,denominator,out=np.ones_like(depths),where=denominator!=0))
