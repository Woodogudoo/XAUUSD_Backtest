# ============================================================================
# bt/viewer.py
# ----------------------------------------------------------------------------
# HTML viewer แบบ MT5 — offline 100% (วาดบน <canvas> ด้วย vanilla JS, ไม่มี dependency)
# อ่าน OHLC (list[Bar]) + trades → เขียน viewer.html ไฟล์เดียว (ฝังข้อมูล+JS)
# วาด: แท่งเทียน + pan/zoom + marker entry/exit + เส้น SL/TP + รายการจุดเข้า (ค้นหา/คลิกกระโดด)
#
# v1: แสดงเฉพาะไม้ที่ fill+ปิดแล้ว (ไม้ที่ไม่ fill = milestone ถัดไป ต้องให้ engine เก็บเพิ่ม)
# ============================================================================
from __future__ import annotations

import json
from collections import defaultdict


def _ohlc_payload(bars) -> dict:
    t, o, h, l, c = [], [], [], [], []
    for b in bars:
        t.append(int(b.time.timestamp()))
        o.append(round(b.open, 3)); h.append(round(b.high, 3))
        l.append(round(b.low, 3));  c.append(round(b.close, 3))
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def _trades_payload(trades) -> list[dict]:
    return [{
        "plan": t["plan_id"], "tag": t["tag"], "dir": t["direction"], "kind": t["kind"],
        "eb": t["entry_bar"], "xb": t["exit_bar"],
        "entry": round(t["entry"], 3), "sl": round(t["sl"], 3),
        "tp": round(t["tp"], 3), "exit": round(t["exit_price"], 3),
        "result": t["result"], "pnl": t["pnl_pips"], "pnlusd": t["pnl_usd"],
    } for t in trades]


def _unfilled_payload(unfilled) -> list[dict]:
    out = []
    for u in (unfilled or []):
        lp = u.get("limit_price")
        out.append({
            "plan": u.get("plan_id"), "tag": u.get("tag"), "dir": u.get("direction"),
            "pb": u.get("place_bar"), "db": u.get("death_bar"),
            "price": round(lp, 3) if lp is not None else None,
            "reason": u.get("reason"),
        })
    return out


def _plans_payload(trades) -> list[dict]:
    groups = defaultdict(list)
    for t in trades:
        groups[t["plan_id"]].append(t)
    out = []
    for pid in sorted(groups):
        g = groups[pid]
        net = round(sum(t["pnl_usd"] for t in g), 2)
        out.append({
            "plan_id": pid, "direction": g[0]["direction"],
            "entry_time": min(str(t["entry_time"]) for t in g),
            "eb": min(t["entry_bar"] for t in g),
            "n_trades": len(g), "net_pnl_usd": net,
            "result": "WIN" if net > 0 else "LOSS",
        })
    return out


