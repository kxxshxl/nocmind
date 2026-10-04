import React, {useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid} from 'recharts';
import './styles.css';

const API = (import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8000/api/v1`).replace(/\/$/, '');
type Incident={id:string;title:string;region:string;severity:string;service:string;status:string;symptoms:string[];rca?:any;remediation?:any;final_report?:string;created_at?:string};

function headers(token:string){return token?{'Authorization':`Bearer ${token}`}:{}};
async function api<T>(
    token: string,
    path: string,
    opts: RequestInit = {}
): Promise<T> {
    const requestHeaders = new Headers(opts.headers);

    if (token) {
        requestHeaders.set("Authorization", `Bearer ${token}`);
    }

    if (opts.body) {
        requestHeaders.set("Content-Type", "application/json");
    }

    const response = await fetch(`${API}${path}`, {
        ...opts,
        headers: requestHeaders,
    });

    if (response.status === 401 && token) {
        localStorage.removeItem('nocmind_token');
        window.location.reload();
    }

    if (!response.ok) {
        const text = await response.text();
        let detail = text;
        try { detail = JSON.parse(text).detail ?? text; } catch { /* plain-text error */ }
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }

    return response.json() as Promise<T>;
}
async function post<T>(token:string,path:string,body?:unknown){return api<T>(token,path,{method:'POST',body:body===undefined?undefined:JSON.stringify(body)})}

const scenarios=[['router-overload','Router overload'],['dns-failure','DNS failure'],['database-latency','Database latency'],['service-crash','Service crash'],['congestion','Congestion'],['lb-imbalance','LB imbalance'],['auth-degradation','Auth degradation'],['complex','Complex failure']];

function App(){
 const [token,setToken]=useState(localStorage.getItem('nocmind_token')||'');
 const [username,setUsername]=useState('admin'); const [password,setPassword]=useState('nocmind-admin'); const [loginError,setLoginError]=useState('');
 const [incidents,setIncidents]=useState<Incident[]>([]); const [selected,setSelected]=useState<Incident|null>(null); const [result,setResult]=useState<any>(null); const [telemetry,setTelemetry]=useState<any>(null); const [tab,setTab]=useState('incident'); const [ragQuery,setRagQuery]=useState('router overload packet loss'); const [ragResults,setRagResults]=useState<any[]>([]); const [evals,setEvals]=useState<any[]>([]); const [busy,setBusy]=useState(false); const [error,setError]=useState('');
 const refresh=async()=>{if(!token)return; try{setIncidents(await api<Incident[]>(token,'/incidents'));setTelemetry(await api<any>(token,'/simulator/state'));setEvals(await api<any[]>(token,'/evaluations'));}catch(e:any){setError(e.message)}};
 useEffect(()=>{if(token){refresh();const id=setInterval(()=>{api<any>(token,'/simulator/state').then(setTelemetry).catch(()=>{})},3000);return()=>clearInterval(id)}},[token]);
 const metrics=useMemo(()=>{const h=Array.isArray(telemetry?.history)?telemetry.history:[]; return h.slice(-20).map((x:any,i:number)=>({name:new Date(x.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}), cpu:x.router_cpu??0, latency:x.api_latency_ms??0, packetLoss:x.packet_loss_pct??0, errors:x.api_error_rate_pct??0, _i:i}));},[telemetry]);
 const login=async()=>{setLoginError('');try{const x=await post<any>('', '/auth/login',{username,password});localStorage.setItem('nocmind_token',x.access_token);setToken(x.access_token)}catch(e:any){setLoginError(e.message)}};
 const logout=()=>{localStorage.removeItem('nocmind_token');setToken('');setSelected(null);setResult(null)};
 const trigger=async(scenario:string)=>{setError('');await post<any>(token,`/simulator/scenarios/${scenario}`);const sym:any={
   'router-overload':['high latency','packet loss','router saturation'],'dns-failure':['DNS timeout','high DNS latency','application errors'],'database-latency':['database latency','slow queries','high API latency'],'service-crash':['service unavailable','error spike'],'congestion':['high latency','packet loss','throughput saturation'],'lb-imbalance':['backend overload','high latency'],'auth-degradation':['authentication latency','application errors'],'complex':['high latency','packet loss','database latency']}[scenario];
   const created=await post<Incident>(token,'/incidents',{title:scenario.replaceAll('-',' '),region:'Mumbai',severity:scenario==='service-crash'||scenario==='complex'?'SEV-1':'SEV-2',service:'API Gateway',symptoms:sym}); await refresh();setSelected(created);setResult(null);setTab('incident');
 };
 const investigate=async()=>{if(!selected)return;setBusy(true);setError('');try{const x=await post<any>(token,`/incidents/${selected.id}/investigate`);setResult(x);await refresh()}catch(e:any){setError(e.message)}finally{setBusy(false)}};
 const selectIncident=async(i:Incident)=>{setSelected(i);setResult(null);try{const d=await api<Incident>(token,`/incidents/${i.id}`);setSelected(d);if(d.rca&&d.remediation){setResult({...d,events:[],tool_calls:[]});}}catch(e:any){setError((e as Error).message)}};
 const remediate=async()=>{if(!selected)return;setBusy(true);setError('');try{await post<any>(token,`/incidents/${selected.id}/approve-remediation`);const x=await post<any>(token,`/incidents/${selected.id}/execute-remediation`);setResult((prev:any)=>({...prev,...x}));const d=await api<Incident>(token,`/incidents/${selected.id}`);setSelected(d);setResult((prev:any)=>({...prev,...d,...x}));await refresh()}catch(e:any){setError(e.message)}finally{setBusy(false)}};
 const runRag=async()=>{try{setRagResults(await post<any[]>(token,'/rag/query',{query:ragQuery,top_k:12,rerank_k:5}))}catch(e:any){setError(e.message)}};
 const runEval=async()=>{setBusy(true);try{await post<any>(token,'/evaluations/run');await refresh();setTab('evaluation')}catch(e:any){setError(e.message)}finally{setBusy(false)}};
 if(!token)return <div className="login-shell"><div className="login-card"><div className="brand">NOCMind</div><p>Autonomous Telecom NOC AI</p><input value={username} onChange={e=>setUsername(e.target.value)} placeholder="Username"/><input type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="Password"/><button className="primary full" onClick={login}>Sign in</button>{loginError&&<div className="error">{loginError}</div>}<small>Demo accounts: admin / nocmind-admin · operator / nocmind-operator · viewer / nocmind-viewer (if enabled)</small></div></div>
 return <div className="app"><header><div><div className="brand">NOCMind</div><span>Autonomous Telecom NOC AI</span></div><div className="header-actions"><div className="pill">● Simulation online</div><button className="ghost" onClick={logout}>Sign out</button></div></header>
 <div className="tabs"><button className={tab==='incident'?'active':''} onClick={()=>setTab('incident')}>NOC Console</button><button className={tab==='rag'?'active':''} onClick={()=>setTab('rag')}>RAG Explorer</button><button className={tab==='evaluation'?'active':''} onClick={()=>setTab('evaluation')}>Evaluation</button><button className="eval-btn" onClick={runEval} disabled={busy}>Run benchmark</button></div>
 {error&&<div className="error banner">{error}</div>}
 {tab==='incident'?<main className="layout"><aside><div className="side-title">ACTIVE INCIDENTS</div>{incidents.map(i=><button key={i.id} className={'incident '+(selected?.id===i.id?'active':'')} onClick={()=>selectIncident(i)}><div><b>{i.id}</b><span className={'sev '+i.severity}>{i.severity}</span></div><strong>{i.title}</strong><small>{i.region} · {i.service} · {i.status}</small></button>)}<div className="side-title space">TRIGGER SCENARIO</div>{scenarios.map(([id,label])=><button key={id} className="demo" onClick={()=>trigger(id)}>{label}</button>)}</aside>
 <section className="content">{selected?<><div className="hero"><div><p className="eyebrow">INCIDENT {selected.id}</p><h1>{selected.title}</h1><p>{selected.region} · {selected.service} · <span className="status">{result?.status||selected.status}</span></p></div><button className="primary" onClick={investigate} disabled={busy}>{busy?'Running agents…':'Investigate with AI'}</button></div>
 <div className="grid2"><div className="card"><div className="card-title">LIVE TELEMETRY</div><ResponsiveContainer width="100%" height={240}><LineChart data={metrics}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="name" hide/><YAxis/><Tooltip/><Line type="monotone" dataKey="latency" name="API latency (ms)"/><Line type="monotone" dataKey="cpu" name="Router CPU (%)"/><Line type="monotone" dataKey="packetLoss" name="Packet loss (%)"/></LineChart></ResponsiveContainer></div><div className="card"><div className="card-title">SYMPTOMS</div><div className="chips">{selected.symptoms.map(s=><span key={s}>{s}</span>)}</div><div className="stats"><div><small>SEVERITY</small><b>{selected.severity}</b></div><div><small>STATE</small><b>{result?.status||selected.status}</b></div><div><small>TOOLS</small><b>{result?.tool_calls?.length||0}</b></div></div></div></div>
 {result&&<><div className="card"><div className="card-title">AGENT INVESTIGATION{result.agent_mode&&<span className={'mode '+result.agent_mode}>{result.agent_mode==='llm'?'LLM-driven':result.agent_mode==='degraded'?'Degraded (LLM unavailable)':'Offline demo'}</span>}</div>{result.llm_errors?.length>0&&<div className="error">LLM errors handled: {result.llm_errors.map((e:any)=>e.stage).join(', ')}</div>}<div className="workflow">{['TRIAGE','PLANNER','INVESTIGATOR','RCA','REMEDIATION'].map((x,idx)=><div key={x} className="workflow-step done"><span>{idx+1}</span>{x}</div>)}</div><div className="timeline">{result.events?.map((e:any,idx:number)=><div className="event" key={idx}><div className="dot"/><div><b>{e.event_type}</b><p>{e.message}</p></div></div>)}</div></div>
 <div className="card"><div className="card-title">INCIDENT REPORT</div><pre className="report">{result.final_report||'Report will appear after investigation.'}</pre></div><div className="grid2"><div className="card"><div className="card-title">ROOT CAUSE ANALYSIS</div><h2>{result.rca?.root_cause}</h2><div className="confidence">Confidence <b>{Math.round((result.rca?.confidence||0)*100)}%</b>{result.rca?.method&&<small className="muted"> · {result.rca.method==='llm'?`LLM (${result.rca.model})`:'rule-based fallback'}</small>}</div><p className="muted">{result.rca?.summary}</p><h4>Evidence</h4><div className="evidence">{result.rca?.evidence?.slice(0,5).map((e:any)=><div className="e" key={e.source_id}><b>{e.title}</b><small>{e.source_type} · {e.section||'live evidence'}</small><p>{e.text}</p></div>)}</div></div><div className="card"><div className="card-title">REMEDIATION</div><h2>{result.remediation?.action}</h2><p className="muted">{result.remediation?.reason}</p><div className="stats"><div><small>TARGET</small><b>{result.remediation?.target}</b></div><div><small>RISK</small><b>{result.remediation?.risk_level}</b></div></div>{result.status!=='RESOLVED'&&<button className="approve" onClick={remediate} disabled={busy}>Approve & execute</button>}{result.status==='RESOLVED'&&<div className="success">✓ Remediation validated and incident resolved</div>}</div></div></>}
 </>:<div className="empty"><h1>Pick an incident</h1><p>Trigger one of the deterministic telecom failures to run a complete investigation.</p></div>}</section></main>
 :tab==='rag'?<section className="page"><div className="page-head"><div><p className="eyebrow">RETRIEVAL PIPELINE</p><h1>RAG Explorer</h1><p>Inspect hybrid retrieval, RRF ranking and reranking outputs.</p></div></div><div className="card"><div className="search"><input value={ragQuery} onChange={e=>setRagQuery(e.target.value)}/><button className="primary" onClick={runRag}>Search</button></div>{ragResults.length>0&&<div className="evidence rag-list">{ragResults.map(r=><div className="e" key={r.chunk_id}><div className="row"><b>{r.metadata?.title||r.document_id}</b><span>rerank {Number(r.rerank_score||0).toFixed(3)}</span></div><small>{r.document_id} · {r.section} · dense {Number(r.dense_score||0).toFixed(3)} · lexical {Number(r.lexical_score||0).toFixed(3)}</small><p>{r.text}</p></div>)}</div>}</div></section>
 :<section className="page"><div className="page-head"><div><p className="eyebrow">MODEL EVALUATION</p><h1>Evaluation</h1><p>Benchmark retrieval, RCA and agent execution against deterministic ground truth.</p></div><button className="primary" onClick={runEval} disabled={busy}>Run benchmark</button></div>{evals.map((r:any)=><EvalRun key={r.id} run={r}/>)}{!evals.length&&<div className="card muted">No evaluation runs yet.</div>}</section>}
 </div>}
const fmt=(v:any)=>v===null||v===undefined?'—':typeof v==='number'?(Number.isInteger(v)?String(v):v.toFixed(3)):typeof v==='boolean'?(v?'yes':'no'):String(v);
function KV({title,data,note}:{title:string;data:any;note?:string}){if(!data)return null;return <div className="card"><div className="card-title">{title}</div>{note&&<small className="muted">{note}</small>}<table className="kv"><tbody>{Object.entries(data).filter(([,v])=>typeof v!=='object'||v===null).map(([k,v])=><tr key={k}><td>{k}</td><td>{fmt(v)}</td></tr>)}</tbody></table></div>}
function EvalRun({run}:{run:any}){const m=run.metrics||{};const gen=m.generation||{};const retrieval=m.retrieval||{};const cols=Object.keys(Object.values(retrieval)[0]||{});
 return <div className="card"><div className="row"><h2>{run.name}</h2><small>{run.created_at}</small></div>
 {m.run_config?<><div className="grid2"><KV title="RCA (deterministic)" data={m.rca}/><KV title="AGENT (deterministic)" data={m.agent}/><KV title="GENERATION · DETERMINISTIC" data={gen.deterministic}/><KV title="GENERATION · MODEL-JUDGED" data={gen.model_judged} note="LLM-as-judge; unjudged cases are omitted, never imputed."/><KV title="LATENCY & TOKENS" data={m.latency}/><KV title="RUN CONFIG" data={m.run_config}/></div>
 <div className="card"><div className="card-title">RETRIEVAL (deterministic, k=5)</div><table className="kv"><thead><tr><th>variant</th>{cols.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{Object.entries(retrieval).map(([name,vals]:any)=><tr key={name}><td>{name}</td>{cols.map(c=><td key={c}>{fmt(vals[c])}</td>)}</tr>)}</tbody></table></div>
 <div className="card"><div className="card-title">PER SCENARIO</div><table className="kv"><thead><tr><th>scenario</th><th>predicted</th><th>correct</th><th>rca</th><th>agent</th><th>remediation</th><th>resolved</th><th>tools</th><th>faithfulness</th><th>latency ms</th></tr></thead><tbody>{(m.per_scenario||[]).map((r:any)=><tr key={r.scenario}><td>{r.scenario}</td><td>{r.predicted_root_cause}</td><td>{fmt(r.correct)}</td><td>{r.rca_method}</td><td>{r.agent_mode}</td><td>{r.remediation}</td><td>{fmt(r.resolved_after_remediation)}</td><td>{r.tools?.length}</td><td>{fmt(r.judge?.faithfulness)}</td><td>{fmt(r.latency_ms)}</td></tr>)}</tbody></table></div></>:null}
 <details><summary>Raw metrics JSON</summary><pre>{JSON.stringify(m,null,2)}</pre></details></div>}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
