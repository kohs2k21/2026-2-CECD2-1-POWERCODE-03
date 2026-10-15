"""Static local comparison; no framework, no publishing, no model calls."""
from prepare_v5 import *
from html import escape
from collections import defaultdict
from semantic_identity_v5 import key as semkey

def candidate_usage():
 fs=read(TOP/'review/adopted_new_features.json');groups=defaultdict(list);out=[]
 for x in fs:
  f=x['checks']['feature'];s=f['measurement']['summary'];v=x['checks']['values'];scope={'grain':f['plan']['grain'],'population':f['plan']['population'],'group_by':f['plan']['group_by'],'source_version':f['source_version']}
  typed=[]
  for a in v:
   if isinstance(a,bool):typed.append(['boolean',a])
   elif a is None:typed.append(['unavailable'])
   elif isinstance(a,dict)and 'decimal'in a:typed.append(['numeric',str(Decimal(a['decimal']).normalize())])
   elif isinstance(a,(int,float)):typed.append(['numeric',str(Decimal(str(a)).normalize())])
   else:typed.append(['text',str(a)])
  vd=digest({'scope':scope,'values':typed});groups[vd].append(x['key']);state='excluded_current_constant'if s['variation']=='constant'and s['valid_count']>0 else 'deferred'
  out.append({'key':x['key'],'code_id':f['id'],'model_alias':f['model_proposal_id'],'source_kind':x['source_kind'],'task':x['task_kind'],'meaning':f['meaning'],'expression':f['plan']['expression'],'grain':f['plan']['grain'],'population':f['plan']['population'],'type':f['dtype'],'unit':f['unit'],'definition_SHA':f['definition_sha256'],'source_version':f['source_version'],'summary':s,'definition_duplicate':False,'sample_value_digest':vd,'availability':'offline_captured_scope','IF_status':state,'IF_approved':False,'operational_usefulness_approved':False,'unconfirmed':['online observation availability','missingness/leakage handling in production','business meaning and any duration/common clock interpretation','actual anomaly discrimination']})
 write(TOP/'review/candidate_usage_v5.json',{'new_adopted_definitions':out,'sample_value_equal_groups':[ks for ks in groups.values()if len(ks)>1],'same_values_do_not_imply_same_definition':True,'existing71':{'source':pin(V4/'definition_usage71_review.json'),'counts':read(V4/'counts.json')['existing_definitions'],'measured_on_prior_scope_not_this_v5_capture':True},'v4_actual4':{'source':pin(V4/'review/adopted_new_features.json'),'actual_definitions':4,'measured_on_v4_fresh128_scope':True},'datasets_versions_not_summed':True});return out

def main():
 comp=read(TOP/'comparison_r02.json');counts=read(TOP/'counts.json');usage=candidate_usage();parts=[]
 for phase,z in comp['results'].items():
  for row in z['rows']:
   details={'request_and_code':row,'raw_output':read(TOP/'training/responses'/phase/(row['id']+'.json'))['raw_output']}
   parts.append('<tr data-group="'+escape(row['group'])+'" data-phase="'+phase+'"><td>'+phase+'</td><td>'+escape(row['id'])+'</td><td>'+row['group']+'</td><td>'+str(row['candidate_count'])+'</td><td>'+('통과'if row['full_pass']else'검토/실패')+'</td><td>'+escape('; '.join(row['root_reasons']))+'</td><td><details><summary>JSON·원문</summary><pre>'+escape(json.dumps(details,ensure_ascii=False,indent=2))+'</pre></details></td></tr>')
 features=''.join('<details><summary>'+escape(x['key'])+' — '+escape(x['meaning'])+'</summary><pre>'+escape(json.dumps(x,ensure_ascii=False,indent=2))+'</pre></details>'for x in usage)
 html='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>피처 보완 v5 비교</title><style>body{font:15px system-ui;max-width:1600px;margin:30px auto;padding:0 20px;color:#18202c}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;word-break:break-all;background:#f5f7fa;padding:14px;max-height:550px;overflow:auto}select{padding:8px;margin:10px}details{margin:8px 0}.limit{background:#fff2ce;padding:14px}</style><h1>피처 모델 보완 v5</h1><p class="limit">동결 입력에서 v4와 v5 비교. 같은 회사의 미사용 포착 거래와 통제 합성 자료이며 기업 일반화·IF 적격성·장애 탐지 성능 승인이 아닙니다. 한 번의 SFT는 OOM 후 전체 상태로 재개했으며, 실제 실행60회/최종 유지58 updates입니다.</p><h2>집계</h2><pre>'''+escape(json.dumps(counts,ensure_ascii=False,indent=2))+'''</pre><h2>입력별 결과</h2><label>과제<select id="group"><option value="">전체</option><option>specified</option><option>discovery</option><option>limits</option></select></label><label>모델<select id="phase"><option value="">전체</option><option>v4</option><option>v5</option></select></label><table><thead><tr><th>모델</th><th>입력</th><th>과제</th><th>후보</th><th>전체</th><th>직접 검토</th><th>근거와 원문</th></tr></thead><tbody>'''+''.join(parts)+'''</tbody></table><h2>새로 채택한 좁은 계산 정의</h2><p>기존71개, v4 실제4개와 합산하지 않습니다. 아래 정의도 운영상 유용성/IF 승인은 별도입니다.</p>'''+features+'''<h2>재현과 Rule 연결</h2><p><a href="README.md">실행 방법</a> · <a href="rule_link/results.json">고정 Rule 모델 출력</a> · <a href="review/root_rule_review.json">Rule 직접 의미 검토</a> · <a href="representative/result.json">대표 CPU 재현</a> · <a href="report.md">보고서</a></p><script>for(const el of document.querySelectorAll('select'))el.addEventListener('change',()=>{for(const tr of document.querySelectorAll('tbody tr'))tr.hidden=!!((group.value&&tr.dataset.group!==group.value)||(phase.value&&tr.dataset.phase!==phase.value))});</script></html>'''
 p=TOP/'comparison.html';assert not p.exists();p.write_text(html)
 print('comparison.html created')
if __name__=='__main__':main()
