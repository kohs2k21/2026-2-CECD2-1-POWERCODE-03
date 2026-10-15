import {useEffect,useRef,useState} from 'react';
import {httpClient,HttpError} from '../../services/api/client';
type Mode='api'|'csv'|'paused';
type State={storedCounts:Record<string,number>;csvResume:null|{settings:{replayId:string;rate:number;repeat:number};position:number;status:string};enabled:boolean;sourceId:string;control:null|{mode:Mode;revision:number;runId:string|null;configVersionId:string|null;
 status:string|null;settings:{rate?:number;repeat?:number;replayId?:string;pollSeconds?:number}|null;
 position:number|null;accepted:number|null;duplicates:number|null;unidentified:number|null;lastError:string|null;workerHeartbeatAt:string|null};
 replays:{id:string;replayKey:string;recordCount:number;layerCounts:Record<string,number>}[];
 configurations:{id:string;mode:string|null;createdAt:string}[]};
const messages:Record<string,string>={input_revision_conflict:'다른 화면에서 입력 설정이 변경되었습니다. 최신 상태를 불러온 뒤 다시 선택하세요.',
 input_settings_changed_restart_required:'이 재생의 속도·횟수가 이전 실행과 다릅니다. 이전 설정으로 재개하거나 새 재생을 선택하세요.',
 csv_replay_not_ready:'먼저 서버에서 CSV를 등록하세요.',input_config_source_mismatch:'이 데이터 원천에 속하는 탐지 구성을 선택하세요.'};
