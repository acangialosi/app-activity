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
h1{margin:0;font-size:30px}#label,.muted{color:var(--muted)}nav{display:flex;gap:8px;margin:28px 0}
button{border:0;border-radius:9px;padding:9px 15px;background:#202b3d;color:var(--text);cursor:pointer}
button.active{background:var(--accent);color:#081426}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:var(--card);border:1px solid #253147;border-radius:14px;padding:18px}.metric strong{display:block;font-size:30px;margin-top:5px}
.grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px;margin-top:14px}h2{font-size:17px;margin:0 0 18px}
.timeline{height:230px;display:flex;align-items:end;gap:4px;border-bottom:1px solid #344158;padding-top:15px}
.bar-wrap{height:100%;flex:1;display:flex;flex-direction:column;justify-content:end;align-items:center;min-width:0}
.bar{width:100%;min-height:1px;background:linear-gradient(#8bb7ff,#518af0);border-radius:4px 4px 0 0}
.bar-label{font-size:10px;color:var(--muted);height:22px;padding-top:5px;white-space:nowrap}
.app{margin:14px 0}.app-head{display:flex;justify-content:space-between;gap:12px}.track{height:7px;background:#273249;border-radius:9px;margin-top:7px;overflow:hidden}
.fill{height:100%;background:var(--accent);border-radius:9px}.empty{color:var(--muted);padding:30px 0;text-align:center}
@media(max-width:750px){header{align-items:start;flex-direction:column}.metrics,.grid{grid-template-columns:1fr}.timeline{height:180px}}
</style>
</head>
<body><main>
<header><div><h1>App Activity</h1><div id="label">Loading...</div></div><div class="muted">Local-only tracking</div></header>
<nav id="periods"></nav>
<section class="metrics">
  <div class="card metric"><span class="muted">Active</span><strong id="active">-</strong></div>
  <div class="card metric"><span class="muted">Idle</span><strong id="idle">-</strong></div>
  <div class="card metric"><span class="muted">Tracked</span><strong id="tracked">-</strong></div>
</section>
<section class="grid">
  <div class="card"><h2>Active time</h2><div class="timeline" id="timeline"></div></div>
  <div class="card"><h2>Applications</h2><div id="apps"></div></div>
</section>
</main>
<script>
const periods=["day","week","month","year"];let selected="day";
const fmt=s=>{const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h}h ${m}m`:`${m}m`};
function nav(){periods.forEach(p=>{const b=document.createElement("button");b.textContent=p[0].toUpperCase()+p.slice(1);b.className=p===selected?"active":"";b.onclick=()=>{selected=p;document.querySelectorAll("nav button").forEach(x=>x.className="");b.className="active";load()};periodsEl.appendChild(b)})}
async function load(){const r=await fetch(`/api/report?period=${selected}`),d=await r.json();label.textContent=d.label;active.textContent=fmt(d.active_seconds);idle.textContent=fmt(d.idle_seconds);tracked.textContent=fmt(d.tracked_seconds);
 const max=Math.max(...d.timeline.map(x=>x.seconds),1);timeline.innerHTML=d.timeline.map((x,i)=>`<div class="bar-wrap" title="${x.label}: ${fmt(x.seconds)}"><div class="bar" style="height:${Math.max(1,x.seconds/max*100)}%"></div><div class="bar-label">${selected==="month"&&i%3?"":x.label}</div></div>`).join("");
 apps.innerHTML=d.apps.length?d.apps.slice(0,15).map(x=>`<div class="app"><div class="app-head"><span>${escapeHtml(x.name)}</span><span>${fmt(x.seconds)} · ${x.percent}%</span></div><div class="track"><div class="fill" style="width:${x.percent}%"></div></div></div>`).join(""):`<div class="empty">No activity recorded yet.</div>`}
function escapeHtml(v){const e=document.createElement("div");e.textContent=v;return e.innerHTML}
const periodsEl=document.getElementById("periods");nav();load();setInterval(load,60000);
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
                    report = database.report(period)
                except ValueError as error:
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
