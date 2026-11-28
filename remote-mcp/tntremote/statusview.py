"""Visual device status for AI apps that show MCP Apps views (Claude, ChatGPT, ...).

device_status links to URI through its tool _meta. The host reads this page with
resources/read, shows it in a sandboxed frame and passes it the tool result
(structuredContent = tools.status_data()). Apps without views use the text result.
"""
URI = "ui://nomad/device-status.html"
MIME = "text/html;profile=mcp-app"

HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Device status</title>
<style>
:root{--bg:var(--color-background-primary,#fff);--fg:var(--color-text-primary,#16181d);--mut:var(--color-text-secondary,#5d6470);
--card:var(--color-background-secondary,#f3f5f8);--line:var(--color-border-primary,#dde2e8);--ok:#12884a;--warn:#c27a00;--bad:#c62828;--acc:#1e5ac8;
--font:var(--font-sans,system-ui,-apple-system,"Segoe UI",sans-serif);--mono:var(--font-mono,ui-monospace,Consolas,monospace)}
@media (prefers-color-scheme:dark){:root{--bg:var(--color-background-primary,#17191e);--fg:var(--color-text-primary,#eceef2);
--mut:var(--color-text-secondary,#9aa3b0);--card:var(--color-background-secondary,#22252c);--line:var(--color-border-primary,#343844);--ok:#3ccf7e;--warn:#f0b13a;--bad:#ff6b6b;--acc:#6f9dff}}
html[data-theme=dark]{--bg:var(--color-background-primary,#17191e);--fg:var(--color-text-primary,#eceef2);--mut:var(--color-text-secondary,#9aa3b0);
--card:var(--color-background-secondary,#22252c);--line:var(--color-border-primary,#343844);--ok:#3ccf7e;--warn:#f0b13a;--bad:#ff6b6b;--acc:#6f9dff}
html[data-theme=light]{--bg:var(--color-background-primary,#fff);--fg:var(--color-text-primary,#16181d);--mut:var(--color-text-secondary,#5d6470);
--card:var(--color-background-secondary,#f3f5f8);--line:var(--color-border-primary,#dde2e8);--ok:#12884a;--warn:#c27a00;--bad:#c62828;--acc:#1e5ac8}
*{box-sizing:border-box}body{margin:0;padding:14px;background:transparent;color:var(--fg);font:14px/1.4 var(--font)}
.top{display:flex;align-items:center;gap:10px;margin-bottom:12px}.dot{width:12px;height:12px;border-radius:50%;flex:none}
h1{font-size:17px;margin:0}.sub{color:var(--mut);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.lab{color:var(--mut);font-size:12px}.big{font-size:clamp(16px,3.6vw,22px);font-weight:700;margin:2px 0;white-space:nowrap}.note{color:var(--mut);font-size:12px}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:6px 0 4px;display:flex}.bar i{display:block;height:100%}
h2{font-size:14px;margin:16px 0 6px}table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{color:var(--mut);width:42%}
code{font:12px var(--mono)}.leg{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--mut)}.leg b{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:3px 10px;font-size:12px}
.pill{font-size:12px;font-weight:600}.on{color:var(--ok)}.off{color:var(--mut)}.wait{color:var(--mut);padding:20px 0}
</style></head><body><div id="root"><div class="wait">Loading device status...</div></div>
<script>
(function(){
var C={ok:"var(--ok)",warn:"var(--warn)",bad:"var(--bad)"},nextId=1,root=document.getElementById("root");
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function lvl(p,w,b){return p>=b?"bad":p>=w?"warn":"ok"}
function bar(parts){return '<div class="bar">'+parts.map(function(p){return '<i style="width:'+Math.max(0,Math.min(100,p[0]))+'%;background:'+p[1]+'"></i>'}).join("")+"</div>"}
function card(lab,big,note,pct,l){return '<div class="card"><div class="lab">'+esc(lab)+'</div><div class="big" style="color:'+(l?C[l]:"inherit")+'">'+esc(big)+"</div>"+(pct==null?"":bar([[pct,C[l||"ok"]]]))+'<div class="note">'+esc(note)+"</div></div>"}
function row(k,v){return "<tr><td>"+esc(k)+"</td><td>"+v+"</td></tr>"}
function render(d){
 if(!d||!d.ram_mb){return}
 var r=d.ram_mb,dk=d.disk_gb,rp=r.total?Math.round(100*r.used/r.total):0,dp=dk.total?Math.round(100*dk.used/dk.total):0,t=d.temp_c,h=[];
 h.push('<div class="top"><span class="dot" style="background:'+C[d.level||"ok"]+'"></span><div><h1>'+esc(d.host)+" is online</h1><div class=\"sub\">"+esc(d.app)+" &middot; "+esc(d.overall)+"</div></div></div>");
 h.push('<div class="grid">');
 h.push(card("CPU temperature",t==null?"?":t+" °C",t==null?"":t>=75?"Hot":t>=65?"Warm":"Normal",null,t==null?null:t>=75?"bad":t>=65?"warn":"ok"));
 h.push(card("Uptime",d.uptime,d.uptime_s<3600?"Recently booted":"Running",null,null));
 h.push(card("RAM",r.used+" / "+r.total+" MB",r.available+" MB available · "+rp+"% used",rp,lvl(rp,72,85)));
 h.push(card("Disk",dk.used+" / "+dk.total+" GB",dk.free+" GB free · "+dp+"% used",dp,lvl(dp,80,90)));
 h.push("</div>");
 var T=r.total||1,parts=[[100*r.programs/T,"var(--acc)"],[100*r.graphics_reserved/T,"#9b59d0"],[100*r.caches/T,"#7a8696"],[100*r.free/T,"var(--line)"]];
 h.push("<h2>Memory breakdown</h2>"+bar(parts)+'<div class="leg"><span><b style="background:var(--acc)"></b>Programs '+r.programs+' MB</span><span><b style="background:#9b59d0"></b>Graphics pool '+r.graphics_used+"/"+r.graphics_reserved+' MB</span><span><b style="background:#7a8696"></b>Caches '+r.caches+' MB</span><span><b style="background:var(--line)"></b>Free '+r.free+" MB</span></div>");
 var tb=[row("Hostname",esc(d.host)),row("User",esc(d.user))];
 (d.ips||[]).forEach(function(i){tb.push(row("IP ("+i.interface+")","<code>"+esc(i.address)+"</code>"))});
 tb.push(row("Wi-Fi",esc(d.wifi||"not connected")));
 var tn=d.tunnel||{};tb.push(row("Tunnel ("+(tn.kind||"?")+")",'<span class="pill '+(tn.state=="connected"?"on":"off")+'">'+esc(tn.state)+"</span>"));
 tb.push(row("Public address",tn.address?"<code>"+esc(tn.address)+"</code>":"not set"));
 tb.push(row("CPU load 1/5/15 min",esc((d.load||[]).join(" / "))));
 tb.push(row("Swap",esc(d.swap_mb.used+" / "+d.swap_mb.total+" MB used")));
 h.push("<h2>Network and system</h2><table>"+tb.join("")+"</table>");
 var pm=[];for(var k in d.permissions){pm.push(row(k,'<span class="pill '+(d.permissions[k]?"on":"off")+'">'+(d.permissions[k]?"● On":"○ Off")+"</span>"))}
 h.push("<h2>Permissions</h2><table>"+pm.join("")+"</table>");
 h.push("<h2>Top programs by memory</h2><table>"+(d.top||[]).map(function(p){return row(p.mb+" MB","<code>"+esc(p.program)+"</code>")}).join("")+"</table>");
 h.push("<h2>Installed apps ("+(d.apps||[]).length+')</h2><div class="chips">'+(d.apps||[]).map(function(a){return '<span class="chip">'+esc(a)+"</span>"}).join("")+"</div>");
 root.innerHTML=h.join("");size();
}
// MCP Apps host (Claude, ChatGPT and others): JSON-RPC over postMessage
function send(m){try{window.parent.postMessage(m,"*")}catch(e){}}
function size(){send({jsonrpc:"2.0",method:"ui/notifications/size-changed",params:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight}})}
function theme(ctx){if(!ctx)return;if(ctx.theme)document.documentElement.setAttribute("data-theme",ctx.theme);
 var v=ctx.styles&&ctx.styles.variables;if(v)for(var k in v){document.documentElement.style.setProperty(k,v[k])}}
window.addEventListener("message",function(ev){var m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.id===1&&m.result){theme(m.result.hostContext);send({jsonrpc:"2.0",method:"ui/notifications/initialized",params:{}})}
 else if(m.method==="ui/notifications/tool-result"){render(m.params&&m.params.structuredContent)}
 else if(m.method==="ui/notifications/host-context-changed"){theme(m.params)}
 else if(m.id!=null&&m.method){send({jsonrpc:"2.0",id:m.id,result:{}})}});
send({jsonrpc:"2.0",id:nextId++,method:"ui/initialize",params:{protocolVersion:"2026-01-26",appInfo:{name:"nomad-device-status",version:"1"},clientInfo:{name:"nomad-device-status",version:"1"},appCapabilities:{availableDisplayModes:["inline"]},capabilities:{}}});
// ChatGPT Apps SDK globals, when present
function fromOpenAI(){var o=window.openai;if(o){if(o.theme)theme({theme:o.theme});if(o.toolOutput)render(o.toolOutput)}}
window.addEventListener("openai:set_globals",fromOpenAI);fromOpenAI();
if(window.ResizeObserver)new ResizeObserver(size).observe(document.body);
})();
</script></body></html>"""
