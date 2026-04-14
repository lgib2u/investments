"""
Investments Backend — FastAPI, port 3300
Serves the investments React dashboard and all /api/investments/* endpoints.
Completely separate from horde-backend so it survives Horde updates.
"""
import asyncio
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TRADER_DIR  = Path.home() / "bot" / "trader"
CLAUDE_BIN  = Path.home() / ".local" / "bin" / "claude"
CHAT_CSV    = TRADER_DIR / "chat_history.csv"
CHAT_FIELDS = ["timestamp", "role", "content"]
PW_HASH     = hashlib.sha256(b"luna21").hexdigest()

# ── Dashboard HTML ─────────────────────────────────────────────────────────────
INVESTMENTS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="icon" href="/favicon.ico"/>
<link rel="apple-touch-icon" sizes="192x192" href="/logo192.png"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;700&display=swap" rel="stylesheet"/>
<meta name="theme-color" content="#0d1117"/>
<meta name="description" content="Investments — personal trading dashboard. Portfolio positions, trade history, and AI signals."/>
<title>Investments</title>
<script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;
  --text:#c9d1d9;--muted:#8b949e;
  --green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff;
  --font:"Courier New",monospace;
}
html,body,#root{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border)}
</style>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
const {useState,useEffect,useRef,useCallback} = React;
const C={bg:"#0d1117",bg2:"#161b22",bg3:"#21262d",border:"#30363d",text:"#c9d1d9",muted:"#8b949e",green:"#3fb950",red:"#f85149",yellow:"#d29922",blue:"#58a6ff"};

function fmt(n,d=2){if(n==null||n==="")return"—";const v=parseFloat(n);return isNaN(v)?"—":v.toFixed(d)}
function sign(v){return parseFloat(v)>=0?"+":""}
function pnlColor(v){const n=parseFloat(v);return n>0?C.green:n<0?C.red:C.text}
const TH={color:C.muted,fontSize:10,textTransform:"uppercase",letterSpacing:1,padding:"6px 10px",textAlign:"left",borderBottom:`1px solid ${C.border}`,whiteSpace:"nowrap"};
const TD={padding:"6px 10px",borderBottom:`1px solid ${C.border}22`,verticalAlign:"top"};
const hov=(e,on)=>Array.from(e.currentTarget.cells).forEach(c=>c.style.background=on?C.bg2:"");

function Badge({status}){
  const m={closed_profit:{bg:"rgba(63,185,80,.15)",c:C.green,b:"rgba(63,185,80,.3)",l:"profit"},
           closed_loss:{bg:"rgba(248,81,73,.15)",c:C.red,b:"rgba(248,81,73,.3)",l:"loss"},
           open:{bg:"rgba(88,166,255,.15)",c:C.blue,b:"rgba(88,166,255,.3)",l:"open"}}[status]
         ||{bg:"rgba(139,148,158,.1)",c:C.muted,b:"rgba(139,148,158,.3)",l:status||"—"};
  return<span style={{display:"inline-block",padding:"1px 5px",fontSize:10,textTransform:"uppercase",background:m.bg,color:m.c,border:`1px solid ${m.b}`}}>{m.l}</span>;
}

