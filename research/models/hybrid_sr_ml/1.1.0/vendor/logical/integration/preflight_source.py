"""Explicit metadata authority contract. Never infer or upgrade document authority."""
from pathlib import Path
import argparse,hashlib,importlib,json,sys
POLICY='logical-source-authority-admission1.0'
HR_AUTHORITIES=frozenset(['controlled_evaluation_contract_not_company_policy','reviewed_business_contract','verified_DDL'])
OTHER_AUTHORITIES=frozenset(['controlled_monitoring_note_not_company_policy','monitoring_note','unknown','unverified_source'])
def audit(bundle,source_path):
    errors=[];sources=[];root=Path(bundle).resolve();path=Path(source_path)
    try:
        source=json.loads(path.read_text())
        sys.path[:0]=[str(root/'implementation'),str(root)]
        normalize=importlib.import_module('run_discovery').normalize
        normalize(source)
        ids=[]
        for doc in source['documents']:
            ident=doc.get('id');authority=doc.get('authority');ids.append(ident)
            if not isinstance(ident,str)or not ident:errors.append('document_id_required')
            if authority not in HR_AUTHORITIES|OTHER_AUTHORITIES:
                errors.append('unsupported_document_authority:'+str(ident))
            if not isinstance(doc.get('text'),str)or not doc['text']:
                errors.append('document_text_required:'+str(ident))
            sources.append(dict(document_id=ident,provided_authority=authority,
                                authority_is_canonical=authority in HR_AUTHORITIES|OTHER_AUTHORITIES,
                                syntactically_HR_eligible=authority in HR_AUTHORITIES))
        if len(ids)!=len(set(ids)):errors.append('duplicate_document_ids')
    except Exception as exc:errors.append(type(exc).__name__+': '+str(exc))
    return dict(policy=POLICY,state='admitted'if not errors else'unavailable',issues=errors,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()if path.is_file()else None,
                documents=sources,authority_is_inferred_or_upgraded=False,
                authority_content_verified_by_this_check=False,model_calls=0,fit=False,
                note='Canonical labels are caller-provided assertions requiring provenance review; syntax does not verify real business authority. Free-form descriptions belong in authority_detail, not authority. Unknown evidence can explicitly use unknown or unverified_source.')
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);result=audit(a.bundle,a.input)
    with (a.output/'admission.json').open('x')as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:result[k]for k in ['policy','state','issues']}))
    if result['state']!='admitted':raise SystemExit(2)
if __name__=='__main__':main()