def write_viewer(bars, trades, out_path: str, title: str = "backtest",
                 unfilled=None) -> str:
    data = {
        "ohlc": _ohlc_payload(bars),
        "trades": _trades_payload(trades),
        "plans": _plans_payload(trades),
        "unfilled": _unfilled_payload(unfilled),
    }
    html = _TEMPLATE.replace("__TITLE__", title).replace(
        "__DATA__", json.dumps(data, separators=(",", ":")))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="th"><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
 *{box-sizing:border-box}
 body{margin:0;background:#0e0e12;color:#cfd2dc;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;display:flex;height:100vh;overflow:hidden}
 #side{width:290px;flex:none;background:#15161c;border-right:1px solid #23252e;display:flex;flex-direction:column}
 #side h1{font-size:14px;margin:10px 12px 2px;color:#e8eaf0}
 #stat{padding:0 12px 8px;font-size:12px;color:#9aa0b0}
 #search{margin:6px 12px;padding:6px 8px;background:#0e0e12;border:1px solid #2a2d38;color:#cfd2dc;border-radius:4px}
 #list{flex:1;overflow:auto}
 .row{padding:6px 12px;border-bottom:1px solid #1c1e26;cursor:pointer}
 .row:hover{background:#1c1e28}
 .win{border-left:3px solid #26a69a}.loss{border-left:3px solid #ef5350}
 .row .t{color:#9aa0b0;font-size:11px}
 #main{flex:1;position:relative}
 canvas{display:block;cursor:crosshair}
 #tip{position:absolute;pointer-events:none;background:#1c1e28;border:1px solid #2a2d38;padding:6px 8px;border-radius:4px;font-size:12px;display:none;white-space:pre;z-index:5}
 #bar{position:absolute;top:6px;left:10px;font-size:12px;color:#9aa0b0;pointer-events:none}
 #help{position:absolute;bottom:6px;left:10px;font-size:11px;color:#5a5f70;pointer-events:none}
 #xtoggle{position:absolute;top:6px;right:10px;font-size:12px;color:#9aa0b0;user-select:none;background:#15161c;border:1px solid #23252e;padding:3px 8px;border-radius:4px;z-index:6}
 #xtoggle label{cursor:pointer;margin-left:8px}
 #xtoggle label:first-child{margin-left:0}
 #xtoggle input{vertical-align:-1px;margin-right:4px}
</style></head>
<body>
<div id="side">
 <h1>__TITLE__</h1>
 <div id="stat"></div>
 <input id="search" placeholder="ค้นหาจุดเข้า (เวลา / BUY / SELL / WIN / LOSS)">
 <div id="list"></div>
</div>
<div id="main">
 <div id="bar"></div>
 <div id="xtoggle"><label><input type="checkbox" id="ecb" checked>entry</label><label><input type="checkbox" id="xcb">crosshair</label></div>
 <canvas id="cv"></canvas>
 <div id="tip"></div>
 <div id="help">scroll = zoom · drag = pan · คลิกรายการซ้ายเพื่อกระโดด</div>
</div>
<script>
const DATA=__DATA__, O=DATA.ohlc, TR=DATA.trades, PL=DATA.plans, N=O.t.length, UF=DATA.unfilled||[];
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const main=document.getElementById('main'), tip=document.getElementById('tip'), barEl=document.getElementById('bar');
let cnt=Math.min(300,N), i0=Math.max(0,N-cnt), W=0,Hgt=0, dpr=window.devicePixelRatio||1;
let cross=false, eline=true, mx=-1, my=-1, rafP=false;
function reqDraw(){if(rafP)return;rafP=true;requestAnimationFrame(function(){rafP=false;draw();});}
function resize(){W=main.clientWidth;Hgt=main.clientHeight;cv.width=W*dpr;cv.height=Hgt*dpr;cv.style.width=W+'px';cv.style.height=Hgt+'px';ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
window.addEventListener('resize',resize);
function fmtT(s){var d=new Date(s*1000),p=function(n){return String(n).padStart(2,'0')};return d.getUTCFullYear()+'-'+p(d.getUTCMonth()+1)+'-'+p(d.getUTCDate())+' '+p(d.getUTCHours())+':'+p(d.getUTCMinutes());}
function vis(){var b=Math.min(N,i0+cnt),lo=1e18,hi=-1e18,i;for(i=i0;i<b;i++){if(O.l[i]<lo)lo=O.l[i];if(O.h[i]>hi)hi=O.h[i];}
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.xb>=i0&&t.eb<b){[t.sl,t.tp,t.entry,t.exit].forEach(function(v){if(v<lo)lo=v;if(v>hi)hi=v;});}}
 var pad=(hi-lo)*0.08||1;return [i0,b,lo-pad,hi+pad];}
function draw(){
 ctx.clearRect(0,0,W,Hgt);var r=vis(),a=r[0],b=r[1],lo=r[2],hi=r[3],n=b-a;if(n<=0)return;
 var cw=W/n,ph=Hgt-24,X=function(i){return (i-a)*cw+cw/2;},Y=function(p){return 4+(hi-p)/(hi-lo)*ph;};
 ctx.lineWidth=1;ctx.font='11px monospace';
 for(var k=0;k<=4;k++){var p=lo+(hi-lo)*k/4,y=Y(p);ctx.strokeStyle='#1c1e26';ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();ctx.fillStyle='#5a5f70';ctx.fillText(p.toFixed(2),4,y-2);}
 var bw=Math.max(1,cw*0.7);
 for(var i=a;i<b;i++){var x=X(i),up=O.c[i]>=O.o[i];ctx.strokeStyle=up?'#26a69a':'#ef5350';ctx.fillStyle=ctx.strokeStyle;
  ctx.beginPath();ctx.moveTo(x,Y(O.h[i]));ctx.lineTo(x,Y(O.l[i]));ctx.stroke();
  var y1=Y(O.o[i]),y2=Y(O.c[i]);ctx.fillRect(x-bw/2,Math.min(y1,y2),bw,Math.max(1,Math.abs(y2-y1)));}
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.xb<a||t.eb>=b)continue;var win=t.result==='TP';
  var xe=X(Math.max(a,t.eb)),xx=X(Math.min(b-1,t.xb));
  ctx.setLineDash([4,3]);
  ctx.strokeStyle='rgba(239,83,80,.8)';ctx.beginPath();ctx.moveTo(xe,Y(t.sl));ctx.lineTo(xx,Y(t.sl));ctx.stroke();
  ctx.strokeStyle='rgba(38,166,154,.8)';ctx.beginPath();ctx.moveTo(xe,Y(t.tp));ctx.lineTo(xx,Y(t.tp));ctx.stroke();
  var buy=t.dir==='BUY';
  if(eline){ctx.setLineDash([6,4]);ctx.strokeStyle=buy?'rgba(66,165,245,.9)':'rgba(255,167,38,.9)';ctx.beginPath();ctx.moveTo(xe,Y(t.entry));ctx.lineTo(xx,Y(t.entry));ctx.stroke();}
  ctx.setLineDash([]);
  var xb=X(t.eb),ye=Y(t.entry);ctx.fillStyle=buy?'#42a5f5':'#ffa726';
  ctx.beginPath();if(buy){ctx.moveTo(xb,ye+9);ctx.lineTo(xb-5,ye+18);ctx.lineTo(xb+5,ye+18);}else{ctx.moveTo(xb,ye-9);ctx.lineTo(xb-5,ye-18);ctx.lineTo(xb+5,ye-18);}ctx.closePath();ctx.fill();
  ctx.fillStyle=win?'#26a69a':'#ef5350';ctx.fillRect(X(t.xb)-3,Y(t.exit)-3,6,6);}
 for(var k=0;k<UF.length;k++){var u=UF[k];if(u.price==null||u.db<a||u.pb>=b)continue;
  var x1=X(Math.max(a,u.pb)),x2=X(Math.min(b-1,u.db)),y=Y(u.price);
  ctx.setLineDash([2,3]);ctx.strokeStyle='rgba(150,154,170,.5)';ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2,y);ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle='rgba(150,154,170,.85)';var xp=X(u.pb);ctx.beginPath();ctx.moveTo(xp,y-4);ctx.lineTo(xp+4,y);ctx.lineTo(xp,y+4);ctx.lineTo(xp-4,y);ctx.closePath();ctx.stroke();
  var xd=X(u.db);ctx.beginPath();ctx.moveTo(xd-3,y-3);ctx.lineTo(xd+3,y+3);ctx.moveTo(xd+3,y-3);ctx.lineTo(xd-3,y+3);ctx.stroke();}
 if(cross&&mx>=0&&mx<=W&&my>=0&&my<=Hgt){
  ctx.setLineDash([3,3]);ctx.lineWidth=1;ctx.strokeStyle='rgba(180,184,200,.45)';
  ctx.beginPath();ctx.moveTo(mx,0);ctx.lineTo(mx,Hgt);ctx.stroke();
  ctx.beginPath();ctx.moveTo(0,my);ctx.lineTo(W,my);ctx.stroke();ctx.setLineDash([]);
  ctx.font='11px monospace';
  var pr=(hi-(my-4)/ph*(hi-lo)).toFixed(2),pw=ctx.measureText(pr).width+8;
  ctx.fillStyle='#3a3f4e';ctx.fillRect(W-pw-1,my-8,pw,16);ctx.fillStyle='#e8eaf0';ctx.fillText(pr,W-pw+3,my+3);
  var bi=Math.max(a,Math.min(b-1,a+Math.floor(mx/cw))),tt=fmtT(O.t[bi])+'  #'+bi,tw=ctx.measureText(tt).width+8,tx=Math.min(Math.max(mx-tw/2,0),W-tw);
  ctx.fillStyle='#3a3f4e';ctx.fillRect(tx,Hgt-16,tw,16);ctx.fillStyle='#e8eaf0';ctx.fillText(tt,tx+4,Hgt-4);
 }
 barEl.textContent=fmtT(O.t[a])+'  →  '+fmtT(O.t[Math.min(N-1,b-1)])+'   ('+n+' / '+N+' แท่ง)';
}
var drag=null;
cv.addEventListener('mousedown',function(e){drag={x:e.clientX,i0:i0};});
window.addEventListener('mouseup',function(){drag=null;});
window.addEventListener('mousemove',function(e){
 var r=cv.getBoundingClientRect();mx=e.clientX-r.left;my=e.clientY-r.top;
 if(drag){var cw=W/cnt,di=Math.round((e.clientX-drag.x)/cw);i0=Math.max(0,Math.min(N-cnt,drag.i0-di));draw();return;}
 if(mx<0||mx>W||my<0||my>Hgt){tip.style.display='none';if(cross)reqDraw();return;}
 if(cross)reqDraw();
 var cw=W/cnt,i=i0+Math.floor(mx/cw);if(i<0||i>=N){tip.style.display='none';return;}
 var s='แท่ง '+i+'  '+fmtT(O.t[i])+'\nO '+O.o[i]+'  H '+O.h[i]+'  L '+O.l[i]+'  C '+O.c[i];
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.eb===i||t.xb===i){s+='\n— #'+t.plan+' '+t.dir+' '+t.tag+'\n  entry '+t.entry+'  '+t.result+'@'+t.exit+'\n  SL '+t.sl+'  TP '+t.tp+'  pnl '+t.pnl+'pip';}}
 for(var k=0;k<UF.length;k++){var u=UF[k];if(u.pb===i||u.db===i){s+='\n— #'+u.plan+' '+u.dir+' '+u.tag+' (ไม่ fill)\n  limit '+u.price+'  '+u.reason;}}
 tip.textContent=s;tip.style.display='block';tip.style.left=Math.min(mx+14,W-180)+'px';tip.style.top=(my+14)+'px';
});
cv.addEventListener('mouseleave',function(){tip.style.display='none';mx=-1;my=-1;if(cross)reqDraw();});
document.getElementById('xcb').addEventListener('change',function(e){cross=e.target.checked;draw();});
document.getElementById('ecb').addEventListener('change',function(e){eline=e.target.checked;draw();});
cv.addEventListener('wheel',function(e){e.preventDefault();var r=cv.getBoundingClientRect(),mx=e.clientX-r.left,piv=i0+mx/(W/cnt);
 cnt=Math.max(20,Math.min(N,Math.round(cnt*(e.deltaY>0?1.18:0.85))));i0=Math.max(0,Math.min(N-cnt,Math.round(piv-mx/(W/cnt))));draw();},{passive:false});