// ── Portfolio chart ────────────────────────────────────────────────────────────
const PERIODS={day:86400,week:604800,month:2592000,year:31536000,all:Infinity};
function PortfolioChart({trades}){
  const [period,setPeriod]=useState("all");
  const canvasRef=useRef(null);
  const chartRef=useRef(null);
  useEffect(()=>{
    if(!canvasRef.current)return;
    const cutoff=period==="all"?new Date(0):new Date(Date.now()-PERIODS[period]*1000);
    const closed=[...trades].filter(t=>t.closed_at&&t.pnl_usd&&new Date(t.closed_at)>=cutoff).sort((a,b)=>a.closed_at.localeCompare(b.closed_at));
    let cum=0; const labels=[],data=[];
    closed.forEach(t=>{cum+=parseFloat(t.pnl_usd||0);labels.push(t.closed_at.slice(0,10));data.push(parseFloat(cum.toFixed(2)));});
    if(!labels.length){labels.push("—");data.push(0);}
    if(chartRef.current)chartRef.current.destroy();
    const up=data[data.length-1]>=0;
    chartRef.current=new Chart(canvasRef.current,{
      type:"line",
      data:{labels,datasets:[{data,borderColor:up?C.green:C.red,backgroundColor:up?"rgba(63,185,80,0.07)":"rgba(248,81,73,0.07)",borderWidth:2,pointRadius:closed.length<30?3:0,pointBackgroundColor:up?C.green:C.red,fill:true,tension:0.3}]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>`$${c.raw.toFixed(2)}`}}},
        scales:{x:{ticks:{color:C.muted,font:{size:10,family:"Courier New"},maxTicksLimit:8},grid:{color:"rgba(48,54,61,0.4)"},border:{color:C.border}},
                y:{ticks:{color:C.muted,font:{size:10,family:"Courier New"},callback:v=>`$${v}`},grid:{color:"rgba(48,54,61,0.4)"},border:{color:C.border}}}}
    });
    return()=>{if(chartRef.current)chartRef.current.destroy();};
  },[trades,period]);
  return<div style={{background:C.bg2,border:`1px solid ${C.border}`,margin:"8px 12px",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"center",marginBottom:8,gap:4}}>
      <span style={{color:C.muted,fontSize:10,textTransform:"uppercase",letterSpacing:1,flex:1}}>Portfolio Performance</span>
      {Object.keys(PERIODS).map(p=><button key={p} onClick={()=>setPeriod(p)} style={{background:period===p?C.bg3:"transparent",border:`1px solid ${period===p?C.blue:C.border}`,color:period===p?C.blue:C.muted,padding:"2px 7px",cursor:"pointer",fontFamily:"inherit",fontSize:10,textTransform:"uppercase"}}>{p}</button>)}
    </div>
    <div style={{height:155}}><canvas ref={canvasRef}/></div>
  </div>;
}

// ── Tables ─────────────────────────────────────────────────────────────────────
function PositionsTab({data}){
  if(!data.length)return<div style={{color:C.muted,padding:20,textAlign:"center",fontSize:12}}>No open positions</div>;
  return<table style={{width:"100%",borderCollapse:"collapse"}}>
    <thead><tr>{["Ticker","Entry","Current","P&L $","P&L %","Stop","Target","Invested","Score"].map(h=><th key={h} style={TH}>{h}</th>)}</tr></thead>
    <tbody>{data.map((p,i)=>{const pl=p.live_pnl_usd,plp=p.live_pnl_pct;return<tr key={i} onMouseEnter={e=>hov(e,true)} onMouseLeave={e=>hov(e,false)}>
      <td style={{...TD,color:C.blue,fontWeight:"bold"}}>{p.ticker||"—"}</td>
      <td style={TD}>${fmt(p.entry_price)}</td><td style={TD}>{p.current_price!=null?`$${fmt(p.current_price)}`:"—"}</td>
      <td style={{...TD,color:pnlColor(pl)}}>{pl!=null?sign(pl)+`$${fmt(pl)}`:"—"}</td>
      <td style={{...TD,color:pnlColor(plp)}}>{plp!=null?sign(plp)+fmt(plp)+"%":"—"}</td>
      <td style={TD}>${fmt(p.stop_loss)}</td><td style={TD}>${fmt(p.take_profit)}</td>
      <td style={TD}>${fmt(p.invested_usd)}</td><td style={TD}>{p.sentiment_score||"—"}</td>
    </tr>;})}</tbody></table>;
}

