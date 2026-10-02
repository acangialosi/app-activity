from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .database import ActivityDatabase


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>App Activity</title>
<style>
:root{color-scheme:dark;--bg:#0c111b;--card:#151d2b;--muted:#91a0b8;--text:#f5f7fb;--accent:#72a7ff;--idle:#59677d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:auto;padding:32px 20px 60px}header{display:flex;justify-content:space-between;align-items:end;gap:20px}
h1{margin:0;font-size:30px}#label,.muted{color:var(--muted)}.report-nav{display:flex;align-items:center;gap:8px;margin-top:5px;flex-wrap:wrap}.report-nav button{padding:3px 8px;font-size:16px}.report-nav .current-period{font-size:12px;padding:4px 9px}.report-nav button:disabled{cursor:default;opacity:.35}.report-nav #label{min-width:190px;text-align:center}nav{display:flex;gap:8px;margin:28px 0}
button{border:0;border-radius:9px;padding:9px 15px;background:#202b3d;color:var(--text);cursor:pointer}
button.active{background:var(--accent);color:#081426}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:var(--card);border:1px solid #253147;border-radius:14px;padding:18px}.metric strong{display:block;font-size:30px;margin-top:5px}
.grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:14px;margin-top:14px}.grid>*{min-width:0}h2{font-size:17px;margin:0 0 18px}
.timeline{height:230px;display:flex;align-items:end;gap:4px;border-bottom:1px solid #344158;padding-top:15px}.chart-card{display:flex;flex-direction:column}.chart-card .timeline{height:auto;min-height:260px;flex:1}
.bar-wrap{height:100%;flex:1;display:flex;flex-direction:column;justify-content:end;align-items:center;min-width:0}
.bar-stack{width:100%;min-height:1px;display:flex;flex-direction:column-reverse;overflow:hidden;border-radius:4px 4px 0 0}.bar-segment{width:100%;min-height:1px}
.bar-label{font-size:10px;color:var(--muted);height:22px;padding-top:5px;white-space:nowrap}
.color-dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:none}
.chart-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.chart-heading h2{margin:0}.chart-toggle{display:flex;background:#202b3d;border-radius:8px;padding:2px}.chart-toggle button{padding:5px 9px;border-radius:6px;background:transparent;color:var(--muted);font-size:12px}.chart-toggle button.active{background:var(--accent);color:#081426}
.app{margin:14px 0;padding-left:18px;min-width:0;max-width:100%}.app>summary{cursor:pointer;list-style:none;min-width:0;position:relative}.app>summary::-webkit-details-marker{display:none}.app>summary::before{content:"›";position:absolute;left:-17px;top:-2px;color:var(--accent);font-size:21px;line-height:1;transition:transform .15s ease}.app[open]>summary::before{transform:rotate(90deg)}.app-head{display:flex;justify-content:space-between;gap:12px;min-width:0}.app-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.app-time{flex:none}.window-count{display:inline-block;color:var(--muted);font-size:10px;line-height:1;margin-left:6px;padding:2px 5px;border:1px solid #344158;border-radius:8px;vertical-align:1px}.track{height:7px;background:#273249;border-radius:9px;margin-top:7px;overflow:hidden}
.windows{border-left:2px solid #344158;margin:10px 0 0 7px;padding-left:12px;min-width:0;max-width:100%}.window{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;color:var(--muted);font-size:13px;margin:8px 0;min-width:0;max-width:100%}.window-detail{flex:1;min-width:0;max-width:100%}.window-detail summary{cursor:pointer;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;list-style:none}.window-detail summary::-webkit-details-marker{display:none}.window-full{color:var(--text);margin-top:6px;overflow-wrap:anywhere;white-space:normal}.window-time{flex:none;white-space:nowrap}
.fill{height:100%;background:var(--accent);border-radius:9px}.empty{color:var(--muted);padding:30px 0;text-align:center}
@media(max-width:750px){header{align-items:start;flex-direction:column}.metrics,.grid{grid-template-columns:1fr}.timeline{height:180px}}
</style>
</head>
<body><main>
<header><div><h1>App Activity</h1><div class="report-nav"><button id="previousPeriod" type="button" title="Previous period" aria-label="Previous period">←</button><div id="label">Loading...</div><button id="nextPeriod" type="button" title="Next period" aria-label="Next period">→</button><button id="currentPeriod" class="current-period" type="button" title="Go to current period">Now</button></div></div><div class="muted">Local-only tracking</div></header>
<nav id="periods"></nav>
<section class="metrics">
  <div class="card metric"><span class="muted">Active</span><strong id="active">-</strong></div>
  <div class="card metric"><span class="muted">Idle</span><strong id="idle">-</strong></div>
  <div class="card metric"><span class="muted">Tracked</span><strong id="tracked">-</strong></div>
</section>
<section class="grid">
  <div class="card chart-card"><div class="chart-heading"><h2>Active time by application</h2><div class="chart-toggle"><button id="timeMode" class="active" type="button">Total time</button><button id="percentMode" type="button">Percentage</button></div></div><div class="timeline" id="timeline"></div></div>
  <div class="card"><h2>Applications</h2><div id="apps"></div></div>
</section>
</main>
<script>
const periods=["hour","day","week","month","year"];let selected="day",periodOffset=0,chartMode="time",currentData=null,colors=new Map();
const palette=["#72a7ff","#5ed6a7","#f6c85f","#ef7b7b","#bd8cff","#5bc0eb","#ff9f68","#8bd450","#e66fbb","#9ca7ff","#4fd1c5","#d4a373"];
const fmt=s=>{const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h}h ${m}m`:m?`${m}m`:`${Math.round(s)}s`};
function nav(){periods.forEach(p=>{const b=document.createElement("button");b.textContent=p[0].toUpperCase()+p.slice(1);b.className=p===selected?"active":"";b.onclick=()=>{selected=p;periodOffset=0;document.querySelectorAll("nav button").forEach(x=>x.className="");b.className="active";load()};periodsEl.appendChild(b)})}
function setChartMode(mode){chartMode=mode;timeMode.className=mode==="time"?"active":"";percentMode.className=mode==="percent"?"active":"";if(currentData)renderChart(currentData)}
function renderChart(d){const max=Math.max(...d.timeline.map(x=>x.seconds),1);timeline.innerHTML=d.timeline.map((x,i)=>{const segments=x.apps.map(a=>{const share=a.seconds/Math.max(x.seconds,1)*100;const tip=chartMode==="percent"?`${a.name}: ${share.toFixed(1)}%`:`${a.name}: ${fmt(a.seconds)}`;return `<div class="bar-segment" title="${escapeHtml(tip)}" style="height:${share}%;background:${colors.get(a.name)||palette[0]}"></div>`}).join("");const height=chartMode==="percent"?(x.seconds?100:1):Math.max(1,x.seconds/max*100);const totalTip=chartMode==="percent"?`${x.label}: application share`:`${x.label}: ${fmt(x.seconds)}`;return `<div class="bar-wrap" title="${escapeHtml(totalTip)}"><div class="bar-stack" style="height:${height}%">${segments}</div><div class="bar-label">${selected==="month"&&i%3?"":x.label}</div></div>`}).join("")}
async function load(){const r=await fetch(`/api/report?period=${selected}&offset=${periodOffset}`),d=await r.json();label.textContent=d.label;nextPeriod.disabled=periodOffset>=0;currentPeriod.disabled=periodOffset===0;active.textContent=fmt(d.active_seconds);idle.textContent=fmt(d.idle_seconds);tracked.textContent=fmt(d.tracked_seconds);
 currentData=d;colors=new Map(d.apps.map((x,i)=>[x.name,palette[i%palette.length]]));renderChart(d);
 apps.innerHTML=d.apps.length?d.apps.slice(0,15).map(x=>{const count=x.windows.length?`<span class="window-count">${x.windows.length} window title${x.windows.length===1?"":"s"}</span>`:"";const head=`<div class="app-head"><span class="app-name"><span class="color-dot" style="background:${colors.get(x.name)}"></span> ${escapeHtml(x.name)}${count}</span><span class="app-time">${fmt(x.seconds)} · ${x.percent}%</span></div><div class="track"><div class="fill" style="width:${x.percent}%;background:${colors.get(x.name)}"></div></div>`;if(!x.windows.length)return `<div class="app">${head}</div>`;const windows=x.windows.slice(0,20).map(w=>{const title=escapeHtml(w.title);return `<div class="window"><details class="window-detail"><summary title="${title}">${title}</summary><div class="window-full">${title}</div></details><span class="window-time">${fmt(w.seconds)} · ${w.percent}%</span></div>`}).join("");return `<details class="app"><summary>${head}</summary><div class="windows">${windows}</div></details>`}).join(""):`<div class="empty">No activity recorded yet.</div>`}
function escapeHtml(v){const e=document.createElement("div");e.textContent=v;return e.innerHTML}
const periodsEl=document.getElementById("periods");previousPeriod.onclick=()=>{periodOffset--;load()};nextPeriod.onclick=()=>{if(periodOffset<0){periodOffset++;load()}};currentPeriod.onclick=()=>{if(periodOffset!==0){periodOffset=0;load()}};timeMode.onclick=()=>setChartMode("time");percentMode.onclick=()=>setChartMode("percent");nav();load();setInterval(load,60000);
</script></body></html>"""


def create_server(database: ActivityDatabase, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(200, "text/html; charset=utf-8", HTML.encode())
                return
            if parsed.path == "/api/report":
                period = parse_qs(parsed.query).get("period", ["day"])[0]
                try:
                    offset = int(
                        parse_qs(parsed.query).get("offset", ["0"])[0]
                    )
                    report = database.report(period, offset=offset)
                except (TypeError, ValueError) as error:
                    self._json(400, {"error": str(error)})
                else:
                    self._json(200, report)
                return
            if parsed.path == "/api/status":
                self._json(200, {"status": "running"})
                return
            self._json(404, {"error": "Not found"})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: object) -> None:
            self._send(
                status,
                "application/json; charset=utf-8",
                json.dumps(payload).encode(),
            )

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
