"""Frozen input probes: per-feature TRAIN median replacement, never model fit."""
from copy import deepcopy
import importlib
import numpy as np
import pandas as pd
from .artifacts import digest, require, sha
from .explanation_context import label


class Sensitivity:
    def __init__(self,source):
        self.source=source
        artifacts=importlib.import_module('research.integration.feature_rule_ml.shared42.artifacts')
        self.bundles,self.model_meta=artifacts.load_models(source.model_folder,source.profile,True)
        self.prepare_frame=importlib.import_module('research.integration.feature_rule_ml.previous_shared42').prepare_frame
        self.score_lof=importlib.import_module('research.integration.feature_rule_ml.previous_shared42').score_lof
        self.score_family=importlib.import_module('research.integration.feature_rule_ml.shared42.detector').score_family
        self.rels={r['id']:r for r in source.profile['definition']['schema']['relationships']}
        self.cache={};self.calls=0;self.probe_rows=0

    def frame(self,short,ordinal,raw):
        source=self.source;arr=source.arrays[short]
        adapter=raw.get('ADAPTER_TYPE','__GLOBAL__') or '__MISSING__'
        if short=='M':
            e=source.capture.engine
            key=e.key(raw,'EC_MESSAGE_MONITORING',['TRANSACTION_ID','PROCESS_ID','__RETRY_LEXICAL'])
            parents=e.parent_index['physical_M_to_P_retry'].get(key,[])if key is not None else []
            adapter=(parents[0].get('ADAPTER_TYPE')or'__MISSING__')if len(parents)==1 else '__UNKNOWN_PARENT__'
        metadata=pd.DataFrame([dict(row_id=raw['__SOURCE_ROW_ID'],transaction_id=raw['TRANSACTION_ID'],
            process_id=raw.get('PROCESS_ID',''),message_id=raw.get('MESSAGE_ID',''),family_date='',adapterType=adapter)])
        contract=source.profile['definition']['ml_contracts'][short]
        return self.prepare_frame(arr['values'][ordinal:ordinal+1],arr['population'][ordinal:ordinal+1],arr['feature_ids'].tolist(),metadata,contract)

    def explain(self,fact,ordinal,raw):
        p=fact['payload'];short=p['score_grain'];algorithm={'IF':'if','K-means':'kmeans','LOF':'lof'}[p['algorithm']]
        key=(short,str(raw['__SOURCE_ROW_ID']),algorithm)
        if key in self.cache:return deepcopy(self.cache[key])
        bundle=self.bundles[short+'_'+algorithm];pin=self.model_meta['models'][short+'_'+algorithm]
        require(pin['sha256']==p['model_sha256']==sha(self.source.model_folder/pin['file']),'sensitivity_model_pin_changed')
        frame=self.frame(short,ordinal,raw);contract=bundle['contract'];cols=contract['columns']
        encoder=bundle['encoder']if algorithm=='lof' else bundle['model'].global_model.encoder
        require(list(encoder.columns)==cols,'sensitivity_encoder_column_order')
        available=[f for f in contract['features']if np.isfinite(float(frame.iloc[0][f])) and bool(frame.iloc[0]['population__'+f])]
        probe=pd.concat([frame]*(len(available)+1),ignore_index=True)
        for j,f in enumerate(available,1):probe.loc[j,f]=float(encoder.median[cols.index(f)])
        # Only available base measurements change; indicator columns, metadata,
        # adapter routing and phase remain identical across all probes.
        stable=[c for c in frame.columns if c not in available]
        require(all(probe[c].equals(pd.concat([frame[c]]*len(probe),ignore_index=True))for c in stable),'probe_changed_metadata_or_indicator')
        score,codes=(self.score_lof if algorithm=='lof' else self.score_family)(bundle,probe)
        self.calls+=1;self.probe_rows+=len(probe)
        require(np.isfinite(score).all(),'nonfinite_sensitivity_score')
        saved=float(p['score']);tol=max(1e-8,abs(saved)*1e-8)
        require(abs(float(score[0])-saved)<=tol,'saved_ML_score_not_reproduced')
        require(bool(codes[0]==1)==(fact['state']=='warning'),'saved_ML_warning_not_reproduced')
        effects=[]
        for j,f in enumerate(available,1):
            feature=self.source.features[f]
            effects.append(dict(feature_id=f,definition_sha256=feature['sha'],title=label(feature['expression'],self.rels),
                original_value=float(frame.iloc[0][f]),replacement_value=float(probe.iloc[j][f]),
                replacement_reference='frozen global TRAIN encoder median',unit=feature['unit'],
                original_score=float(score[0]),replacement_score=float(score[j]),score_decrease=float(score[0]-score[j]),
                warning_after_replacement=bool(codes[j]==1)))
        effects.sort(key=lambda e:(-e['score_decrease'],e['feature_id']))
        result=dict(contract='xai-ML-input-sensitivity/1.0.0',score_evidence_id=fact['id'],
            algorithm=p['algorithm'],source_refs=fact['source_refs'],model_sha256=pin['sha256'],
            feature_space_sha256=p['feature_space_sha256'],original_saved_score=saved,reproduced_score=float(score[0]),
            reproduction_tolerance=tol,score_direction=p['score_direction'],threshold=float(p['threshold']),
            method='one_available_base_feature_replaced_with_frozen_TRAIN_global_median',
            reference_scope='TRAIN only; global encoder, route and local experts unchanged',
            availability_policy='only finite in-population measurements; original missing/population indicators unchanged',
            feature_effects=effects,skipped_features=[f for f in contract['features']if f not in available],
            top_positive_changes=[e for e in effects if e['score_decrease']>tol][:3],
            all_changes_nonpositive=all(e['score_decrease']<=tol for e in effects),
            limitation='Input sensitivity, not additive attribution, causal effect or a valid reconstructed T/P/M transaction. Derived-feature consistency is not enforced in this model-input probe.',
            no_fit=True,no_threshold_change=True)
        self.cache[key]=deepcopy(result)
        return result