function TradesTab({data}){
  if(!data.length)return<div style={{color:C.muted,padding:20,textAlign:"center",fontSize:12}}>No trades yet</div>;
  return<table style={{width:"100%",borderCollapse:"collapse"}}>
    <thead><tr>{["Opened","Closed","Ticker","Entry","Close","P&L","Status","R:R"].map(h=><th key={h} style={TH}>{h}</th>)}</tr></thead>
    <tbody>{data.map((t,i)=><tr key={i} onMouseEnter={e=>hov(e,true)} onMouseLeave={e=>hov(e,false)}>
      <td style={TD}>{t.opened_at?t.opened_at.slice(0,10):"—"}</td><td style={TD}>{t.closed_at?t.closed_at.slice(0,10):"—"}</td>
      <td style={{...TD,color:C.blue,fontWeight:"bold"}}>{t.ticker||"—"}</td>
      <td style={TD}>${fmt(t.entry_price)}</td><td style={TD}>${fmt(t.close_price)}</td>
      <td style={{...TD,color:pnlColor(t.pnl_usd)}}>{t.pnl_usd?sign(t.pnl_usd)+`$${fmt(t.pnl_usd)}`:"—"}</td>
      <td style={TD}><Badge status={t.status}/></td><td style={TD}>{fmt(t.rr_ratio)}</td>
    </tr>)}</tbody></table>;
}

function SignalsTab({data}){
  if(!data.length)return<div style={{color:C.muted,padding:20,textAlign:"center",fontSize:12}}>No signals</div>;
  return<table style={{width:"100%",borderCollapse:"collapse"}}>
    <thead><tr>{["Time","Ticker","Score","Traded","Signal","Skip Reason"].map(h=><th key={h} style={TH}>{h}</th>)}</tr></thead>
    <tbody>{data.map((s,i)=>{const sc=parseFloat(s.score),traded=s.traded==="True"||s.traded===true;return<tr key={i} onMouseEnter={e=>hov(e,true)} onMouseLeave={e=>hov(e,false)}>
      <td style={{...TD,whiteSpace:"nowrap"}}>{s.timestamp?s.timestamp.slice(0,16).replace("T"," "):"—"}</td>
      <td style={{...TD,color:C.blue,fontWeight:"bold",whiteSpace:"nowrap"}}>{s.ticker||"—"}</td>
      <td style={{...TD,color:sc>=0.7?C.green:sc>=0.4?C.yellow:C.red,whiteSpace:"nowrap"}}>{fmt(sc,2)}</td>
      <td style={{...TD,color:traded?C.green:C.muted,whiteSpace:"nowrap"}}>{traded?"✓":"—"}</td>
      <td style={{...TD,color:C.text,fontSize:11,maxWidth:320}}>{s.reason||"—"}</td>
      <td style={{...TD,color:C.muted,fontSize:11,whiteSpace:"nowrap"}}>{s.skip_reason||"—"}</td>
    </tr>;})}</tbody></table>;
}

