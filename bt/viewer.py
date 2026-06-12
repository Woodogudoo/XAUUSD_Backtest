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

import csv
import json
import os
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
        "result": t["result"], "lot": t["lot"],
        "pnl": t["pnl_pips"], "pnlusd": t["pnl_usd"],
    } for t in trades]


def _unfilled_payload(unfilled) -> list[dict]:
    out = []
    for u in (unfilled or []):
        lp, sl, tp = u.get("limit_price"), u.get("sl"), u.get("tp")
        out.append({
            "plan": u.get("plan_id"), "tag": u.get("tag"), "dir": u.get("direction"),
            "pb": u.get("place_bar"), "db": u.get("death_bar"),
            "price": round(lp, 3) if lp is not None else None,
            "sl": round(sl, 3) if sl is not None else None,
            "tp": round(tp, 3) if tp is not None else None,
            "reason": u.get("reason"),
        })
    return out


def _read_plan_meta(out_path: str) -> list[dict]:
    """อ่าน plan_meta.csv (พ่อ/แม่/%/เหตุผล) จากโฟลเดอร์เดียวกับ viewer.html
    — Phase A เขียนไฟล์นี้ก่อนเรียก viewer แล้ว · ไม่มีไฟล์ → [] (เช่น unit test)"""
    p = os.path.join(os.path.dirname(out_path), "plan_meta.csv")
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            reasons = (row.get("reasons") or "").split(" · ")
            out.append({
                "plan": int(row["plan_id"]),
                "fs":   int(row["father_start_bar"]),
                "fe":   int(row["father_end_bar"]),
                "fp":   row["father_pct"],          # string — รักษาทศนิยม 1 ตำแหน่ง
                "mb":   int(row["mother_bar"]),
                "mp":   row["mother_pct"],
                "reasons": [r for r in reasons if r],
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
        "plan_meta": _read_plan_meta(out_path),
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
 .row.unf{border-left:3px solid #6a6f80;opacity:.62}
 .row .nf{color:#7a8093;font-size:11px;font-style:italic}
 .row .t{color:#9aa0b0;font-size:11px}
 #main{flex:1;position:relative}
 canvas{display:block;cursor:crosshair}
 #tip{position:absolute;pointer-events:none;background:#1c1e28;border:1px solid #2a2d38;padding:6px 8px;border-radius:4px;font-size:12px;display:none;white-space:pre;z-index:5}
 #bar{position:absolute;top:6px;left:10px;font-size:12px;color:#9aa0b0;pointer-events:none}
 #help{position:absolute;bottom:6px;left:10px;font-size:11px;color:#5a5f70;pointer-events:none}
 #xtoggle{position:absolute;top:6px;right:10px;font-size:12px;color:#9aa0b0;user-select:none;background:#15161c;border:1px solid #23252e;padding:3px 8px;border-radius:4px;z-index:6}
 #xtoggle label{cursor:pointer;margin-left:8px}
 #xtoggle label:first-child{margin-left:0}
 #xtoggle input[type=checkbox]{vertical-align:-1px;margin-right:4px}
 #xtoggle input[type=number]{width:44px;background:#0e0e12;border:1px solid #2a2d38;color:#cfd2dc;border-radius:3px;padding:1px 3px;font-size:11px;margin:0 2px}
 #fopt{margin-left:8px;color:#7a8093;display:none}
 .row.open{background:#1c1e28}
 .detail{background:#0e0e12;border-bottom:1px solid #23252e;padding:2px 12px 6px}
 .detail .dl{padding:5px 0;border-bottom:1px solid #1a1c24;font-size:12px;line-height:1.5;cursor:pointer}
 .detail .dl:hover{background:#1c1e28}
 .detail .dl:last-child{border-bottom:0}
 .detail .dl b{color:#e8eaf0}
 .detail .mut{color:#8a90a3}
 .detail .dsub{font-size:10px;color:#5a5f70;padding:6px 0 2px}
 .detail .du{font-size:11px;color:#7a8093;padding:2px 0}
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
 <div id="xtoggle"><label><input type="checkbox" id="fcb">focus</label><label><input type="checkbox" id="xcb">crosshair</label><span id="fopt">N<input type="number" id="iN" value="55" min="5" step="5">ratio<input type="number" id="iw" value="5.5" step="0.5">:<input type="number" id="ih" value="7.5" step="0.5"></span></div>
 <canvas id="cv"></canvas>
 <div id="tip"></div>
 <div id="help">scroll = zoom · drag = pan · คลิกรายการซ้ายเพื่อกระโดด</div>
</div>
<script>
const DATA=__DATA__, O=DATA.ohlc, TR=DATA.trades, PL=DATA.plans, N=O.t.length, UF=DATA.unfilled||[];
// plan_meta (พ่อ/แม่/%/เหตุผล) → lookup ตาม plan_id · ครบทั้ง filled + unfilled
const PM_ARR=DATA.plan_meta||[];var PM={};for(var _pm=0;_pm<PM_ARR.length;_pm++)PM[PM_ARR[_pm].plan]=PM_ARR[_pm];
// รวม plan filled (PL) + plan ที่ไม่มีไม้ fill เลย (สร้างจาก UF) → เรียง plan_id ต่อเนื่อง (ไม่ข้าม)
var ufBy={};for(var _k=0;_k<UF.length;_k++){(ufBy[UF[_k].plan]=ufBy[UF[_k].plan]||[]).push(UF[_k]);}
var _fid={};for(var _k=0;_k<PL.length;_k++)_fid[PL[_k].plan_id]=1;
var ALLP=PL.slice();
for(var _pid in ufBy){if(!_fid[_pid]){var _us=ufBy[_pid],_pb=1e18;
 for(var _j=0;_j<_us.length;_j++)if(_us[_j].pb<_pb)_pb=_us[_j].pb;
 ALLP.push({plan_id:parseInt(_pid,10),direction:_us[0].dir,entry_time:'',eb:_pb,n_trades:0,n_unf:_us.length,unfilled_only:true});}}
ALLP.sort(function(a,b){return a.plan_id-b.plan_id;});
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const main=document.getElementById('main'), tip=document.getElementById('tip'), barEl=document.getElementById('bar');
let cnt=Math.min(300,N), i0=Math.max(0,N-cnt), W=0,Hgt=0, dpr=window.devicePixelRatio||1;
let cross=false, mx=-1, my=-1, rafP=false, dragMoved=false;
let focus=false, fN=55, fw=5.5, fh=7.5, fc=Math.floor(N/2);
let hoverPlan=null, hlTrade=-1;   // แผนที่ hover (พรีวิว) · ไม้ที่ highlight (-1=ไม่มี)
function reqDraw(){if(rafP)return;rafP=true;requestAnimationFrame(function(){rafP=false;draw();});}
function resize(){W=main.clientWidth;Hgt=main.clientHeight;cv.width=W*dpr;cv.height=Hgt*dpr;cv.style.width=W+'px';cv.style.height=Hgt+'px';ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
window.addEventListener('resize',resize);
function fmtT(s){var d=new Date(s*1000),p=function(n){return String(n).padStart(2,'0')};return d.getUTCFullYear()+'-'+p(d.getUTCMonth()+1)+'-'+p(d.getUTCDate())+' '+p(d.getUTCHours())+':'+p(d.getUTCMinutes());}
function planTrades(id){return TR.filter(function(t){return t.plan===id;});}
function circled(n){return (n>=1&&n<=9)?String.fromCharCode(9311+n):'('+n+')';}  // ①..⑨
function tagNum(t){var m=String(t.tag).match(/(\d)/);return m?parseInt(m[1]):0;}
// เส้น level (entry/SL/TP) ของแผนที่โชว์ — highlight ไม้ที่ hover (hl)
// free: พาดเต็มจอ (0..W) · focus: จากแท่ง entry → tag (ในกรอบ, ไม่ overflow)
function drawPlanLines(id,hl,S){var ft=planTrades(id),Y=S.Y,fr=S.fr;
 for(var pass=0;pass<2;pass++)for(var ti=0;ti<ft.length;ti++){var isHl=(hl===ti);
  if(hl>=0){if((pass===0)===isHl)continue;}else if(pass===1)continue;   // pass0=อื่น, pass1=ตัว hl ทับบน
  var t=ft[ti],aOn=(hl>=0&&!isHl)?0.22:0.9,ldA=(hl>=0&&!isHl)?0.12:0.32,buy=t.dir==='BUY',lw=isHl?2:1;
  var sx,ex,tagx;  // free: เต็มจอ · focus: ช่วงไม้ (entry→exit) + leader ไป tag ขอบขวา
  if(fr){var c=function(x){return Math.max(fr.x,Math.min(fr.x+fr.w,x));};sx=c(S.X(t.eb));ex=c(S.X(t.xb));tagx=fr.x+fr.w;}
  else{sx=S.X(t.eb);ex=S.X(t.xb);}   // free: เส้นสั้นช่วงไม้ (ไม่เต็มจอ · ไม่มี leader)
  var lv=[['239,83,80',t.sl],['38,166,154',t.tp],[buy?'66,165,245':'255,167,38',t.entry]];
  for(var L=0;L<3;L++){var y=Y(lv[L][1]);
   if(fr&&(y<fr.y+1||y>fr.y+fr.h-1))continue;   // นอกกรอบแนวตั้ง → ข้าม
   ctx.setLineDash([4,3]);ctx.lineWidth=lw;ctx.strokeStyle='rgba('+lv[L][0]+','+aOn+')';
   ctx.beginPath();ctx.moveTo(sx,y);ctx.lineTo(ex,y);ctx.stroke();
   if(fr){ctx.setLineDash([1,3]);ctx.lineWidth=1;ctx.strokeStyle='rgba('+lv[L][0]+','+ldA+')';  // leader บางจาง
    ctx.beginPath();ctx.moveTo(ex,y);ctx.lineTo(tagx,y);ctx.stroke();}}}
 // ออร์เดอร์ไม่ fill (ghost): เส้น entry(limit)/SL/TP ในช่วง place→death · เส้นประจาง
 var uf=UF.filter(function(u){return u.plan===id;}),gA=(hl>=0)?0.12:0.3;
 for(var ui=0;ui<uf.length;ui++){var u=uf[ui];if(u.price==null)continue;var ubuy=u.dir==='BUY',usx,uex,utagx;
  if(fr){var c2=function(x){return Math.max(fr.x,Math.min(fr.x+fr.w,x));};usx=c2(S.X(u.pb));uex=c2(S.X(u.db));utagx=fr.x+fr.w;}
  else{usx=S.X(u.pb);uex=S.X(u.db);}
  var ulv=[['239,83,80',u.sl],['38,166,154',u.tp],[ubuy?'66,165,245':'255,167,38',u.price]];
  for(var L=0;L<3;L++){if(ulv[L][1]==null)continue;var y=Y(ulv[L][1]);
   if(fr&&(y<fr.y+1||y>fr.y+fr.h-1))continue;
   ctx.setLineDash([2,3]);ctx.lineWidth=1;ctx.strokeStyle='rgba('+ulv[L][0]+','+gA+')';
   ctx.beginPath();ctx.moveTo(usx,y);ctx.lineTo(uex,y);ctx.stroke();
   if(fr){ctx.setLineDash([1,4]);ctx.strokeStyle='rgba('+ulv[L][0]+','+(gA*0.5)+')';ctx.beginPath();ctx.moveTo(uex,y);ctx.lineTo(utagx,y);ctx.stroke();}}}
 ctx.setLineDash([]);ctx.lineWidth=1;}
// tag ค่าราคา · free: ชิดขอบจอ · focus: ชิดขอบซ้าย "ในกรอบ" · รวมเลขไม้ระดับเดียวกัน (①②③) · เรืองแสงตอน hover
function drawPlanTags(id,hl,S){var ft=planTrades(id),Y=S.Y,fr=S.fr,groups={};
 var ymin=fr?fr.y+7:7, ymax=fr?fr.y+fr.h-3:Hgt-3;
 for(var ti=0;ti<ft.length;ti++){var t=ft[ti],num=tagNum(t),buy=t.dir==='BUY';
  var lx=fr?(fr.x+fr.w):S.X(t.xb);   // จุดเกาะ tag: focus=ขอบขวานอกกรอบ · free=ปลายเส้นสั้น (แท่ง exit)
  var lv=[[t.entry,buy?'#42a5f5':'#ffa726','e'],[t.sl,'#ef5350','s'],[t.tp,'#26a69a','p']];
  for(var L=0;L<3;L++){var price=lv[L][0],y=Y(price);
   if(fr&&(y<fr.y+1||y>fr.y+fr.h-1))continue;   // นอกกรอบ → ไม่โชว์ tag
   var key=lv[L][2]+price.toFixed(3);
   var g=groups[key]||(groups[key]={y:y,x:lx,c:lv[L][1],price:price.toFixed(3),nums:[],hl:false});
   if(lx>g.x)g.x=lx;   // free: ใช้ปลายขวาสุดของไม้ที่ระดับเดียวกัน · focus: เท่ากันทุกตัว
   if(g.nums.indexOf(num)<0)g.nums.push(num);if(hl===ti)g.hl=true;}}
 var uf=UF.filter(function(u){return u.plan===id;});   // tag ghost ของออร์เดอร์ไม่ fill
 for(var ui=0;ui<uf.length;ui++){var u=uf[ui],unum=tagNum(u),ubuy=u.dir==='BUY';
  var ux=fr?(fr.x+fr.w):S.X(u.db);
  var ulv=[[u.price,ubuy?'#42a5f5':'#ffa726','e'],[u.sl,'#ef5350','s'],[u.tp,'#26a69a','p']];
  for(var L=0;L<3;L++){var price=ulv[L][0];if(price==null)continue;var y=Y(price);
   if(fr&&(y<fr.y+1||y>fr.y+fr.h-1))continue;
   var key='u'+ulv[L][2]+price.toFixed(3);
   var g=groups[key]||(groups[key]={y:y,x:ux,c:ulv[L][1],price:price.toFixed(3),nums:[],hl:false,ghost:true});
   if(ux>g.x)g.x=ux;if(g.nums.indexOf(unum)<0)g.nums.push(unum);}}
 var arr=[];for(var key in groups)arr.push(groups[key]);
 arr.sort(function(a,b){return a.x-b.x||a.y-b.y;});
 for(var k=1;k<arr.length;k++){if(Math.abs(arr[k].x-arr[k-1].x)<60&&arr[k].y<arr[k-1].y+13)arr[k].y=arr[k-1].y+13;}  // x-aware de-overlap
 ctx.font='10px monospace';
 for(var k=0;k<arr.length;k++){var g=arr[k];g.nums.sort(function(a,b){return a-b;});
  var pfx=g.nums.map(circled).join(''),label=pfx+(g.nums.length>1?' ':'')+g.price;
  var dim=(hl>=0&&!g.hl),w=ctx.measureText(label).width+6,y=Math.max(ymin,Math.min(ymax,g.y));
  var tx=Math.max(0,Math.min(g.x+(fr?0:2),W-w));   // focus: ขอบขวา · free: ติดปลายเส้น (+2) · กันล้นจอ
  ctx.globalAlpha=g.ghost?(dim?0.3:0.6):(dim?0.35:1);   // ghost = โทนหรี่กว่าไม้ fill
  if(g.hl&&!g.ghost){ctx.shadowColor=g.c;ctx.shadowBlur=8;}
  ctx.fillStyle=g.c;ctx.fillRect(tx,y-7,w,13);ctx.shadowBlur=0;
  ctx.fillStyle='#0e0e12';ctx.fillText(label,tx+3,y+3);ctx.globalAlpha=1;}}
// === plan_meta overlay (focus mode เท่านั้น) ===
// [1] tint แท่งพ่อ (fs→fe) / แม่ (mb) — แถบพื้นจาง + ขอบบน-ล่าง · subtle ไม่กลบไส้เทียน
function drawPlanBars(id,S){var pm=PM[id];if(!pm)return;var fr=S.fr,cw=S.cw;
 function band(b0,b1,fill,edge){var lx=Math.max(fr.x,S.X(b0)-cw/2),rx=Math.min(fr.x+fr.w,S.X(b1)+cw/2);
  if(rx<=lx)return;ctx.fillStyle=fill;ctx.fillRect(lx,fr.y,rx-lx,fr.h);
  ctx.strokeStyle=edge;ctx.lineWidth=1;ctx.beginPath();
  ctx.moveTo(lx,fr.y+0.5);ctx.lineTo(rx,fr.y+0.5);ctx.moveTo(lx,fr.y+fr.h-0.5);ctx.lineTo(rx,fr.y+fr.h-0.5);ctx.stroke();}
 band(pm.fs,pm.fe,'rgba(110,150,220,0.10)','rgba(110,150,220,0.30)');   // พ่อ — น้ำเงินจาง
 band(pm.mb,pm.mb,'rgba(220,175,90,0.13)','rgba(220,175,90,0.40)');     // แม่ — ทอง/ส้มจาง (แยกสี)
 ctx.lineWidth=1;}
// ตัดข้อความให้พอดี maxw — เลือกตัดที่ช่องว่างก่อน, ไม่งั้นตัดราย char (รองรับไทย)
function wrapTxt(s,maxw){var out=[],cur='';
 for(var i=0;i<s.length;i++){var ch=s[i];
  if(cur&&ctx.measureText(cur+ch).width>maxw){var sp=cur.lastIndexOf(' ');
   if(sp>0){out.push(cur.slice(0,sp));cur=cur.slice(sp+1)+ch;}else{out.push(cur);cur=ch;}}
  else cur+=ch;}
 if(cur)out.push(cur);return out;}
// [2] panel พ่อ/แม่/เหตุผล — โซนมืดซ้ายของกรอบ focus (ซ้ายแคบ → แถบบนใต้ header)
function drawPlanPanel(id,S){var pm=PM[id];if(!pm)return;var fr=S.fr;
 var topZone=(fr.x-16)<150, maxw=topZone?(fr.w-20):(fr.x-20);if(maxw<80)return;
 var FNT=function(sz){return sz+'px -apple-system,Segoe UI,Roboto,sans-serif';};
 var blocks=[{t:'พ่อ: #'+pm.fs+' → #'+pm.fe+' · '+pm.fp+'%',c:'#9ec1ff',sz:12},
             {t:'แม่: #'+pm.mb+' · '+pm.mp+'%',c:'#e8c07a',sz:12},
             {t:'เหตุผล:',c:'#9aa0b0',sz:11}];
 for(var r=0;r<(pm.reasons||[]).length;r++)blocks.push({t:'• '+pm.reasons[r],c:'#cfd2dc',sz:11,bul:true});
 var lines=[];
 for(var b=0;b<blocks.length;b++){ctx.font=FNT(blocks[b].sz);var ind=blocks[b].bul?12:0,subs=wrapTxt(blocks[b].t,maxw-ind);
  for(var s=0;s<subs.length;s++)lines.push({t:subs[s],c:blocks[b].c,sz:blocks[b].sz,x:(s>0?ind:0)});}
 var lh=16,padX=8,padY=7,th=lines.length*lh,boxX,boxW,boxY;
 if(topZone){boxX=fr.x;boxW=fr.w;boxY=Math.max(2,fr.y-th-2*padY-2);}
 else{boxX=4;boxW=fr.x-8;boxY=fr.y;}
 ctx.fillStyle='rgba(14,15,20,0.82)';ctx.fillRect(boxX,boxY,boxW,th+2*padY);
 ctx.fillStyle='rgba(120,150,210,0.55)';ctx.fillRect(boxX,boxY,2,th+2*padY);   // accent ซ้าย
 var ty=boxY+padY+11;
 for(var k=0;k<lines.length;k++){ctx.font=FNT(lines[k].sz);ctx.fillStyle=lines[k].c;
  ctx.fillText(lines[k].t,boxX+padX+(lines[k].x||0),ty);ty+=lh;}}
function scale(){
 if(focus){
  var ratio=fw/fh, fH=Math.min(Hgt-20,Math.round(Hgt*0.82)), fW=Math.min(Math.round(W*0.95),Math.round(fH*ratio));
  var fx=Math.round((W-fW)/2), fy=Math.round((Hgt-fH)/2), i0f=fc-Math.floor(fN/2);
  var lo=1e18,hi=-1e18;for(var i=Math.max(0,i0f);i<Math.min(N,i0f+fN);i++){if(O.l[i]<lo)lo=O.l[i];if(O.h[i]>hi)hi=O.h[i];}
  if(hi<=lo){var c=Math.max(0,Math.min(N-1,fc));lo=O.l[c];hi=O.h[c];}
  var pad=(hi-lo)*0.04||1;lo-=pad;hi+=pad;var cw=fW/fN;  // focus: แท่งล้วน ~4% ชิดขอบ
  return {a:Math.max(0,Math.floor(i0f-fx/cw)),b:Math.min(N,Math.ceil(i0f+(W-fx)/cw)+1),cw:cw,lo:lo,hi:hi,top:fy,ph:fH,
   fr:{x:fx,y:fy,w:fW,h:fH},
   X:function(i){return fx+(i-i0f)*cw+cw/2;},Y:function(p){return fy+(hi-p)/(hi-lo)*fH;},
   barAt:function(x){return i0f+Math.floor((x-fx)/cw);},priceAt:function(y){return hi-(y-fy)/fH*(hi-lo);}};
 }
 var a=i0,b=Math.min(N,i0+cnt),n=b-a;if(n<1){n=1;b=a+1;}
 var lo=1e18,hi=-1e18;for(var i=a;i<b;i++){if(O.l[i]<lo)lo=O.l[i];if(O.h[i]>hi)hi=O.h[i];}
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.xb>=a&&t.eb<b){[t.sl,t.tp,t.entry,t.exit].forEach(function(v){if(v<lo)lo=v;if(v>hi)hi=v;});}}
 var pad=(hi-lo)*0.08||1;lo-=pad;hi+=pad;var cw=W/n,top=4,ph=Hgt-24;
 return {a:a,b:b,cw:cw,lo:lo,hi:hi,top:top,ph:ph,fr:null,
  X:function(i){return (i-a)*cw+cw/2;},Y:function(p){return top+(hi-p)/(hi-lo)*ph;},
  barAt:function(x){return a+Math.floor(x/cw);},priceAt:function(y){return hi-(y-top)/ph*(hi-lo);}};
}
function draw(){
 ctx.clearRect(0,0,W,Hgt);var S=scale();if(S.b-S.a<=0||S.hi<=S.lo)return;
 var a=S.a,b=S.b,cw=S.cw,lo=S.lo,hi=S.hi,X=S.X,Y=S.Y;
 var sp=(hoverPlan!==null?hoverPlan:openId), hl=(sp===openId?hlTrade:-1);  // แผนที่โชว์ + ไม้ที่ highlight
 ctx.lineWidth=1;ctx.font='11px monospace';
 for(var k=0;k<=4;k++){var p=lo+(hi-lo)*k/4,y=Y(p);ctx.strokeStyle='#1c1e26';ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();ctx.fillStyle='#5a5f70';ctx.fillText(p.toFixed(2),4,y-2);}
 if(S.fr&&sp!==null)drawPlanBars(sp,S);              // tint แท่งพ่อ/แม่ (หลัง grid, ก่อนเทียน → ไม่กลบไส้)
 var bw=Math.max(1,cw*0.7);
 for(var i=a;i<b;i++){var x=X(i),up=O.c[i]>=O.o[i];ctx.strokeStyle=up?'#26a69a':'#ef5350';ctx.fillStyle=ctx.strokeStyle;
  ctx.beginPath();ctx.moveTo(x,Y(O.h[i]));ctx.lineTo(x,Y(O.l[i]));ctx.stroke();
  var y1=Y(O.o[i]),y2=Y(O.c[i]);ctx.fillRect(x-bw/2,Math.min(y1,y2),bw,Math.max(1,Math.abs(y2-y1)));}
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.xb<a||t.eb>=b)continue;var win=t.result==='TP',buy=t.dir==='BUY';
  var xb=X(t.eb),ye=Y(t.entry);ctx.fillStyle=buy?'#42a5f5':'#ffa726';
  ctx.beginPath();if(buy){ctx.moveTo(xb,ye+9);ctx.lineTo(xb-5,ye+18);ctx.lineTo(xb+5,ye+18);}else{ctx.moveTo(xb,ye-9);ctx.lineTo(xb-5,ye-18);ctx.lineTo(xb+5,ye-18);}ctx.closePath();ctx.fill();
  ctx.fillStyle=win?'#26a69a':'#ef5350';ctx.fillRect(X(t.xb)-3,Y(t.exit)-3,6,6);}
 for(var k=0;k<UF.length;k++){var u=UF[k];if(u.price==null||u.db<a||u.pb>=b)continue;
  var x1=X(Math.max(a,u.pb)),x2=X(Math.min(b-1,u.db)),y=Y(u.price);
  ctx.setLineDash([2,3]);ctx.strokeStyle='rgba(150,154,170,.5)';ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2,y);ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle='rgba(150,154,170,.85)';var xp=X(u.pb);ctx.beginPath();ctx.moveTo(xp,y-4);ctx.lineTo(xp+4,y);ctx.lineTo(xp,y+4);ctx.lineTo(xp-4,y);ctx.closePath();ctx.stroke();
  var xd=X(u.db);ctx.beginPath();ctx.moveTo(xd-3,y-3);ctx.lineTo(xd+3,y+3);ctx.moveTo(xd+3,y-3);ctx.lineTo(xd-3,y+3);ctx.stroke();}
 if(sp!==null)drawPlanLines(sp,hl,S);                 // เส้น level เฉพาะแผนที่ hover/เลือก (ก่อน overlay)
 if(S.fr){var fr=S.fr;ctx.fillStyle='rgba(8,8,12,.6)';
  ctx.fillRect(0,0,W,fr.y);ctx.fillRect(0,fr.y+fr.h,W,Hgt-fr.y-fr.h);
  ctx.fillRect(0,fr.y,fr.x,fr.h);ctx.fillRect(fr.x+fr.w,fr.y,W-fr.x-fr.w,fr.h);
  ctx.strokeStyle='rgba(200,204,220,.65)';ctx.lineWidth=1;ctx.strokeRect(fr.x+0.5,fr.y+0.5,fr.w-1,fr.h-1);}
 if(sp!==null)drawPlanTags(sp,hl,S);                  // tag (หลัง overlay — อ่านได้แม้ focus)
 if(S.fr&&sp!==null)drawPlanPanel(sp,S);              // panel พ่อ/แม่/เหตุผล (โซนมืดซ้าย · บนสุด)
 if(cross&&mx>=0&&mx<=W&&my>=0&&my<=Hgt){
  ctx.setLineDash([3,3]);ctx.lineWidth=1;ctx.strokeStyle='rgba(180,184,200,.55)';
  ctx.beginPath();ctx.moveTo(mx,0);ctx.lineTo(mx,Hgt);ctx.stroke();
  ctx.beginPath();ctx.moveTo(0,my);ctx.lineTo(W,my);ctx.stroke();ctx.setLineDash([]);
  ctx.font='11px monospace';
  var pr=S.priceAt(my).toFixed(2),pw=ctx.measureText(pr).width+8;
  ctx.fillStyle='#3a3f4e';ctx.fillRect(W-pw-1,my-8,pw,16);ctx.fillStyle='#e8eaf0';ctx.fillText(pr,W-pw+3,my+3);
  var bi=Math.max(0,Math.min(N-1,S.barAt(mx))),tt=fmtT(O.t[bi])+'  #'+bi,tw=ctx.measureText(tt).width+8,tx=Math.min(Math.max(mx-tw/2,0),W-tw);
  ctx.fillStyle='#3a3f4e';ctx.fillRect(tx,Hgt-16,tw,16);ctx.fillStyle='#e8eaf0';ctx.fillText(tt,tx+4,Hgt-4);
 }
 barEl.textContent=fmtT(O.t[Math.max(0,a)])+'  →  '+fmtT(O.t[Math.min(N-1,b-1)])+'   ('+(focus?('focus '+fN+' แท่ง @#'+fc):((b-a)+' / '+N+' แท่ง'))+')';
}
var drag=null;
cv.addEventListener('mousedown',function(e){drag={x:e.clientX,i0:i0,fc:fc};dragMoved=false;});
window.addEventListener('mouseup',function(){drag=null;});
window.addEventListener('mousemove',function(e){
 var r=cv.getBoundingClientRect();mx=e.clientX-r.left;my=e.clientY-r.top;
 if(drag){var di=Math.round((e.clientX-drag.x)/scale().cw);if(di!==0)dragMoved=true;
  if(focus){fc=Math.max(0,Math.min(N-1,drag.fc-di));}else{i0=Math.max(0,Math.min(N-cnt,drag.i0-di));}draw();return;}
 if(mx<0||mx>W||my<0||my>Hgt){tip.style.display='none';if(cross)reqDraw();return;}
 if(cross)reqDraw();
 var i=scale().barAt(mx);if(i<0||i>=N){tip.style.display='none';return;}
 var s='แท่ง '+i+'  '+fmtT(O.t[i])+'\nO '+O.o[i]+'  H '+O.h[i]+'  L '+O.l[i]+'  C '+O.c[i];
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.eb===i||t.xb===i){s+='\n— #'+t.plan+' '+t.dir+' '+t.tag+'\n  entry '+t.entry+'  '+t.result+'@'+t.exit+'\n  SL '+t.sl+'  TP '+t.tp+'  pnl '+t.pnl+'pip';}}
 for(var k=0;k<UF.length;k++){var u=UF[k];if(u.pb===i||u.db===i){s+='\n— #'+u.plan+' '+u.dir+' '+u.tag+' (ไม่ fill)\n  limit '+u.price+'  '+u.reason;}}
 tip.textContent=s;tip.style.display='block';tip.style.left=Math.min(mx+14,W-180)+'px';tip.style.top=(my+14)+'px';
});
cv.addEventListener('mouseleave',function(){tip.style.display='none';mx=-1;my=-1;if(cross)reqDraw();});
document.getElementById('xcb').addEventListener('change',function(e){cross=e.target.checked;draw();});
document.getElementById('fcb').addEventListener('change',function(e){focus=e.target.checked;
 document.getElementById('fopt').style.display=focus?'inline':'none';
 if(focus){fc=Math.max(0,Math.min(N-1,Math.round(i0+cnt/2)));}else{i0=Math.max(0,Math.min(N-cnt,fc-Math.floor(cnt/2)));}draw();});
document.getElementById('iN').addEventListener('input',function(e){var v=parseInt(e.target.value);if(v>=5){fN=v;draw();}});
document.getElementById('iw').addEventListener('input',function(e){var v=parseFloat(e.target.value);if(v>0){fw=v;draw();}});
document.getElementById('ih').addEventListener('input',function(e){var v=parseFloat(e.target.value);if(v>0){fh=v;draw();}});
cv.addEventListener('wheel',function(e){e.preventDefault();
 if(focus){fc=Math.max(0,Math.min(N-1,fc+(e.deltaY>0?1:-1)*Math.max(1,Math.round(fN/5))));draw();return;}
 var r=cv.getBoundingClientRect(),mx2=e.clientX-r.left,piv=i0+mx2/(W/cnt);
 cnt=Math.max(20,Math.min(N,Math.round(cnt*(e.deltaY>0?1.18:0.85))));i0=Math.max(0,Math.min(N-cnt,Math.round(piv-mx2/(W/cnt))));draw();},{passive:false});
function jump(eb){
 if(focus){fc=Math.max(0,Math.min(N-1,eb));}                       // focus: center กรอบที่แท่งนั้น
 else{if(cnt>400)cnt=200;i0=Math.max(0,Math.min(N-cnt,eb-Math.floor(cnt/2)));}  // free: center view
 draw();}
function esc(v){return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function hm(s){return fmtT(s).slice(11);}   // 'HH:MM'
function planById(id){for(var k=0;k<ALLP.length;k++)if(ALLP[k].plan_id===id)return ALLP[k];return null;}
var openId=null, curQ='';
function detailHTML(id){
 var ft=TR.filter(function(t){return t.plan===id;}), uf=UF.filter(function(u){return u.plan===id;}), h='';
 for(var k=0;k<ft.length;k++){var t=ft[k],win=t.result==='TP',col=win?'#26a69a':'#ef5350';
  var po=(t.pnlusd>=0?'+':'')+t.pnlusd.toFixed(2), pp=(t.pnl>=0?'+':'')+t.pnl;
  h+='<div class="dl"><div><b>'+esc(t.tag)+'</b> '+t.kind+' · <span style="color:'+col+'">'+(win?'WIN':'LOSS')+' '+po+'$ ('+pp+'p)</span></div>'+
     '<div class="mut">entry '+t.entry+' · SL '+t.sl+' · TP '+t.tp+' · lot '+t.lot+' · exit '+t.result+' @'+hm(O.t[t.xb])+'</div></div>';}
 if(uf.length){h+='<div class="dsub">ไม้ไม่ fill ('+uf.length+')</div>';
  for(var k=0;k<uf.length;k++){var u=uf[k];
   h+='<div class="du"><b>'+esc(u.tag)+'</b> · limit '+u.price+' · SL '+u.sl+' · TP '+u.tp+' · '+u.reason+' @'+hm(O.t[u.db])+'</div>';}}
 return h;
}
function setOpen(id,doJump){
 openId=id;renderList(curQ);
 var p=planById(id);if(doJump&&p)jump(p.eb);
 var el=document.getElementById('row'+id);if(el)el.scrollIntoView({block:'nearest'});
}
cv.addEventListener('click',function(e){
 if(dragMoved){dragMoved=false;return;}
 var r=cv.getBoundingClientRect(),cx=e.clientX-r.left,cy=e.clientY-r.top;
 var S=scale(),a=S.a,b=S.b,X=S.X,Y=S.Y;if(b-a<=0)return;
 var best=null,bd=16;
 for(var k=0;k<TR.length;k++){var t=TR[k];if(t.xb<a||t.eb>=b)continue;
  var pts=[[t.eb,t.entry],[t.xb,t.exit]];
  for(var j=0;j<2;j++){var d=Math.hypot(cx-X(pts[j][0]),cy-Y(pts[j][1]));if(d<bd){bd=d;best=t;}}}
 if(best){setOpen(best.plan,false);}                         // คลิก marker → เลือกแผน (sticky)
 else if(openId!==null){openId=null;hlTrade=-1;renderList(curQ);draw();}  // คลิกที่ว่าง → ยกเลิก/ยุบ/เคลียร์เส้น
});
window.addEventListener('keydown',function(e){if(e.key==='Escape'&&openId!==null){openId=null;hlTrade=-1;renderList(curQ);draw();}});
var listEl=document.getElementById('list');
function renderList(q){curQ=q||'';var Q=curQ.toUpperCase();listEl.innerHTML='';
 for(var k=0;k<ALLP.length;k++){var p=ALLP[k];
  var et=p.unfilled_only?fmtT(O.t[p.eb]):p.entry_time;
  var line=(et+' '+p.direction+' '+(p.unfilled_only?'UNFILL ไม่ fill':p.result)).toUpperCase();if(Q&&line.indexOf(Q)<0)continue;
  var d=document.createElement('div');d.id='row'+p.plan_id;
  if(p.unfilled_only){
   d.className='row unf'+(p.plan_id===openId?' open':'');
   d.innerHTML='<div>#'+p.plan_id+' '+p.direction+'  <span class="nf">ไม่ fill</span></div><div class="t">'+et+' · '+p.n_unf+' ออร์เดอร์ · —</div>';
  }else{
   d.className='row '+(p.result==='WIN'?'win':'loss')+(p.plan_id===openId?' open':'');
   var col=p.result==='WIN'?'#26a69a':'#ef5350';
   d.innerHTML='<div>#'+p.plan_id+' '+p.direction+'  <b style="color:'+col+'">'+p.net_pnl_usd.toFixed(2)+'$</b></div><div class="t">'+p.entry_time+' · '+p.n_trades+' ไม้ · '+p.result+'</div>';
  }
  (function(id){
   d.onclick=function(){if(openId===id){openId=null;hlTrade=-1;renderList(curQ);draw();}else setOpen(id,true);};
   d.onmouseenter=function(){hoverPlan=id;hlTrade=-1;reqDraw();};   // hover แถวแผน → พรีวิวเส้น
   d.onmouseleave=function(){hoverPlan=null;reqDraw();};
  })(p.plan_id);
  listEl.appendChild(d);
  if(p.plan_id===openId){var dd=document.createElement('div');dd.className='detail';dd.innerHTML=detailHTML(p.plan_id);listEl.appendChild(dd);
   var dls=dd.querySelectorAll('.dl');                              // hover แถวไม้ → highlight เส้น/tag ไม้นั้น
   for(var di=0;di<dls.length;di++){(function(idx){dls[idx].onmouseenter=function(){hlTrade=idx;reqDraw();};dls[idx].onmouseleave=function(){hlTrade=-1;reqDraw();};})(di);}}
 }}
document.getElementById('search').addEventListener('input',function(e){renderList(e.target.value);});
var wins=TR.filter(function(t){return t.result==='TP';}).length;
document.getElementById('stat').textContent=TR.length+' ไม้ · WR '+(TR.length?(100*wins/TR.length).toFixed(1):0)+'% · '+PL.length+' จุดเข้า · '+UF.length+' ไม่ fill';
renderList('');resize();
</script></body></html>
"""