function jump(eb){if(cnt>400)cnt=200;i0=Math.max(0,Math.min(N-cnt,eb-Math.floor(cnt/2)));draw();}
var listEl=document.getElementById('list');
function renderList(q){q=(q||'').toUpperCase();listEl.innerHTML='';
 for(var k=0;k<PL.length;k++){var p=PL[k],line=(p.entry_time+' '+p.direction+' '+p.result).toUpperCase();if(q&&line.indexOf(q)<0)continue;
  var d=document.createElement('div');d.className='row '+(p.result==='WIN'?'win':'loss');var col=p.result==='WIN'?'#26a69a':'#ef5350';
  d.innerHTML='<div>#'+p.plan_id+' '+p.direction+'  <b style="color:'+col+'">'+p.net_pnl_usd.toFixed(2)+'</b></div><div class="t">'+p.entry_time+' · '+p.n_trades+' ไม้</div>';
  (function(eb){d.onclick=function(){jump(eb);};})(p.eb);listEl.appendChild(d);}}
document.getElementById('search').addEventListener('input',function(e){renderList(e.target.value);});
var wins=TR.filter(function(t){return t.result==='TP';}).length;
document.getElementById('stat').textContent=TR.length+' ไม้ · WR '+(TR.length?(100*wins/TR.length).toFixed(1):0)+'% · '+PL.length+' จุดเข้า · '+UF.length+' ไม่ fill';
renderList('');resize();
</script></body></html>
"""
