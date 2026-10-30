"""Offline display of saved artifacts. Does not modify Model answers or approval."""
from pathlib import Path
import json,html
from .io import read

def render_batch(folder):
 folder=Path(folder);rows=[]
 for x in read(folder/'validation_report.json'):
  d=folder/'cases'/x['id'];entry={'id':x['id'],'validation':x}
  for name in ['public_request','public_rule_plan','feature_bundle','rule_bundle','validated_rules','pending_decisions']:
   if(d/(name+'.json')).exists():entry[name]=read(d/(name+'.json'))
  rows.append(entry)
 data=json.dumps({'counts':read(folder/'counts.json'),'cases':rows},ensure_ascii=False).replace('<','\\u003c')
 page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Feature · Rule 실행 결과</title>
<style>body{font:16px/1.65 system-ui;margin:2rem auto;max-width:1150px;padding:0 1rem;background:#f5f7fb;color:#17243c}h1{font-size:1.7rem}article{background:white;border:1px solid #dce3ed;border-radius:12px;padding:1.1rem;margin:1rem 0}pre{overflow:auto;background:#f0f3f8;padding:.8rem;font-size:12px;max-height:420px}b.ok{color:#137344}b.bad{color:#ac2538}small{color:#536079}details{margin:.6rem 0}code{background:#edf1f7}input{padding:.6rem;width:90%;max-width:600px}p{margin:.5rem 0}</style>
<h1>Feature → 측정 → HR/SR 명세</h1><p>실제 모델 원문과 코드 계산 사실을 분리한 저장 결과입니다. 운영 장애 판정이나 탐지 정확도가 아닙니다.</p><p id="counts"></p><input id="search" placeholder="사례 이름 검색"><main id="cards"></main>
<script id="payload" type="application/json">PAYLOAD</script><script>
const D=JSON.parse(document.getElementById('payload').textContent),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty=o=>'<pre>'+esc(JSON.stringify(o,null,2))+'</pre>',detail=(t,o)=>'<details><summary>'+esc(t)+'</summary>'+pretty(o)+'</details>';
document.getElementById('counts').textContent=JSON.stringify(D.counts);
function draw(){const q=document.getElementById('search').value.toLowerCase();document.getElementById('cards').innerHTML=D.cases.filter(x=>x.id.toLowerCase().includes(q)).map(x=>{const f=x.feature_bundle||{},c=f.compilation?.compiled_feature,r=x.rule_bundle||{},v=x.validation;return '<article><h2>'+esc(x.id)+' · <b class="'+(v.passed?'ok':'bad')+'">'+(v.passed?'程序完了 / program complete':'실패')+'</b></h2><p>최초 Feature: '+esc(v.Feature_first_pass)+' / 최초 Rule: '+esc(v.Rule_first_pass)+'</p>'+(c?'<p>AI 목적: '+esc(f.proposal.purpose)+'</p><p>원본 AI 설명: '+esc(f.proposal.meaning)+'</p><small>자동 설명 검사 범위: '+esc(f.description_validation?.status)+'; 전체 자연어·업무 의미 증명 아님</small><p>출력 행: '+esc(c.plan.grain.table)+' / 타입: '+esc(c.dtype)+' / 단위: '+esc(c.unit)+'</p>'+detail('코드가 확인한 공식 계산 사실 (AI 작성 아님)',f.official_semantics)+detail('실제 측정',c.measurement.summary):'<p>'+esc(f.reason||f.status||'Feature 미완료')+'</p>')+detail('사용자 공개 입력·Rule 검토 목적',{request:x.public_request,rule_plan:x.public_rule_plan})+detail('실제 모델 제안·수정 이력',f)+detail('실행 가능한 검증 명세',x.validated_rules||[])+detail('보류·근거 요청·적용 불가',x.pending_decisions||[])+detail('Rule 원문·공식 조건 사실',r)+detail('검증·재생 보고서',v)+'</article>'}).join('')}
document.getElementById('search').oninput=draw;draw();</script></html>'''
 page=page.replace('PAYLOAD',data).replace('程序完了 / program complete','프로그램 완료')
 (folder/'demo.html').write_text(page)
