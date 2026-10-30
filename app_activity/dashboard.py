from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .database import ActivityDatabase
from .updates import UpdateChecker


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>App Activity</title>
<script>
try{const theme=localStorage.getItem("appActivityTheme");if(theme==="light"||theme==="dark")document.documentElement.dataset.theme=theme}catch{}
</script>
<style>
:root,:root[data-theme="light"]{color-scheme:light;--bg:#f4f7fb;--card:#fff;--muted:#5c6b80;--text:#172033;--accent:#2563eb;--accent-text:#fff;--button:#e6edf7;--border:#d7e0ec;--grid:#dfe6f0;--track:#e2e8f0}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0c111b;--card:#151d2b;--muted:#91a0b8;--text:#f5f7fb;--accent:#72a7ff;--accent-text:#081426;--button:#202b3d;--border:#344158;--grid:#2b374b;--track:#273249}
@media(prefers-color-scheme:dark){:root:not([data-theme]){color-scheme:dark;--bg:#0c111b;--card:#151d2b;--muted:#91a0b8;--text:#f5f7fb;--accent:#72a7ff;--accent-text:#081426;--button:#202b3d;--border:#344158;--grid:#2b374b;--track:#273249}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:auto;padding:32px 20px 60px}header{display:flex;justify-content:space-between;align-items:start;gap:20px;margin-bottom:24px}
h1{margin:0;font-size:30px}.header-brand{flex:none}.muted{color:var(--muted)}.tracking-badge{display:inline-block;margin-top:8px;padding:3px 12px;border-radius:999px;background:var(--button);color:var(--muted);font-size:13px}.header-controls{display:grid;width:540px;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"navigation settings" "periods .";align-items:start;gap:16px 8px;min-width:0}.theme-setting{display:flex;align-items:center;justify-content:space-between;gap:12px;color:var(--muted);font-size:13px}.theme-setting select{border:1px solid var(--border);border-radius:8px;padding:6px 28px 6px 9px;background:var(--card);color:var(--text);font:inherit;cursor:pointer}.report-nav{grid-area:navigation;display:flex;align-items:center;gap:8px;min-width:0;max-width:100%}.report-nav button{flex:none;min-width:44px;min-height:44px;border:1px solid var(--border);background:var(--card);font:inherit}.report-nav .current-period{color:var(--muted)}.report-nav button:disabled{cursor:default;opacity:.4}.report-label{display:flex;flex:1;align-items:center;justify-content:center;gap:10px;min-height:44px;min-width:0;max-width:100%;padding:9px 14px;border:1px solid var(--border);border-radius:9px;background:var(--card)}.calendar-icon{width:20px;height:20px;flex:none}#label{text-align:center;overflow-wrap:anywhere}#periods{grid-area:periods;display:flex;min-width:0;max-width:100%;border:1px solid var(--border);border-radius:10px;background:var(--card)}#periods button{flex:1;min-width:0;min-height:44px;border-radius:0;background:transparent;font:inherit;padding:9px 6px}#periods button+button{border-left:1px solid var(--border)}#periods button:first-child{border-radius:9px 0 0 9px}#periods button:last-child{border-radius:0 9px 9px 0}#periods button.active{border-radius:9px;background:var(--accent);color:var(--accent-text)}.header-controls button:focus-visible{position:relative;outline:2px solid var(--accent);outline-offset:3px;z-index:1}
button{border:0;border-radius:9px;padding:9px 15px;background:var(--button);color:var(--text);cursor:pointer}
button.active{background:var(--accent);color:var(--accent-text)}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px}.metric strong{display:block;font-size:30px;margin-top:5px}
.grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:14px;margin-top:14px}.grid>*{min-width:0}h2{font-size:17px;margin:0 0 18px}
.chart-card{display:flex;flex-direction:column}.chart-area{display:flex;min-height:260px;flex:1}.y-axis{width:54px;flex:none;display:flex;flex-direction:column;justify-content:space-between;align-items:flex-end;padding:12px 8px 22px 0;color:var(--muted);font-size:10px;white-space:nowrap}.timeline{position:relative;display:flex;align-items:end;gap:4px;border-bottom:1px solid var(--border);padding-top:15px;flex:1;min-width:0}.timeline::before{content:"";position:absolute;inset:0 0 22px;border-top:1px solid var(--grid);background:linear-gradient(to bottom,var(--grid) 1px,transparent 1px);background-size:100% var(--grid-step,25%);pointer-events:none}
.bar-wrap{position:relative;z-index:1;height:100%;flex:1;display:flex;flex-direction:column;justify-content:end;align-items:center;min-width:0}
.bar-stack{width:100%;min-height:1px;display:flex;flex-direction:column-reverse;overflow:hidden;border-radius:4px 4px 0 0}.bar-segment{width:100%;min-height:1px}
.bar-label{font-size:10px;color:var(--muted);height:22px;padding-top:5px;white-space:nowrap}
.color-dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:none}
.chart-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px;flex-wrap:wrap}.chart-heading h2{margin:0}.chart-toggle{display:flex;background:var(--button);border-radius:8px;padding:2px}.chart-toggle button{padding:5px 9px;border-radius:6px;background:transparent;color:var(--muted);font-size:12px}.chart-toggle button.active{background:var(--accent);color:var(--accent-text)}
.application-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}.application-heading h2{margin:0}.application-heading button{font-size:12px;padding:5px 9px}.application-heading button:disabled{opacity:.5;cursor:default}.application-help{color:var(--muted);font-size:12px;margin:8px 0;overflow-wrap:anywhere}.filter-status{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}.app-select{display:block;width:100%;padding:8px;text-align:left;font:inherit;background:transparent;border:1px solid transparent}.app-select:hover{background:var(--button)}.app-select[aria-pressed="true"]{background:var(--button);border-color:var(--accent)}.app-select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}.app-details{margin:6px 8px}.app-details>summary{cursor:pointer;color:var(--muted);font-size:12px}
.export-controls{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.export-controls label{display:grid;flex:1 1 130px;min-width:0;gap:3px;color:var(--muted);font-size:12px}.export-controls input{width:100%;min-width:0;border:1px solid var(--border);border-radius:7px;padding:6px;background:var(--card);color:var(--text);font:inherit}.export-controls button,.export-controls a{font:inherit}.export-controls a{border-radius:9px;padding:9px 15px;background:var(--button);color:var(--text);text-decoration:none}
.app{margin:14px 0;padding-left:18px;min-width:0;max-width:100%}.app>summary{cursor:pointer;list-style:none;min-width:0;position:relative}.app>summary::-webkit-details-marker{display:none}.app>summary::before{content:"›";position:absolute;left:-17px;top:-2px;color:var(--accent);font-size:21px;line-height:1;transition:transform .15s ease}.app[open]>summary::before{transform:rotate(90deg)}.app-head{display:flex;justify-content:space-between;gap:12px;min-width:0}.app-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.app-time{flex:none}.window-count{display:inline-block;color:var(--muted);font-size:10px;line-height:1;margin-left:6px;padding:2px 5px;border:1px solid var(--border);border-radius:8px;vertical-align:1px}.track{height:7px;background:var(--track);border-radius:9px;margin-top:7px;overflow:hidden}
.windows{border-left:2px solid var(--border);margin:10px 0 0 7px;padding-left:12px;min-width:0;max-width:100%}.window{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;color:var(--muted);font-size:13px;margin:8px 0;min-width:0;max-width:100%}.window-detail{flex:1;min-width:0;max-width:100%}.window-detail summary{cursor:pointer;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;list-style:none}.window-detail summary::-webkit-details-marker{display:none}.window-full{color:var(--text);margin-top:6px;overflow-wrap:anywhere;white-space:normal}.window-time{flex:none;white-space:nowrap}
.fill{height:100%;background:var(--accent);border-radius:9px}.empty{color:var(--muted);padding:30px 0;text-align:center}
.settings{grid-area:settings;position:relative;flex:none;max-width:100%}.settings>summary{display:flex;align-items:center;width:fit-content;min-width:44px;min-height:44px;list-style:none;cursor:pointer;border:1px solid var(--border);border-radius:9px;padding:10px;background:var(--card);font-size:13px}.settings>summary::-webkit-details-marker{display:none}.settings-icon{width:22px;height:22px;flex:none}.settings>summary:focus-visible,.settings-panel :focus-visible{outline:2px solid var(--accent);outline-offset:3px}.settings-update{margin-left:6px;padding:2px 6px;border-radius:6px;background:var(--button);color:var(--accent);font-size:12px}.settings-update:not([hidden]){display:inline-block}.settings-panel{position:absolute;right:0;top:calc(100% + 8px);z-index:3;width:min(340px,calc(100vw - 40px));max-height:calc(100dvh - 180px);overflow-y:auto;padding:18px;background:var(--card);border:1px solid var(--border);border-radius:12px;box-shadow:0 8px 24px #0002}.settings-panel h2{font-size:14px;margin:0 0 10px}.settings-panel section+section{border-top:1px solid var(--border);margin-top:16px;padding-top:16px}
.update-status{font-size:13px;color:var(--muted);overflow-wrap:anywhere}.update-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:12px}.update-status a{color:var(--accent)}.update-status button{font:inherit;padding:6px 9px}.update-status button:disabled{opacity:.5;cursor:default}.update-status[data-status="available"]{border-left:3px solid var(--accent);padding-left:8px;color:var(--text)}
.feedback-links{display:flex;flex-direction:column;gap:8px;font-size:13px}.feedback-links a{color:var(--accent);padding:4px 0;text-underline-offset:3px}
@media(min-width:751px){main{height:max(720px,100dvh);display:grid;grid-template-rows:auto auto minmax(0,1fr);padding:24px 20px}.grid{min-height:0}.chart-area{min-height:0}.applications-card{display:flex;flex-direction:column;min-height:0}.applications-card> :not(#apps){flex-shrink:0}#apps{flex:1;min-height:0;overflow-y:auto;scrollbar-gutter:stable;padding:3px}.chart-heading{flex-shrink:0}}
@media(max-width:750px){header{flex-direction:column}.header-controls{width:100%;grid-template-columns:minmax(0,1fr);grid-template-areas:"navigation" "settings" "periods"}.report-nav{width:100%;flex-wrap:wrap}.report-label{flex:1 1 calc(100% - 104px);padding:9px 8px;gap:6px}.report-nav button{padding:9px}.report-nav .current-period{margin-left:auto}.settings{justify-self:end}.settings[open]{width:100%}.settings>summary{margin-left:auto}.settings-panel{position:static;width:100%;max-height:none;margin-top:8px;box-shadow:none}#periods button{padding:9px 4px}.metrics,.grid{grid-template-columns:1fr}.chart-area{min-height:210px}.y-axis{width:48px}}
</style>
</head>
<body><main>
<header><div class="header-brand"><h1>App Activity</h1><div class="tracking-badge">Local-only tracking</div></div>
<div class="header-controls"><div class="report-nav" role="group" aria-label="Report navigation"><button id="previousPeriod" type="button" title="Previous period" aria-label="Previous period">←</button><div class="report-label"><svg class="calendar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M7 3v5M17 3v5"/></svg><span id="label" role="status">Loading...</span></div><button id="nextPeriod" type="button" title="Next period" aria-label="Next period" disabled>→</button><button id="currentPeriod" class="current-period" type="button" title="Go to current period" disabled>Today</button></div>
<details class="settings" id="settings"><summary id="settingsSummary" aria-label="Settings" title="Settings"><svg class="settings-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 3h6l.6 3 2.4 1.4 2.9-.9 3 5.2-2.3 2v2.8l2.3 2-3 5.2-2.9-.9-2.4 1.4-.6 3H9l-.6-3L6 21.8l-2.9.9-3-5.2 2.3-2v-2.8l-2.3-2 3-5.2 2.9.9L8.4 6Z" transform="translate(2 0) scale(.83)"/><circle cx="12" cy="12" r="3"/></svg><span class="settings-update" id="settingsUpdate" role="status" hidden></span></summary>
<div class="settings-panel">
  <section aria-labelledby="appearanceHeading"><h2 id="appearanceHeading">Appearance</h2><label class="theme-setting" for="themeSelect">Theme<select id="themeSelect"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label></section>
  <section aria-labelledby="exportHeading"><h2 id="exportHeading">Export</h2><form class="export-controls" id="exportForm" action="/api/export" method="get"><label>From<input id="exportStart" name="start" type="date" required></label><label>To<input id="exportEnd" name="end" type="date" required></label><button type="submit">Export CSV</button><a href="/api/export?all=1">Export all</a></form></section>
  <section aria-labelledby="updatesHeading"><h2 id="updatesHeading">Updates</h2><div class="update-status" id="updateStatus"><span id="updateMessage" role="status">Checking for updates...</span><div class="update-actions"><a id="updateLink" href="https://github.com/acangialosi/app-activity/releases" target="_blank" rel="noopener noreferrer">View releases</a><button id="checkUpdates" type="button" aria-describedby="updateMessage" disabled>Check now</button></div></div></section>
  <section aria-labelledby="feedbackHeading"><h2 id="feedbackHeading">Feedback</h2><div class="feedback-links">
    <a href="https://github.com/acangialosi/app-activity/issues/new?title=%5BProblem%5D%20Briefly%20describe%20the%20problem&amp;body=%23%23%20What%20happened%3F%0ADescribe%20the%20problem.%0A%0A%23%23%20Steps%20to%20reproduce%0A1.%20%0A%0A%23%23%20Expected%20behavior%0AWhat%20did%20you%20expect%3F%0A%0A%23%23%20Environment%0AApp%20Activity%20version%20and%20Windows%20version%20%28if%20known%29%3A%0A%0APlease%20omit%20private%20information%2C%20window%20titles%2C%20and%20activity%20history.%0A" target="_blank" rel="noopener noreferrer">Report a problem</a>
    <a href="https://github.com/acangialosi/app-activity/issues/new?title=%5BFeature%5D%20Briefly%20describe%20the%20idea&amp;body=%23%23%20Feature%20idea%0AWhat%20would%20you%20like%20App%20Activity%20to%20do%3F%0A%0A%23%23%20Why%20would%20this%20help%3F%0ADescribe%20your%20use%20case.%0A%0A%23%23%20Alternatives%20considered%0AOptional%3A%20describe%20other%20approaches.%0A%0APlease%20omit%20private%20information%2C%20window%20titles%2C%20and%20activity%20history.%0A" target="_blank" rel="noopener noreferrer">Suggest a feature</a>
  </div></section>
</div></details>
<nav id="periods" aria-label="Report period"></nav>
</div></header>
<section class="metrics">
  <div class="card metric"><span class="muted">Active</span><strong id="active">-</strong></div>
  <div class="card metric"><span class="muted">Idle</span><strong id="idle">-</strong></div>
  <div class="card metric"><span class="muted">Tracked</span><strong id="tracked">-</strong></div>
</section>
<section class="grid">
  <div class="card chart-card"><div class="chart-heading"><h2>Active time by application</h2><div class="chart-toggle"><button id="timeMode" class="active" type="button">Total time</button><button id="percentMode" type="button">Percentage</button></div></div><div class="chart-area"><div class="y-axis" id="yAxis" aria-hidden="true"></div><div class="timeline" id="timeline"></div></div></div>
  <div class="card applications-card"><div class="application-heading"><h2>Applications</h2><button id="showAllApplications" type="button" disabled>Show all</button></div><p class="application-help" id="applicationHelp">Click an application to show only it; click it again to show all. Ctrl+click adds or removes applications. Show all clears the selection. Percentages show each application's share of all active time.</p><p class="filter-status" id="filterStatus" role="status">Chart: All applications</p><div id="apps"></div></div>
</section>
</main>
<script>
const periods=["hour","day","week","month","year"],selectedApplications=new Set(),colors=new Map();let selected="day",periodOffset=0,chartMode="time",currentData=null;
const palette=["#72a7ff","#5ed6a7","#f6c85f","#ef7b7b","#bd8cff","#5bc0eb","#ff9f68","#8bd450","#e66fbb","#9ca7ff","#4fd1c5","#d4a373"];
const fmt=s=>{const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h}h ${m}m`:m?`${m}m`:`${Math.round(s)}s`};
function storedTheme(){try{const theme=localStorage.getItem("appActivityTheme");return theme==="light"||theme==="dark"?theme:"system"}catch{return"system"}}
function setTheme(theme){if(theme==="light"||theme==="dark")document.documentElement.dataset.theme=theme;else delete document.documentElement.dataset.theme;themeSelect.value=theme;try{localStorage.setItem("appActivityTheme",theme)}catch{}}
const settings=document.getElementById("settings"),settingsSummary=document.getElementById("settingsSummary");
settings.onkeydown=event=>{if(event.key==="Escape"&&settings.open&&!event.defaultPrevented){event.preventDefault();settings.open=false;settingsSummary.focus()}};
// Pointer focus changes must not collapse the inline panel before the outside click.
document.addEventListener("keyup",event=>{if(event.key==="Tab"&&!settings.contains(document.activeElement))settings.open=false});
document.addEventListener("click",event=>{if(!settings.contains(event.target))settings.open=false});
function nav(){periods.forEach(p=>{const b=document.createElement("button");b.type="button";b.textContent=p[0].toUpperCase()+p.slice(1);b.className=p===selected?"active":"";b.ariaPressed=String(p===selected);b.onclick=()=>{selected=p;periodOffset=0;document.querySelectorAll("#periods button").forEach(x=>{x.className="";x.ariaPressed="false"});b.className="active";b.ariaPressed="true";load()};periodsEl.appendChild(b)})}
function setChartMode(mode){chartMode=mode;timeMode.className=mode==="time"?"active":"";percentMode.className=mode==="percent"?"active":"";if(currentData)renderChart(currentData)}
function updateApplicationSelection(){
 document.querySelectorAll(".app-select").forEach(button=>{button.setAttribute("aria-pressed",String(selectedApplications.has(currentData.apps[Number(button.dataset.appIndex)].name)))});
 showAllApplications.disabled=selectedApplications.size===0;
 const available=new Set(currentData.apps.map(app=>app.name));
 filterStatus.textContent=selectedApplications.size?`Chart: ${[...selectedApplications].map(name=>`${name}${available.has(name)?"":" (no activity)"}`).join(", ")}`:"Chart: All applications";
}
function selectApplication(name,additive=false){
 if(additive){if(!selectedApplications.delete(name))selectedApplications.add(name)}
 else{const reset=selectedApplications.size===1&&selectedApplications.has(name);selectedApplications.clear();if(!reset)selectedApplications.add(name)}
 if(currentData){updateApplicationSelection();renderChart(currentData)}
}
function chartBuckets(d){if(!selectedApplications.size)return d.timeline;return d.timeline.map(x=>{
 const apps=x.apps.filter(a=>selectedApplications.has(a.name));
 // Independently rounded app durations must not exceed the original bucket total.
 const seconds=apps.length===x.apps.length?x.seconds:Math.min(x.seconds,apps.reduce((total,a)=>total+a.seconds,0));
 return{...x,apps,seconds,total_seconds:x.seconds};
})}
function niceMax(value){const minimumStep=Math.max(value,1)/4,steps=[1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200,10800,21600,43200,86400];return (steps.find(step=>step>=minimumStep)||Math.ceil(minimumStep/86400)*86400)*4}
function timeAxis(dataMax,capacity){const steps=[1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200,10800,21600,43200,86400];for(const step of steps){const intervals=Math.ceil(dataMax/step),max=intervals*step;if(intervals>=4&&intervals<=6&&max<=capacity)return{max,ticks:Array.from({length:intervals+1},(_,i)=>max-i*step)}}const max=Math.min(niceMax(dataMax),capacity);return{max,ticks:[4,3,2,1,0].map(i=>max*i/4)}}
function fixedTimeAxis(capacity,intervals){const step=capacity/intervals;return{max:capacity,ticks:Array.from({length:intervals+1},(_,i)=>capacity-i*step)}}
function renderChart(d){const buckets=chartBuckets(d),dataMax=Math.max(...buckets.map(x=>x.seconds),1),capacity=Math.max(...buckets.map(x=>x.capacity_seconds),1),fixedIntervals=d.period==="hour"?5:d.period==="day"?4:0,axis=chartMode==="percent"?{max:100,ticks:[100,75,50,25,0]}:fixedIntervals?fixedTimeAxis(capacity,fixedIntervals):timeAxis(dataMax,capacity);timeline.style.setProperty("--grid-step",`${100/(axis.ticks.length-1)}%`);yAxis.innerHTML=axis.ticks.map(value=>`<span>${chartMode==="percent"?`${value}%`:fmt(value)}</span>`).join("");timeline.innerHTML=buckets.map((x,i)=>{const total=x.total_seconds??x.seconds,segmentTotal=x.apps.reduce((sum,a)=>sum+a.seconds,0),segments=x.apps.map(a=>{const share=a.seconds/Math.max(segmentTotal,1)*100,percent=share*x.seconds/Math.max(total,1);const tip=chartMode==="percent"?`${a.name}: ${percent.toFixed(1)}%`:`${a.name}: ${fmt(a.seconds)}`;return `<div class="bar-segment" title="${escapeHtml(tip)}" style="height:${share}%;background:${colors.get(a.name)||palette[0]}"></div>`}).join("");const height=chartMode==="percent"?(total?x.seconds/total*100:1):Math.max(1,x.seconds/axis.max*100);const totalTip=chartMode==="percent"?`${x.label}: application share`:`${x.label}: ${fmt(x.seconds)}`;return `<div class="bar-wrap" title="${escapeHtml(totalTip)}"><div class="bar-stack" style="height:${height}%">${segments}</div><div class="bar-label">${selected==="month"&&i%3?"":x.label}</div></div>`}).join("")}
function updateAppTooltips(){document.querySelectorAll(".app-name[data-app-index]").forEach(label=>{const app=currentData?.apps[Number(label.dataset.appIndex)];label.title=app&&label.scrollWidth>label.clientWidth?app.name:""})}
async function load(){currentPeriod.textContent={hour:"This hour",day:"Today",week:"This week",month:"This month",year:"This year"}[selected];nextPeriod.disabled=periodOffset>=0;currentPeriod.disabled=periodOffset===0;const r=await fetch(`/api/report?period=${selected}&offset=${periodOffset}`),d=await r.json();label.textContent=d.label;const rangeKey=`${d.start}|${d.end}`;if(exportStart.dataset.rangeKey!==rangeKey){const end=new Date(`${d.end.slice(0,19)}Z`);end.setUTCMilliseconds(-1);exportStart.value=d.start.slice(0,10);exportEnd.value=end.toISOString().slice(0,10);exportStart.dataset.rangeKey=rangeKey}active.textContent=fmt(d.active_seconds);idle.textContent=fmt(d.idle_seconds);tracked.textContent=fmt(d.tracked_seconds);
 currentData=d;d.apps.forEach(app=>{if(!colors.has(app.name))colors.set(app.name,palette[colors.size%palette.length])});renderChart(d);
 apps.innerHTML=d.apps.length?d.apps.map((x,i)=>{const head=`<button class="app-select" type="button" data-app-index="${i}" aria-pressed="${selectedApplications.has(x.name)}" aria-controls="timeline" aria-describedby="applicationHelp"><span class="app-head"><span class="app-name" data-app-index="${i}"><span class="color-dot" style="background:${colors.get(x.name)}"></span> ${escapeHtml(x.name)}</span><span class="app-time">${fmt(x.seconds)} · ${x.percent}%</span></span><span class="track" style="display:block"><span class="fill" style="display:block;width:${x.percent}%;background:${colors.get(x.name)}"></span></span></button>`;if(!x.windows.length)return `<div class="app">${head}</div>`;const windows=x.windows.slice(0,20).map(w=>{const title=escapeHtml(w.title);return `<div class="window"><details class="window-detail"><summary title="${title}">${title}</summary><div class="window-full">${title}</div></details><span class="window-time">${fmt(w.seconds)} · ${w.percent}%</span></div>`}).join("");return `<div class="app">${head}<details class="app-details"><summary>${x.windows.length} window title${x.windows.length===1?"":"s"}</summary><div class="windows">${windows}</div></details></div>`}).join(""):`<div class="empty">No activity recorded yet.</div>`;updateApplicationSelection();requestAnimationFrame(updateAppTooltips)}
function escapeHtml(v){const e=document.createElement("div");e.textContent=v;return e.innerHTML.replace(/"/g,"&quot;")}
let updateRequestPending=false,updateRetry;
async function loadUpdate(force=false){
 if(updateRequestPending)return;
 updateRequestPending=true;clearTimeout(updateRetry);
 const status=document.getElementById("updateStatus"),message=document.getElementById("updateMessage"),link=document.getElementById("updateLink"),button=document.getElementById("checkUpdates");
 button.disabled=true;
 if(force){status.dataset.status="pending";message.textContent="Checking for updates..."}
 try{const response=await fetch("/api/update",{method:force?"POST":"GET"});if(!response.ok)throw new Error("Update status unavailable");const data=await response.json();status.dataset.status=data.status;message.textContent=`${data.version}${data.channel?` (${data.channel})`:""} - ${data.message}`;link.href=data.release_url;link.textContent=data.status==="available"?"Download update":"View releases";button.disabled=data.status==="pending"||!data.channel;
 if(["available","current","source"].includes(data.status)){const badge=document.getElementById("settingsUpdate");badge.hidden=data.status!=="available";badge.textContent=badge.hidden?"":"Update available"}
 if(data.status==="pending")updateRetry=setTimeout(loadUpdate,1000)}
 catch{status.dataset.status="error";message.textContent="Update status unavailable. Check GitHub releases manually.";button.disabled=false}
 finally{updateRequestPending=false}
}
document.getElementById("checkUpdates").onclick=()=>loadUpdate(true);
loadUpdate();setInterval(loadUpdate,60000);
const periodsEl=document.getElementById("periods");previousPeriod.onclick=()=>{periodOffset--;load()};nextPeriod.onclick=()=>{if(periodOffset<0){periodOffset++;load()}};currentPeriod.onclick=()=>{if(periodOffset!==0){periodOffset=0;load()}};timeMode.onclick=()=>setChartMode("time");percentMode.onclick=()=>setChartMode("percent");apps.onclick=event=>{const button=event.target.closest(".app-select");if(button&&apps.contains(button))selectApplication(currentData.apps[Number(button.dataset.appIndex)].name,event.ctrlKey)};showAllApplications.onclick=()=>{selectedApplications.clear();if(currentData){updateApplicationSelection();renderChart(currentData)}};themeSelect.onchange=()=>setTheme(themeSelect.value);addEventListener("resize",updateAppTooltips);setTheme(storedTheme());nav();load();setInterval(load,60000);
</script></body></html>"""


def create_server(database: ActivityDatabase, port: int) -> ThreadingHTTPServer:
    updates = UpdateChecker()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if urlparse(self.path).path == "/api/update":
                port = self.server.server_address[1]
                if self.headers.get("Origin") not in (
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                ):
                    self._json(403, {"error": "Update checks require a local dashboard origin"})
                    return
                self._json(200, updates.status(force=True))
                return
            self._json(404, {"error": "Not found"})

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
            if parsed.path == "/api/export":
                parameters = parse_qs(parsed.query)
                try:
                    if parameters.get("all") == ["1"]:
                        start_ts = end_ts = None
                    else:
                        start_date = date.fromisoformat(
                            parameters.get("start", [""])[0]
                        )
                        end_date = date.fromisoformat(
                            parameters.get("end", [""])[0]
                        )
                        if end_date < start_date:
                            raise ValueError("End date must not be before start date")
                        try:
                            start = datetime.combine(start_date, time.min).astimezone()
                            end = datetime.combine(
                                end_date + timedelta(days=1), time.min
                            ).astimezone()
                            start_ts = start.timestamp()
                            end_ts = end.timestamp()
                        except OSError as error:
                            raise ValueError(f"Unsupported date bounds: {error}") from error
                    output = io.StringIO(newline="")
                    writer = csv.writer(output)
                    writer.writerow([
                        "start",
                        "end",
                        "application",
                        "idle",
                        "window_title",
                        "duration_seconds",
                    ])
                    for segment_start, segment_end, app_name, idle, title in (
                        database.export_rows(start_ts, end_ts)
                    ):
                        writer.writerow(
                            [
                                datetime.fromtimestamp(
                                    segment_start
                                ).astimezone().isoformat(),
                                datetime.fromtimestamp(
                                    segment_end
                                ).astimezone().isoformat(),
                                _spreadsheet_safe(app_name),
                                idle,
                                _spreadsheet_safe(title),
                                round(segment_end - segment_start, 3),
                            ]
                        )
                except (TypeError, ValueError, OverflowError) as error:
                    self._json(400, {"error": str(error)})
                else:
                    self._send(
                        200,
                        "text/csv; charset=utf-8",
                        output.getvalue().encode("utf-8"),
                        "attachment; filename=app-activity.csv",
                    )
                return
            if parsed.path == "/api/status":
                self._json(200, {"status": "running"})
                return
            if parsed.path == "/api/update":
                self._json(200, updates.status())
                return
            self._json(404, {"error": "Not found"})

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                database.close()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: object) -> None:
            self._send(
                status,
                "application/json; charset=utf-8",
                json.dumps(payload).encode(),
            )

        def _send(
            self,
            status: int,
            content_type: str,
            body: bytes,
            content_disposition: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if content_disposition:
                self.send_header("Content-Disposition", content_disposition)
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _spreadsheet_safe(value: str) -> str:
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