// ── Claude chat ────────────────────────────────────────────────────────────────
function ChatPanel(){
  const [messages,setMessages]=useState([]);
  const [input,setInput]=useState("");
  const [loading,setLoading]=useState(false);
  const bottomRef=useRef(null);
  const inputRef=useRef(null);

  useEffect(()=>{
    fetch("/api/investments/chat").then(r=>r.json()).then(h=>
      setMessages(h.map(m=>({role:m.role,content:m.content,ts:m.timestamp?.slice(0,16).replace("T"," ")})))
    ).catch(()=>{});
  },[]);

  useEffect(()=>{ bottomRef.current?.scrollIntoView({behavior:"smooth"}); },[messages,loading]);

  const send=async()=>{
    const msg=input.trim(); if(!msg||loading)return;
    setInput("");
    setMessages(p=>[...p,{role:"user",content:msg}]);
    setLoading(true);
    try{
      const r=await fetch("/api/investments/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:msg})});
      const d=await r.json();
      setMessages(p=>[...p,{role:"assistant",content:d.response}]);
    }catch{ setMessages(p=>[...p,{role:"assistant",content:"Error — try again."}]); }
    finally{ setLoading(false); setTimeout(()=>inputRef.current?.focus(),50); }
  };

  return<div style={{display:"flex",flexDirection:"column",height:"100%",background:C.bg,borderLeft:`1px solid ${C.border}`}}>
    <div style={{padding:"9px 12px",borderBottom:`1px solid ${C.border}`,color:C.blue,fontSize:11,letterSpacing:2,textTransform:"uppercase",flexShrink:0}}>// Talk to HORDE</div>
    <div style={{flex:1,overflowY:"auto",padding:"10px 10px",display:"flex",flexDirection:"column",gap:8}}>
      {!messages.length&&<div style={{color:C.muted,fontSize:11,textAlign:"center",marginTop:24}}>Ask HORDE about your portfolio</div>}
      {messages.map((m,i)=><div key={i} style={{display:"flex",flexDirection:"column",alignItems:m.role==="user"?"flex-end":"flex-start"}}>
        <div style={{fontSize:9,color:C.muted,marginBottom:2,textTransform:"uppercase",letterSpacing:1}}>{m.role==="user"?"You":"HORDE"}{m.ts?` · ${m.ts}`:""}</div>
        <div style={{maxWidth:"90%",padding:"6px 9px",fontSize:12,lineHeight:1.5,background:m.role==="user"?C.bg3:C.bg2,border:`1px solid ${C.border}`,whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{m.content}</div>
      </div>)}
      {loading&&<div style={{display:"flex",flexDirection:"column",alignItems:"flex-start"}}>
        <div style={{fontSize:9,color:C.muted,marginBottom:2,textTransform:"uppercase",letterSpacing:1}}>HORDE</div>
        <div style={{background:C.bg2,border:`1px solid ${C.border}`,padding:"6px 9px",color:C.muted,fontSize:12}}>thinking...</div>
      </div>}
      <div ref={bottomRef}/>
    </div>
    <div style={{padding:"7px 8px",borderTop:`1px solid ${C.border}`,display:"flex",gap:5,flexShrink:0}}>
      <input ref={inputRef} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>e.key==="Enter"&&!e.shiftKey&&send()}
        placeholder="Ask HORDE about your portfolio..." style={{flex:1,background:C.bg3,border:`1px solid ${C.border}`,color:C.text,padding:"5px 9px",fontFamily:"inherit",fontSize:12,outline:"none"}}/>
      <button onClick={send} disabled={loading||!input.trim()} style={{background:C.blue,color:C.bg,border:"none",padding:"5px 11px",cursor:loading?"wait":"pointer",fontFamily:"inherit",fontSize:12,fontWeight:"bold",opacity:loading||!input.trim()?0.5:1}}>→</button>
    </div>
  </div>;
}

// \u2500\u2500 Login \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
const GS={
  overlay:{position:"fixed",inset:0,background:"#0d1117",display:"flex",alignItems:"center",justifyContent:"center",zIndex:9999},
  box:{background:"#161b22",border:"1px solid #30363d",padding:"32px",width:300,fontFamily:"'Fira Code',monospace"},
  logo:{width:48,height:48,display:"block",margin:"0 auto 16px"},
  title:{color:"#58a6ff",fontSize:13,letterSpacing:2,marginBottom:20,fontFamily:"inherit",fontWeight:700},
  input:{width:"100%",background:"#21262d",border:"1px solid #30363d",color:"#c9d1d9",padding:"8px 12px",fontFamily:"inherit",fontSize:13,marginBottom:10,outline:"none",display:"block"},
  inputErr:{width:"100%",background:"#21262d",border:"1px solid #f85149",color:"#c9d1d9",padding:"8px 12px",fontFamily:"inherit",fontSize:13,marginBottom:10,outline:"none",display:"block"},
  btn:{width:"100%",background:"#58a6ff",color:"#0d1117",border:"none",padding:"8px",cursor:"pointer",fontFamily:"inherit",fontSize:13,fontWeight:"bold"},
  err:{color:"#f85149",fontSize:12,marginTop:8,minHeight:16,fontFamily:"inherit"},
  locked:{color:"#f85149",fontSize:12,marginTop:8,lineHeight:1.6},
};
const MAX_UI_ATTEMPTS=3;
function Login({onAuth}){
  const [pw,setPw]=useState("");
  const [attempts,setAttempts]=useState(0);
  const [err,setErr]=useState("");
  const [locked,setLocked]=useState(false);
  const [shake,setShake]=useState(false);
  const submit=async()=>{
    if(locked||attempts>=MAX_UI_ATTEMPTS)return;
    try{
      const r=await fetch("/api/investments/auth",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:pw})});
      const d=await r.json();
      if(d.locked){setLocked(true);return;}
      if(d.ok){sessionStorage.setItem("inv_auth","1");onAuth();return;}
      const next=attempts+1;
      setAttempts(next);
      setErr(next>=MAX_UI_ATTEMPTS?"too many attempts.":`incorrect. ${MAX_UI_ATTEMPTS-next} attempt${MAX_UI_ATTEMPTS-next!==1?"s":""} remaining.`);
      setShake(true);setPw("");
      setTimeout(()=>setShake(false),400);
    }catch{setErr("cannot reach server.");}
  };
  const isBlocked=locked||attempts>=MAX_UI_ATTEMPTS;
  return<div style={GS.overlay}>
    <div style={GS.box}>
      <img src="/logo192.png" alt="" style={GS.logo}/>
      <div style={GS.title}>// INVESTMENTS</div>
      {isBlocked?<div style={GS.locked}>{locked?"system locked — ssh to restore access.":"too many attempts — try again later."}</div>:(
        <>
          <input style={shake?GS.inputErr:GS.input} type="password" placeholder="password" value={pw} autoFocus onChange={e=>{setPw(e.target.value);setErr("");}} onKeyDown={e=>e.key==="Enter"&&submit()}/>
          <button style={GS.btn} onClick={submit}>ENTER</button>
          <div style={GS.err}>{err}</div>
        </>
      )}
    </div>
  </div>;
}
// ── Dashboard ──────────────────────────────────────────────────────────────────
function Dashboard(){
  const [tab,setTab]=useState("positions");
  const [stats,setStats]=useState(null);
  const [positions,setPositions]=useState([]);
  const [trades,setTrades]=useState([]);
  const [signals,setSignals]=useState([]);
  const [updated,setUpdated]=useState("");

  const load=useCallback(async()=>{
    const [st,po,tr,si]=await Promise.all([
      fetch("/api/investments/stats").then(r=>r.json()),
      fetch("/api/investments/positions").then(r=>r.json()),
      fetch("/api/investments/trades").then(r=>r.json()),
      fetch("/api/investments/signals").then(r=>r.json()),
    ]);
    setStats(st);setPositions(po);setTrades(tr);setSignals(si);
    setUpdated(new Date().toLocaleTimeString());
  },[]);

  useEffect(()=>{load();const id=setInterval(load,60000);return()=>clearInterval(id);},[load]);

  const pnl=stats?.total_pnl||0, wr=stats?.win_rate||0;

  return<div style={{display:"flex",flexDirection:"column",height:"100vh",overflow:"hidden"}}>
    <div style={{background:C.bg2,borderBottom:`1px solid ${C.border}`,padding:"9px 14px",display:"flex",alignItems:"center",gap:12,flexShrink:0}}>
      <span style={{fontSize:14,color:C.blue,letterSpacing:2,textTransform:"uppercase"}}>// Investments</span>
      <span style={{color:C.muted,fontSize:11}}>updated {updated}</span>
      <button onClick={load} style={{marginLeft:"auto",background:C.bg3,border:`1px solid ${C.border}`,color:C.muted,padding:"3px 10px",cursor:"pointer",fontFamily:"inherit",fontSize:11}}>↺ refresh</button>
      <button onClick={async()=>{const r=await fetch("/api/investments/sync",{method:"POST"});const d=await r.json();load();alert(d.synced?`Synced ${d.synced} closed trade(s): ${d.tickers.join(", ")}`:"No new closes found.");}} style={{background:C.bg3,border:`1px solid ${C.border}`,color:C.muted,padding:"3px 10px",cursor:"pointer",fontFamily:"inherit",fontSize:11}}>⟳ sync alpaca</button>
    </div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:7,padding:"8px 12px",flexShrink:0}}>
      {[{l:"Portfolio Value",v:stats?.portfolio_value!=null?`$${fmt(stats.portfolio_value)}`:"—",c:C.blue},
        {l:"Cash",v:stats?.cash!=null?`$${fmt(stats.cash)}`:"—",c:C.muted},
        {l:"Win Rate",v:`${wr}%`,c:wr>=50?C.green:C.red},
        {l:"Total P&L",v:`${sign(pnl)}$${fmt(pnl)}`,c:pnlColor(pnl)},
        {l:"Positions",v:stats?.open_positions??0,c:C.blue}]
        .map(s=><div key={s.l} style={{background:C.bg2,border:`1px solid ${C.border}`,padding:"9px 11px"}}>
          <div style={{color:C.muted,fontSize:10,textTransform:"uppercase",letterSpacing:1,marginBottom:3}}>{s.l}</div>
          <div style={{fontSize:19,fontWeight:"bold",color:s.c}}>{s.v}</div>
        </div>)}
    </div>
    <div style={{flex:1,display:"flex",overflow:"hidden"}}>
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
        <PortfolioChart trades={trades}/>
        <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 12px",display:"flex",flexShrink:0}}>
          {["positions","trades","signals"].map(t=><div key={t} onClick={()=>setTab(t)} style={{padding:"7px 13px",cursor:"pointer",color:tab===t?C.blue:C.muted,fontSize:11,textTransform:"uppercase",letterSpacing:1,borderBottom:tab===t?`2px solid ${C.blue}`:"2px solid transparent",marginBottom:-1}}>{t}</div>)}
        </div>
        <div style={{flex:1,overflowX:"auto",overflowY:"auto",padding:"0 12px 12px"}}>
          {tab==="positions"&&<PositionsTab data={positions}/>}
          {tab==="trades"&&<TradesTab data={trades}/>}
          {tab==="signals"&&<SignalsTab data={signals}/>}
        </div>
      </div>
      <div style={{width:"26%",minWidth:230,maxWidth:320,flexShrink:0}}><ChatPanel/></div>
    </div>
  </div>;
}

