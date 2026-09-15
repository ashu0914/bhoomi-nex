import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const stages = ['Input', 'Preprocess', 'AI understand', 'Validate', 'Human verify', 'Trusted record'];
const sample = { id: 'REC20260914-001', owner: 'Ramesh Kumar', khasra: '123/2', village: 'Aurangabad', district: 'Palwal', area: '2.50 Acres', confidence: 87 };

function App() {
  const [page, setPage] = useState('overview');
  const [language, setLanguage] = useState('English');
  const [record, setRecord] = useState(sample);
  const [verified, setVerified] = useState(false);
  const [menu, setMenu] = useState(false);
  const verify = () => { setVerified(true); setRecord({ ...record, confidence: 100 }); };
  return <main className="shell">
    <aside className="side">
      <div className="brand"><span className="brand-orbit">◌</span><span><b>BHOOMI</b><em>-AI</em><small>BhoomiNex · SIH 2026</small></span></div>
      <div className="nav-label">WORKSPACE</div>
      {[['overview','◫','Command centre'],['upload','↑','Process document'],['review','✓','Human verification'],['records','▤','Trusted records'],['analytics','⌁','Model insights']].map(([id,icon,label]) => <button key={id} className={'nav '+(page===id?'active':'')} onClick={()=>setPage(id)}><i>{icon}</i>{label}</button>)}
      <div className="side-note"><span>SIH26018</span><b>Intelligent land-record digitization and validation</b><small>Software · Smart Automation</small></div>
    </aside>
    <section className="workspace">
      <header><div><span className="eyebrow">SMART INDIA HACKATHON 2026</span><h1>{page==='overview'?'Trust dashboard':page==='upload'?'Process a land document':page==='review'?'Human verification':'Trusted land data'}</h1></div><div className="head-actions"><div className="language-wrap"><button className="language" onClick={()=>setMenu(!menu)}>◎ {language}⌄</button>{menu&&<div className="language-menu">{['English','हिन्दी','ਪੰਜਾਬੀ'].map(l=><button key={l} onClick={()=>{setLanguage(l);setMenu(false)}}>{l}</button>)}</div>}</div><button className="avatar">AK</button></div></header>
      {page==='overview' && <Overview setPage={setPage} />}
      {page==='upload' && <Upload setPage={setPage} setRecord={setRecord} />}
      {page==='review' && <Review record={record} verified={verified} verify={verify} />}
      {(page==='records'||page==='analytics') && <Records record={record} verified={verified} />}
    </section>
  </main>
}
function Overview({setPage}) { return <div className="page">
  <section className="hero"><div><span className="eyebrow">FROM DOCUMENT TO TRUSTED RECORD</span><h2>Land records, understood.<br/><em>Not just digitized.</em></h2><p>BHOOMI-AI extracts, validates and routes only uncertain fields to officials—creating data that can be trusted.</p><div className="hero-actions"><button className="primary" onClick={()=>setPage('upload')}>Process a document →</button><button className="ghost" onClick={()=>setPage('review')}>Open verification queue</button></div></div><div className="hero-art"><div className="document"><span className="doc-tag">LEGACY RECORD</span><b>Jamabandi / Land Record</b><i>Owner  ·  Survey  ·  Area  ·  Village</i><div className="scan-lines"></div></div><div className="trust-ring"><b>87%</b><small>field confidence</small></div></div></section>
  <section className="metrics">{[['128','Documents processed'],['96','Trusted records'],['12','Need human review'],['91%','Average confidence']].map(([n,t])=><div className="metric" key={t}><b>{n}</b><span>{t}</span></div>)}</section>
  <section className="process"><div className="section-title"><div><span className="eyebrow">THE VERIFICATION ENGINE</span><h3>One controlled path to trusted data</h3></div><span className="legend"><i></i> AI complete <i></i> Human decision</span></div><div className="stage-list">{stages.map((s,i)=><div className={'stage '+(i<3?'done':i===3?'current':'')} key={s}><span>0{i+1}</span><b>{s}</b><small>{['Documents enter the secure queue','Deskew, denoise, quality score','OCR + layout + entity extraction','Rules, duplicates and conflicts','Review only flagged fields','Audit-ready structured record'][i]}</small></div>)}</div></section>
</div>}
function Upload({setPage,setRecord}) { const [name,setName]=useState(''); return <div className="page two-col"><section className="upload-card"><span className="eyebrow">01 · SECURE INPUT</span><h2>Bring a record into the trust pipeline.</h2><label className="drop"><input type="file" onChange={e=>setName(e.target.files[0]?.name||'')}/><b>↑ Drop scan or select file</b><span>{name||'PDF, JPG, PNG · legacy documents and cadastral maps'}</span></label><button className="primary" onClick={()=>{setRecord({...sample,id:'REC20260914-002'});setPage('review')}}>Extract and validate →</button></section><section className="pipeline-card"><span className="eyebrow">WHAT HAPPENS NEXT</span>{['Document quality scoring','Multilingual OCR and layout analysis','Field-level confidence scoring','Rules, conflicts and duplicate checks','Human review only where needed'].map((x,i)=><div className="check" key={x}><b>0{i+1}</b><span>{x}</span></div>)}</section></div> }
function Review({record,verified,verify}) { return <div className="page review-grid"><section><span className="eyebrow">05 · HUMAN VERIFICATION</span><h2>Confirm only what AI is uncertain about.</h2><div className="source"><span className="doc-tag">SOURCE DOCUMENT</span><b>Jamabandi record</b><p>Highlighted fields require an officer decision. Every change is stored in the audit trail.</p><div className="source-lines"></div></div></section><section className="review-card"><div className="review-head"><div><span className="eyebrow">{verified?'TRUSTED RECORD':'AI EXTRACTION'}</span><h3>{record.id}</h3></div><span className={'status '+(verified?'trusted':'review')}>{verified?'✓ Trusted':'Needs review'}</span></div>{[['Owner name',record.owner,'high'],['Khasra number',record.khasra,'high'],['Village',record.village,'medium'],['District',record.district,'high'],['Area',record.area,'medium']].map(([l,v,c])=><label className={'field '+c} key={l}><span>{l}<small>{c==='high'?'High confidence':'Review suggested'}</small></span><input defaultValue={v} readOnly={verified}/></label>)}<div className="review-actions">{!verified?<><button className="ghost">Request correction</button><button className="primary" onClick={verify}>Approve trusted record →</button></>:<button className="primary full">View audit trail →</button>}</div></section></div>}
function Records({record,verified}) { return <div className="page"><span className="eyebrow">OUTPUT · API / GIS READY</span><h2>Structured, traceable land data.</h2><div className="records-card"><div className="record-row header-row"><span>Record</span><span>Location</span><span>Confidence</span><span>Status</span></div><div className="record-row"><b>{record.id}<small>{record.owner} · Khasra {record.khasra}</small></b><span>{record.village}, {record.district}</span><span>{verified?'100':record.confidence}%</span><span className={'status '+(verified?'trusted':'review')}>{verified?'Trusted':'Needs review'}</span></div></div></div>}
createRoot(document.getElementById('root')).render(<App/>);
