import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { createPipelineJob, fetchPipelineJob, fetchProcessDecisions, type PipelineJob, type ProcessDecision } from '../../services/api/pipeline.api';
import { consumeDecisionStream } from '../../services/api/decision-stream.api';
import './pipeline.css';
import { DecisionDiagnostics } from './DecisionDiagnostics';

export function SavedDecisions() {
  const [decisions,setDecisions] = useState<ProcessDecision[]>([]);
  const [jobs,setJobs] = useState<Record<string,PipelineJob>>({});
  const [error,setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let after = '0';
    let retry: ReturnType<typeof setTimeout> | undefined;
    void fetchProcessDecisions().then(rows => {if (!controller.signal.aborted) setDecisions(rows);}).catch(reason => setError(String(reason)));
    const connect = async () => {
      try {
        await consumeDecisionStream(controller.signal,after,(value,id) => {
          after=id;setDecisions(previous => [value,...previous.filter(item => item.id!==value.id)].slice(0,50));
        });
      } catch (reason) {if (!controller.signal.aborted) setError(String(reason));}
      if (!controller.signal.aborted) retry=setTimeout(() => {void connect();},3000);
    };
    void connect();
    return () => {controller.abort();if (retry) clearTimeout(retry);};
  },[]);
  useEffect(() => {
    const pending=Object.entries(jobs).filter(([,job]) => ['queued','running'].includes(job.status));
    if (!pending.length) return;
    const timer=setInterval(() => {
      for (const [id,job] of pending) void fetchPipelineJob(job.id).then(updated => setJobs(previous => ({...previous,[id]:updated}))).catch(reason => setError(String(reason)));
    },3000);
    return () => clearInterval(timer);
  },[jobs]);
  async function explain(decision:ProcessDecision) {
    try {
      const job=await createPipelineJob({kind:'explain',sourceId:decision.sourceId,decisionId:decision.id});
      setJobs(previous => ({...previous,[decision.id]:job}));
    } catch (reason) {setError(String(reason));}
  }
  return <main className="pipeline-workspace">
    <h2>저장된 탐지 판정</h2><Link to="/analysis">이상 분석으로</Link>
    <p>Rule·ML 판정의 저장 결과입니다. 설명은 요청할 때 생성하며, 원인은 확정하지 않습니다.</p>
    {error && <p role="alert">{error}</p>}
    {decisions.length===0 && <p>저장된 판정이 없습니다.</p>}
    {decisions.map(decision => <section className="pipeline-card" key={decision.id}>
      {decision.evidence?.input && <p>{decision.evidence.input.mode === "csv" ? "CSV 재생" : "API 수집"} · {decision.evidence.input.configurationScope === "input-validation" ? "선택한 후보 구성으로 검증" : "운영 구성"}</p>}
      {decision.evidence?.demo && <p>과거 데이터 재생 데모 · {decision.evidence.demo.runKey}</p>}
      <h3>{decision.decisionCode ?? '판정 코드 없음'}</h3>
      <p>{decision.status} · Rule {decision.ruleStatus} · ML {decision.mlStatus}</p>
      <DecisionDiagnostics decision={decision} />
      <p>{decision.evidence?.anomalous===true ? '이상 확인' : decision.evidence?.anomalous===false ? '선택 경로 정상' : '정상 여부 미확인'} ·
        모드 {decision.evidence?.requestedMode ?? '이전 구성'}</p>
      <p>{new Date(decision.decidedAt).toLocaleString()}</p>
      <button disabled={!!decision.evidence?.demo || ['queued','running'].includes(jobs[decision.id]?.status)} onClick={() => {void explain(decision);}}>저장된 근거 설명 요청</button>
      {decision.evidence?.demo && <p>CPU 모델 판정입니다. 이 데모에서는 LLM 설명을 실행하지 않습니다.</p>}
      {jobs[decision.id] && <p>설명 작업: {jobs[decision.id].status} {jobs[decision.id].errorCode}</p>}
      {jobs[decision.id]?.resultSummary?.facts?.map(fact => <p key={fact.id}>{fact.name}: {JSON.stringify(fact.value)}</p>)}
      {jobs[decision.id]?.resultSummary?.nextChecks?.map(check => <p key={check.code}>{check.text}</p>)}
    </section>)}
  </main>;
}