function App(){
  const [authed,setAuthed]=useState(!!sessionStorage.getItem("inv_auth"));
  if(!authed)return<Login onAuth={()=>setAuthed(true)}/>;
  return<Dashboard/>;
}
ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
</script>
</body>
</html>"""

from fastapi.responses import FileResponse
STATIC_DIR = Path(__file__).parent

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return FileResponse(STATIC_DIR / "favicon.ico")

@app.get("/logo192.png", include_in_schema=False)
def logo192(): return FileResponse(STATIC_DIR / "logo192.png")

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(INVESTMENTS_HTML)

# ── Auth ───────────────────────────────────────────────────────────────────────
class AuthRequest(BaseModel):
    password: str

@app.post("/api/investments/auth")
def inv_auth(req: AuthRequest):
    return {"ok": hashlib.sha256(req.password.encode()).hexdigest() == PW_HASH}

# ── Positions ──────────────────────────────────────────────────────────────────
@app.get("/api/investments/positions")
def inv_positions():
    """Alpaca is the source of truth — only show what's actually held there."""
    try:
        sys.path.insert(0, str(TRADER_DIR))
        from broker import get_latest_price, load_env, _trading_client
        load_env()

        # Load local state for enrichment (entry, stop, target, reason etc.)
        local = {}
        path = TRADER_DIR / "positions.json"
        if path.exists():
            for ticker, p in json.loads(path.read_text()).items():
                # Normalise symbol: BTC/USD -> BTCUSD for matching
                local[ticker.replace("/", "")] = dict(p, ticker=ticker)

        client = _trading_client(paper=False)
        alpaca_positions = client.get_all_positions()

        enriched = []
        for ap in alpaca_positions:
            sym = ap.symbol  # e.g. AAPL, BTCUSD
            # Find matching local record
            loc = local.get(sym, {})
            entry = float(ap.avg_entry_price)
            current = float(ap.current_price) if ap.current_price else None
            shares = float(ap.qty)

            # Try live price for more accurate P&L
            try:
                ticker = loc.get("ticker", sym)
                current = get_latest_price(ticker, paper=False)
            except Exception:
                pass

            live_pnl = round((current - entry) * shares, 4) if current else None
            live_pct = round(((current - entry) / entry) * 100, 2) if current and entry else None

            enriched.append({
                "ticker": loc.get("ticker", sym),
                "entry_price": entry,
                "current_price": current,
                "live_pnl_usd": live_pnl,
                "live_pnl_pct": live_pct,
                "shares": shares,
                "invested_usd": loc.get("invested_usd", round(entry * shares, 4)),
                "stop_loss": loc.get("stop_loss"),
                "take_profit": loc.get("take_profit"),
                "sentiment_score": loc.get("sentiment_score"),
                "reason": loc.get("reason"),
                "opened_at": loc.get("opened_at"),
                "status": "open",
            })
        return enriched
    except Exception as e:
        return []