const errorText=(e:unknown)=>e instanceof HttpError?messages[e.code??'']??e.code??e.message:e instanceof Error?e.message:'입력 상태를 확인하지 못했습니다.';
export function InputControls({sourceId}:{sourceId:string}) {
 const [state,setState]=useState<State|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [mode,setMode]=useState<Mode>('csv'),[replay,setReplay]=useState(''),[rate,setRate]=useState(10),[repeat,setRepeat]=useState(1);
 const [poll,setPoll]=useState(2),[restart,setRestart]=useState(false),[config,setConfig]=useState('');
 const version=useRef(0),busyRef=useRef(false),initialized=useRef(false),mounted=useRef(true);
 async function load(signal?:AbortSignal) {
  const current=++version.current;
  const result=await httpClient.get<State>('/api/inputs',{params:{sourceId},signal});
  if(!mounted.current||current!==version.current)return;
  setState(result);
  if(!initialized.current&&result.control){
   initialized.current=true;setMode(result.control.mode);setConfig(result.control.configVersionId??'');
   setReplay(result.control.settings?.replayId??result.csvResume?.settings.replayId??result.replays[0]?.id??'');
   setRate(result.control.settings?.rate??result.csvResume?.settings.rate??10);setRepeat(result.control.settings?.repeat??result.csvResume?.settings.repeat??1);setPoll(result.control.settings?.pollSeconds??2);
  }
 }
 useEffect(()=>{
  if(!sourceId)return;
  mounted.current=true;const controller=new AbortController();
  const update=()=>{if(!busyRef.current)void load(controller.signal).catch(e=>{if(!controller.signal.aborted)setError(errorText(e));});};
  update();const timer=setInterval(update,3000);
  return ()=>{mounted.current=false;version.current++;controller.abort();clearInterval(timer);};
 },[sourceId]);
 async function select(target:Mode) {
  if(!state?.control)return;
  busyRef.current=true;setBusy(true);setError('');version.current++;
  try{
   await httpClient.post(`/api/inputs/${sourceId}/mode`,{mode:target,expectedRevision:state.control.revision,
    ...(target==='api'?{pollSeconds:poll}:target==='csv'?{replayId:replay,rate,repeat,restart}:{}),
    ...(target==='paused'?{}:{configVersionId:config||null})});
   if(mounted.current){setRestart(false);await load();}
  }catch(e){if(mounted.current){setError(errorText(e));await load().catch(()=>{});}}
  finally{busyRef.current=false;if(mounted.current)setBusy(false);}
 }
 if(!sourceId)return null;
 return <section className="pipeline-card">
  <h3>입력 선택 · API / CSV</h3>
  <p>입력만 교체하며 같은 원천의 피처 생성·데이터 변환·Rule 추천 및 선택·모델 학습·탐지 경로를 사용합니다.</p>
  {error&&<p role="alert">{error}</p>}
  {state&&!state.enabled?<p>이 원천은 입력 전환에 등록되어 있지 않습니다. Collector를 시작하거나 입력 초기화를 실행하세요.</p>:null}
  {state?.control&&<>
   <p>현재 입력: <strong>{state.control.mode==='api'?'API':state.control.mode==='csv'?'CSV':'일시정지'}</strong> · 실행 상태: {state.control.status??'대기'}</p>
   <p>원천 데이터: T {state.storedCounts.T??0} · P {state.storedCounts.P??0} · M {state.storedCounts.M??0} · BODY 참조 {state.storedCounts.B??0}</p>
   <p>누적 저장 {state.control.accepted??0}행 · 중복 {state.control.duplicates??0}행 · 식별자 부족 {state.control.unidentified??0}행</p>
   {state.control.lastError&&<p role="alert">입력 오류: {state.control.lastError}. 다른 모드로 자동 전환하지 않습니다.</p>}
   {!state.control.workerHeartbeatAt&&<p>Collector 처리 기록이 아직 없습니다. 모드 선택 후 실제 저장 건수를 확인하세요.</p>}
   <div className="pipeline-fields">
    <label>입력 방식<select value={mode} onChange={e=>setMode(e.target.value as Mode)}><option value="api">API 실시간 조회</option><option value="csv">CSV 순차 재생</option><option value="paused">일시정지</option></select></label>
    <label>공통 탐지 구성<select value={config} onChange={e=>setConfig(e.target.value)}><option value="">승인·적용된 운영 구성 사용</option>
     {state.configurations.map(c=><option key={c.id} value={c.id}>검증용 후보 · {c.mode??'이전 구성'} · {new Date(c.createdAt).toLocaleString()}</option>)}
    </select></label>
    {mode==='api'&&<label>API 조회 간격(초)<input type="number" min="0.1" max="3600" step="0.1" value={poll} onChange={e=>setPoll(Number(e.target.value))}/></label>}
    {mode==='csv'&&<>
     <label>등록된 CSV<select value={replay} onChange={e=>setReplay(e.target.value)}><option value="">재생할 파일 묶음 선택</option>{state.replays.map(r=><option key={r.id} value={r.id}>{r.replayKey} · {r.recordCount.toLocaleString()}행</option>)}</select></label>
     <label>초당 전송 행 수<input type="number" min="0.1" max="10000" step="0.1" value={rate} onChange={e=>setRate(Number(e.target.value))}/></label>
     <label>반복 횟수<input type="number" min="1" max="100" value={repeat} onChange={e=>setRepeat(Number(e.target.value))}/></label>
     <label><input type="checkbox" checked={restart} onChange={e=>setRestart(e.target.checked)}/>처음부터 새 재생 실행</label>
    </>}
   </div>
   {mode==='csv'&&<p>T·P·M·BODY 참조 행을 합산한 전송 속도입니다. 같은 묶음·속도·횟수는 중단 위치부터 재개하고, 완료된 재생은 새 실행을 선택해야 다시 보냅니다.</p>}
   {config?<p>선택한 학습 완료 후보로 검증합니다. 운영 활성 구성이나 정확도 검증·승인 상태는 변경하지 않습니다.</p>:<p>운영 구성이 적용되기 전에도 데이터를 축적할 수 있습니다. 탐지는 구성이 준비될 때까지 큐에서 대기합니다.</p>}
   <button disabled={busy||(mode==='csv'&&(!replay||!Number.isFinite(rate)||rate<.1||rate>10000||!Number.isInteger(repeat)||repeat<1||repeat>100))||(mode==='api'&&(!Number.isFinite(poll)||poll<.1||poll>3600))} onClick={()=>void select(mode)}>선택한 입력 적용</button>
   <button disabled={busy} onClick={()=>void select('paused')}>입력 일시정지</button>
   <p>이미 등록된 작업은 고정된 구성으로 처리를 마칩니다. BODY 원문은 현재 수집하지 않으며 Message 모니터링과 구분해 참조만 보관합니다.</p>
  </>}
 </section>;
}