# ── Trades ─────────────────────────────────────────────────────────────────────
@app.get("/api/investments/trades")
def inv_trades():
    path = TRADER_DIR / "trades.csv"
    if not path.exists():
        return []
    with open(path) as f:
        return list(reversed(list(csv.DictReader(f))))

# ── Signals ────────────────────────────────────────────────────────────────────
@app.get("/api/investments/signals")
def inv_signals():
    path = TRADER_DIR / "signals.csv"
    if not path.exists():
        return []
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return list(reversed(rows[-100:]))

# ── Stats ──────────────────────────────────────────────────────────────────────
@app.get("/api/investments/stats")
def inv_stats():
    stats = {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
        "total_pnl": 0.0, "open_positions": 0, "invested_usd": 0.0,
        "portfolio_value": None, "cash": None, "buying_power": None,
    }
    trades_path = TRADER_DIR / "trades.csv"
    positions_path = TRADER_DIR / "positions.json"

    if trades_path.exists():
        with open(trades_path) as f:
            for row in csv.DictReader(f):
                if row.get("status") in ("closed_profit", "closed_loss", "closed_expired", "closed_manual"):
                    pnl = float(row.get("pnl_usd") or 0)
                    if pnl != 0:
                        stats["total_trades"] += 1
                        stats["total_pnl"] += pnl
                        if pnl >= 0: stats["wins"] += 1
                        else: stats["losses"] += 1
        if stats["total_trades"]:
            stats["win_rate"] = round(stats["wins"] / stats["total_trades"] * 100, 1)
        stats["total_pnl"] = round(stats["total_pnl"], 4)

    if positions_path.exists():
        positions = json.loads(positions_path.read_text())
        open_pos = {t: p for t, p in positions.items() if p.get("status") == "open"}
        stats["open_positions"] = len(open_pos)
        stats["invested_usd"] = round(sum(p.get("invested_usd", 0) for p in open_pos.values()), 4)

    # Live account data from Alpaca
    try:
        sys.path.insert(0, str(TRADER_DIR))
        from broker import get_account, load_env
        load_env()
        acct = get_account(paper=False)
        stats["portfolio_value"] = round(acct["portfolio_value"], 2)
        stats["cash"] = round(acct["cash"], 2)
        stats["buying_power"] = round(acct["buying_power"], 2)
    except Exception:
        pass

    return stats

# ── Chat ───────────────────────────────────────────────────────────────────────
def _append_chat(role: str, content: str):
    write_header = not CHAT_CSV.exists()
    with open(CHAT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CHAT_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow({"timestamp": datetime.now().isoformat(), "role": role, "content": content})

@app.get("/api/investments/chat")
def get_chat():
    if not CHAT_CSV.exists():
        return []
    with open(CHAT_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

class ChatMsg(BaseModel):
    message: str

@app.post("/api/investments/chat")
async def post_chat(req: ChatMsg):
    stats = inv_stats()
    _append_chat("user", req.message)
    prompt = (
        f"You are a concise trading assistant called HORDE. "
        f"Portfolio: win_rate={stats['win_rate']}%, total_pnl=${stats['total_pnl']}, "
        f"open_positions={stats['open_positions']}, invested=${stats['invested_usd']}. "
        f"Be brief and insightful. No markdown headers.\n\nUser: {req.message}"
    )
    # Merge OAuth env so Claude CLI can authenticate
    oauth_env = {}
    oauth_path = Path.home() / ".claude_oauth_env"
    if oauth_path.exists():
        for line in oauth_path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                oauth_env[k.strip()] = v.strip()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env.update(oauth_env)
    response = "Sorry, I couldn't respond right now — try again."
    try:
        proc = await asyncio.create_subprocess_exec(
            str(CLAUDE_BIN), "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home() / "bot"), env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        raw = stdout.decode().strip()
        if raw:
            data = json.loads(raw)
            response = data.get("result") or response
        else:
            err = stderr.decode().strip()
            print(f"[chat] Claude returned empty stdout. stderr: {err[:300]}")
    except asyncio.TimeoutError:
        response = "Timed out waiting for HORDE — try again."
    except Exception as e:
        print(f"[chat] error: {e}")
    _append_chat("assistant", response)
    return {"response": response}

# ── Alpaca manual-close sync ──────────────────────────────────────────────────
TRADES_FIELDS = [
    "opened_at","closed_at","ticker","is_etf","entry_price","close_price",
    "shares","invested_usd","stop_loss","take_profit","rr_ratio",
    "status","pnl_usd","sentiment_score","reason",
]

@app.post("/api/investments/sync")
def sync_alpaca_closes():
    """Detect positions closed manually in Alpaca and record them in trades.csv."""
    sys.path.insert(0, str(TRADER_DIR))
    from broker import _trading_client, load_env
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus, OrderSide as AS
    load_env()

    client = _trading_client(paper=False)
    positions_path = TRADER_DIR / "positions.json"
    trades_path    = TRADER_DIR / "trades.csv"

    if not positions_path.exists():
        return {"synced": 0, "tickers": []}

    positions = json.loads(positions_path.read_text())

    # Symbols currently held in Alpaca
    try:
        held = {p.symbol for p in client.get_all_positions()}
    except Exception as e:
        return {"error": str(e)}

    # Already-recorded tickers (avoid dupes)
    recorded = set()
    if trades_path.exists():
        with open(trades_path) as f:
            for row in csv.DictReader(f):
                if row.get("status", "").startswith("closed"):
                    recorded.add(f"{row['ticker']}:{row.get('opened_at','')}")

    synced, to_remove = 0, []

    for ticker, pos in positions.items():
        alpaca_sym = ticker.replace("/", "")
        if alpaca_sym in held:
            continue  # still open in Alpaca

        dedup_key = f"{ticker}:{pos.get('opened_at','')}"
        if dedup_key in recorded:
            to_remove.append(ticker)
            continue

        # Find the most recent filled sell order for this symbol
        close_price, closed_at = None, None
        try:
            orders = client.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                symbols=[alpaca_sym],
                limit=10,
            ))
            for order in sorted(orders, key=lambda o: o.filled_at or o.updated_at or datetime.min, reverse=True):
                if order.side == AS.SELL and order.filled_avg_price:
                    close_price = float(order.filled_avg_price)
                    closed_at   = str(order.filled_at or order.updated_at)[:19].replace("T", " ")
                    break
        except Exception as e:
            print(f"[sync] order fetch failed for {ticker}: {e}")

        if close_price is None:
            close_price = pos.get("entry_price", 0)  # worst case — no P&L distortion

        entry  = float(pos.get("entry_price") or 0)
        shares = float(pos.get("shares") or 0)
        pnl    = round((close_price - entry) * shares, 4)

        trade = {
            "opened_at":       pos.get("opened_at", ""),
            "closed_at":       closed_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ticker":          ticker,
            "is_etf":          pos.get("is_etf", False),
            "entry_price":     entry,
            "close_price":     close_price,
            "shares":          shares,
            "invested_usd":    pos.get("invested_usd", round(entry * shares, 4)),
            "stop_loss":       pos.get("stop_loss", ""),
            "take_profit":     pos.get("take_profit", ""),
            "rr_ratio":        "",
            "status":          "closed_manual",
            "pnl_usd":         pnl,
            "sentiment_score": pos.get("sentiment_score", ""),
            "reason":          pos.get("reason", ""),
        }

        write_header = not trades_path.exists()
        with open(trades_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADES_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(trade)

        to_remove.append(ticker)
        synced += 1

    # Remove synced positions from positions.json
    for ticker in to_remove:
        positions.pop(ticker, None)
    if to_remove:
        positions_path.write_text(json.dumps(positions, indent=2))

    return {"synced": synced, "tickers": to_remove}

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "time": time.time()}
