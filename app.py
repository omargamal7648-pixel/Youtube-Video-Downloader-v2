#!/usr/bin/env python3
"""
YouTube Downloader Pro — تصميم وتطوير: عمر جمال
─────────────────────────────────────────────────
Flow: yt-dlp → /tmp/ytdl/<id>/ → stream to browser → auto-delete
"""
from flask import Flask, request, jsonify, Response
import yt_dlp, threading, os, uuid, ssl, time, glob, shutil
from urllib.parse import quote

app = Flask(__name__)

# /tmp is writable on every platform (Railway, Render, local)
TMP_DIR = "/tmp/ytdl"
os.makedirs(TMP_DIR, exist_ok=True)

downloads = {}   # id → state

# ── SSL patch ─────────────────────────────────────────────────────────────────
try:
    _ctx = ssl.create_default_context()
    _ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    _ctx.check_hostname = False
    _ctx.verify_mode    = ssl.CERT_NONE
except Exception:
    pass

# ── yt-dlp base config ────────────────────────────────────────────────────────
YDL_BASE = {
    "quiet":            True,
    "no_warnings":      True,
    # ── FIX: HTTP/2 + ISP middleboxes = SSL DECRYPTION_FAILED ────────────────
    "http2":                         False,   # force HTTP/1.1 — THE real fix
    "nocheckcertificate":            True,
    "legacyserverconnect":           True,
    "prefer_insecure":               True,
    # ── retries ──────────────────────────────────────────────────────────────
    "retries":                       15,
    "fragment_retries":              15,
    "extractor_retries":             5,
    "file_access_retries":           5,
    "sleep_interval_requests":       0.5,
    # ── speed: parallel fragments ─────────────────────────────────────────────
    "concurrent_fragment_downloads": 16,
    "buffersize":                    1024 * 32,
    "http_chunk_size":               10 * 1024 * 1024,
    "socket_timeout":                60,
    # ── bypass some YouTube restrictions ─────────────────────────────────────
    "extractor_args": {
        "youtube": {"player_client": ["android", "web"]}
    },
}

# ── auto-cleanup: delete /tmp folders older than 30 min ──────────────────────
def _cleanup_loop():
    while True:
        time.sleep(300)
        try:
            now = time.time()
            for d in glob.glob(f"{TMP_DIR}/*"):
                if os.path.isdir(d) and now - os.path.getmtime(d) > 1800:
                    shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

threading.Thread(target=_cleanup_loop, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# UI — single HTML file, no static folder, works on any host
# ══════════════════════════════════════════════════════════════════════════════
TEMPLATE = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YouTube Downloader Pro</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;900&display=swap" rel="stylesheet">
<style>
:root{--bg:#07070E;--panel:#0F0F1C;--card:#161625;--card2:#1E1E33;--accent:#FF3D3D;--a2:#FF6B35;--gold:#FFB800;--green:#00E676;--blue:#448AFF;--muted:#5A5A7A;--bdr:#252540;--text:#EEEEFF;--fnt:'Cairo',sans-serif}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--fnt);direction:rtl}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:var(--panel)}::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:3px}
.app{display:flex;height:100vh;overflow:hidden}
.sb{width:235px;min-width:235px;background:var(--panel);border-left:1px solid var(--bdr);display:flex;flex-direction:column;padding-bottom:16px;position:relative;overflow:hidden}
.sb::before{content:'';position:absolute;top:-80px;right:-80px;width:220px;height:220px;background:radial-gradient(circle,rgba(255,61,61,.12),transparent 70%);pointer-events:none}
.brand{padding:20px 18px 16px;border-bottom:1px solid var(--bdr);margin-bottom:10px}
.brand-icon{font-size:26px;display:block;margin-bottom:4px}
.brand-name{font-size:16px;font-weight:900;background:linear-gradient(135deg,var(--accent),var(--a2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.brand-sub{font-size:10px;color:var(--muted);margin-top:2px}
.nl{font-size:10px;color:var(--muted);padding:4px 16px 6px;letter-spacing:1px}
.nb{display:flex;align-items:center;gap:10px;padding:11px 14px;margin:2px 8px;border-radius:10px;cursor:pointer;transition:all .2s;font-size:13px;font-family:var(--fnt);font-weight:600;background:transparent;border:none;color:var(--muted);text-align:right;width:calc(100% - 16px)}
.nb .ic{font-size:17px;min-width:26px}.nb:hover{background:var(--card2);color:var(--text)}
.nb.active{background:linear-gradient(135deg,rgba(255,61,61,.18),rgba(255,107,53,.08));color:var(--text)}
.sb-bot{margin-top:auto;padding:12px 14px 0}
.sig{text-align:center;font-size:11px;color:var(--muted);padding:10px 0}
.content{flex:1;overflow-y:auto;padding:26px 28px}
.page{display:none;animation:fi .3s ease}.page.active{display:block}
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.ph{margin-bottom:22px}.pt{font-size:24px;font-weight:900}.ps{font-size:13px;color:var(--muted);margin-top:4px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:18px;margin-bottom:14px}
.ct{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:10px}
.ir{display:flex;gap:8px;align-items:stretch}
.inp{flex:1;padding:11px 14px;background:var(--panel);border:1px solid var(--bdr);border-radius:9px;font-family:var(--fnt);font-size:13px;color:var(--text);transition:border-color .2s;direction:ltr;text-align:left}
.inp:focus{outline:none;border-color:var(--accent)}.inp::placeholder{color:var(--muted)}
.btn{padding:11px 18px;border-radius:9px;border:none;font-family:var(--fnt);font-size:13px;font-weight:700;cursor:pointer;transition:all .2s;white-space:nowrap;display:inline-flex;align-items:center;gap:5px;text-decoration:none}
.br{background:var(--accent);color:#fff}.br:hover{background:#CC3030;transform:translateY(-1px)}
.bo{background:var(--a2);color:#fff}.bo:hover{background:#CC5520;transform:translateY(-1px)}
.bg_{background:var(--gold);color:#000}.bg_:hover{background:#CC9400;transform:translateY(-1px)}
.bgreen{background:var(--green);color:#000;font-weight:900}.bgreen:hover{background:#00B85E}
.bgh{background:var(--card2);border:1px solid var(--bdr);color:var(--text)}.bgh:hover{border-color:var(--muted)}
.bsm{padding:7px 12px;font-size:12px;border-radius:8px}
.bfull{width:100%;justify-content:center;margin-top:10px;padding:13px;font-size:14px}
.or{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
.ol{font-size:12px;color:var(--muted);font-weight:600}
select{padding:8px 12px;background:var(--panel);border:1px solid var(--bdr);border-radius:8px;font-family:var(--fnt);font-size:12px;color:var(--text);cursor:pointer}
select:focus{outline:none;border-color:var(--accent)}
.ib{background:var(--panel);border:1px solid var(--bdr);border-radius:9px;padding:12px 14px;font-size:13px;line-height:1.7;margin-top:10px;min-height:50px;display:flex;align-items:center;color:var(--muted)}
.ib.ok{color:var(--text);border-color:var(--green)}
.pw{margin-top:10px}.po{height:7px;background:var(--panel);border-radius:4px;overflow:hidden}
.pi{height:100%;width:0%;border-radius:4px;background:linear-gradient(90deg,var(--accent),var(--a2));transition:width .4s;position:relative;overflow:hidden}
.pi::after{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);animation:sh 1.5s infinite}
@keyframes sh{to{left:100%}}
.ptxt{font-size:11px;color:var(--muted);margin-top:5px;text-align:center}
.segs{display:flex;background:var(--panel);border-radius:9px;padding:3px;gap:2px}
.seg{flex:1;padding:8px;border-radius:7px;border:none;text-align:center;font-family:var(--fnt);font-size:12px;font-weight:700;cursor:pointer;transition:all .2s;color:var(--muted);background:transparent}
.seg.active{background:var(--accent);color:#fff}
.plc{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.pll{max-height:360px;overflow-y:auto;border-radius:9px}
.pli{display:flex;align-items:center;gap:10px;padding:9px 12px;border-bottom:1px solid var(--bdr);transition:background .15s}
.pli:last-child{border-bottom:none}.pli:hover{background:rgba(255,255,255,.03)}
.pli input[type=checkbox]{width:16px;height:16px;accent-color:var(--a2);cursor:pointer;flex-shrink:0}
.pn{font-size:11px;color:var(--muted);min-width:26px;text-align:left}
.ptit{flex:1;font-size:12px;font-weight:600;direction:rtl;text-align:right}
.pdur{font-size:11px;color:var(--muted);min-width:46px;text-align:center;direction:ltr}
.pe{text-align:center;padding:36px;color:var(--muted);font-size:13px}
.bi{display:flex;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid var(--bdr);direction:ltr}
.bi:last-child{border-bottom:none}
.bu{flex:1;font-size:11px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bidx{font-size:11px;color:var(--muted);min-width:22px}
.brm{background:none;border:none;color:var(--accent);cursor:pointer;font-size:15px;padding:0 3px;transition:transform .2s}.brm:hover{transform:scale(1.3)}
.qi{background:var(--card2);border:1px solid var(--bdr);border-radius:11px;padding:12px 14px;margin-bottom:7px;transition:all .3s}
.qi.done{background:rgba(0,230,118,.05);border-color:rgba(0,230,118,.25)}
.qi.err{background:rgba(255,61,61,.05);border-color:rgba(255,61,61,.25)}
.qt{display:flex;align-items:flex-start;gap:9px;margin-bottom:8px}
.qico{font-size:17px;flex-shrink:0}
.qtit{flex:1;font-size:12px;font-weight:600;direction:rtl;text-align:right}
.qspd{font-size:10px;color:var(--muted);margin-top:3px;direction:ltr;text-align:right}
.qp{height:4px;background:var(--panel);border-radius:3px;overflow:hidden;margin-bottom:8px}
.qpi{height:100%;width:0%;border-radius:3px;background:linear-gradient(90deg,var(--green),var(--blue));transition:width .4s}
.qpi.err{background:var(--accent)}
.qbot{display:flex;align-items:center;justify-content:space-between}
.qst{font-size:11px;color:var(--muted)}
.qst.done{color:var(--green)}.qst.err{color:var(--accent)}
.sbar{display:flex;gap:14px;padding:12px 18px;background:var(--panel);border-radius:11px;margin-bottom:14px;border:1px solid var(--bdr);align-items:center}
.si{text-align:center}.sv{font-size:20px;font-weight:900}
.sv.r{color:var(--accent)}.sv.o{color:var(--a2)}.sv.g{color:var(--green)}.sv.go{color:var(--gold)}
.sl{font-size:10px;color:var(--muted)}.sdiv{width:1px;height:28px;background:var(--bdr)}
.bdg{display:inline-flex;align-items:center;justify-content:center;background:var(--accent);color:#fff;border-radius:20px;font-size:10px;font-weight:700;padding:2px 7px;margin-right:4px}
.bdg.g{background:var(--gold);color:#000}
.div{height:1px;background:var(--bdr);margin:10px 0}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(16px);background:var(--card2);border:1px solid var(--bdr);border-radius:9px;padding:10px 22px;font-size:13px;font-weight:600;opacity:0;transition:all .3s;z-index:999;pointer-events:none}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.s{border-color:var(--green);color:var(--green)}.toast.e{border-color:var(--accent);color:var(--accent)}
.spin{display:inline-block;animation:sp .8s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}
.dl-btn{padding:6px 16px;border-radius:8px;font-size:12px;font-weight:900;font-family:var(--fnt);background:var(--green);color:#000;border:none;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px}
.dl-btn:hover{background:#00C864}
</style>
</head>
<body>
<div class="app">
<aside class="sb">
  <div class="brand">
    <span class="brand-icon">▶️</span>
    <div class="brand-name">YT Downloader Pro</div>
    <div class="brand-sub">محمّل يوتيوب الاحترافي</div>
  </div>
  <div class="nl">التنقل</div>
  <button class="nb active" onclick="gp('single',this)"><span class="ic">🎬</span>فيديو واحد</button>
  <button class="nb" onclick="gp('playlist',this)"><span class="ic">📋</span>قائمة تشغيل</button>
  <button class="nb" onclick="gp('batch',this)"><span class="ic">⚡</span>تحميل مجمّع</button>
  <button class="nb" onclick="gp('queue',this)">
    <span class="ic">📥</span>الطابور
    <span class="bdg" id="qBdg" style="display:none;margin-right:auto">0</span>
  </button>
  <div class="sb-bot">
    <div class="div"></div>
    <div class="sig">تصميم وتطوير: عمر جمال</div>
  </div>
</aside>

<main class="content">

<!-- ── Single ─────────────────────────────────────────────────────────────── -->
<div class="page active" id="page-single">
  <div class="ph">
    <div class="pt">🎬 تحميل فيديو واحد</div>
    <div class="ps">السيرفر يحمّل الفيديو، بعدين انت تحمّله على جهازك مباشرة</div>
  </div>
  <div class="card">
    <div class="ct">رابط الفيديو</div>
    <div class="ir">
      <input class="inp" id="sUrl" type="text" placeholder="https://youtube.com/watch?v=..." onkeydown="if(event.key==='Enter')fsi()">
      <button class="btn br" onclick="fsi()">🔍 جلب المعلومات</button>
    </div>
    <div class="ib" id="sInfo">أدخل الرابط واضغط "جلب المعلومات"</div>
  </div>
  <div class="card">
    <div class="ct">خيارات التحميل</div>
    <div class="or">
      <span class="ol">الصيغة:</span>
      <div class="segs" style="flex:1;max-width:290px">
        <button class="seg active" onclick="setFmt('mp4',this)">MP4 فيديو</button>
        <button class="seg" onclick="setFmt('mp3',this)">MP3 صوت</button>
        <button class="seg" onclick="setFmt('webm',this)">WEBM</button>
      </div>
      <span class="ol">الجودة:</span>
      <select id="sQ">
        <option value="best" selected>أعلى جودة</option>
        <option value="2160">4K</option>
        <option value="1440">1440p</option>
        <option value="1080">1080p</option>
        <option value="720">720p</option>
        <option value="480">480p</option>
        <option value="360">360p</option>
        <option value="240">240p</option>
        <option value="144">144p</option>
      </select>
    </div>
    <div class="pw" id="sPW" style="display:none">
      <div class="po"><div class="pi" id="sBar"></div></div>
      <div class="ptxt" id="sTxt"></div>
    </div>
    <div id="sDlWrap" style="display:none;text-align:center;margin-top:12px"></div>
    <button class="btn br bfull" onclick="dlSingle()">⬇ تحميل الفيديو</button>
  </div>
</div>

<!-- ── Playlist ───────────────────────────────────────────────────────────── -->
<div class="page" id="page-playlist">
  <div class="ph">
    <div class="pt">📋 تحميل قائمة تشغيل</div>
    <div class="ps">اختار الفيديوهات اللي عايزها وحمّلها واحدة واحدة</div>
  </div>
  <div class="card">
    <div class="ct">رابط القائمة</div>
    <div class="ir">
      <input class="inp" id="plUrl" type="text" placeholder="https://youtube.com/playlist?list=...">
      <button class="btn bo" onclick="fpl()">📋 جلب القائمة</button>
    </div>
    <div class="or" style="margin-top:12px">
      <span class="ol">الجودة:</span>
      <select id="plQ">
        <option value="best" selected>أعلى جودة</option>
        <option value="1080">1080p</option>
        <option value="720">720p</option>
        <option value="480">480p</option>
        <option value="360">360p</option>
        <option value="mp3">صوت فقط MP3</option>
      </select>
    </div>
  </div>
  <div class="card">
    <div class="plc">
      <button class="btn bgh bsm" onclick="sAll(true)">☑ تحديد الكل</button>
      <button class="btn bgh bsm" onclick="sAll(false)">☐ إلغاء الكل</button>
      <span id="plCnt" style="font-size:12px;color:var(--muted);margin-right:auto"></span>
    </div>
    <div class="pll" id="plList">
      <div class="pe">أدخل رابط القائمة واضغط "جلب القائمة"</div>
    </div>
    <div class="pw" id="plPW" style="display:none">
      <div class="po"><div class="pi" id="plBar" style="background:linear-gradient(90deg,var(--a2),var(--gold))"></div></div>
      <div class="ptxt" id="plTxt"></div>
    </div>
    <button class="btn bo bfull" onclick="dlPl()">⬇ تحميل المحدد</button>
  </div>
</div>

<!-- ── Batch ──────────────────────────────────────────────────────────────── -->
<div class="page" id="page-batch">
  <div class="ph">
    <div class="pt">⚡ تحميل مجمّع</div>
    <div class="ps">أضف روابط متفرقة وحمّلها كلها دفعة واحدة</div>
  </div>
  <div class="card">
    <div class="ct">أضف رابطاً</div>
    <div class="ir">
      <input class="inp" id="btEnt" type="text" placeholder="https://youtube.com/watch?v=..." onkeydown="if(event.key==='Enter')addBt()">
      <button class="btn bg_" onclick="addBt()">+ إضافة</button>
    </div>
    <div class="or" style="margin-top:12px">
      <span class="ol">الجودة:</span>
      <select id="btQ">
        <option value="best" selected>أعلى جودة</option>
        <option value="1080">1080p</option>
        <option value="720">720p</option>
        <option value="480">480p</option>
        <option value="360">360p</option>
        <option value="mp3">صوت فقط MP3</option>
      </select>
      <button class="btn bgh bsm" style="margin-right:auto;color:var(--accent)" onclick="clrBt()">🗑 مسح الكل</button>
      <span class="bdg g" id="btCnt">0</span>
    </div>
  </div>
  <div class="card">
    <div class="ct">قائمة الروابط</div>
    <div id="btList"><div class="pe">لم تُضف أي روابط بعد</div></div>
    <div class="pw" id="btPW" style="display:none">
      <div class="po"><div class="pi" id="btBar" style="background:linear-gradient(90deg,var(--gold),var(--a2))"></div></div>
      <div class="ptxt" id="btTxt"></div>
    </div>
    <button class="btn bg_ bfull" onclick="dlBt()">⬇ تحميل الكل</button>
  </div>
</div>

<!-- ── Queue ──────────────────────────────────────────────────────────────── -->
<div class="page" id="page-queue">
  <div class="ph">
    <div class="pt">📥 طابور التحميل</div>
    <div class="ps">لما أي فيديو يخلص، هيظهر زر تحميل جنبه مباشرة</div>
  </div>
  <div class="sbar">
    <div class="si"><div class="sv r" id="stT">0</div><div class="sl">المجموع</div></div>
    <div class="sdiv"></div>
    <div class="si"><div class="sv o" id="stA">0</div><div class="sl">نشط</div></div>
    <div class="sdiv"></div>
    <div class="si"><div class="sv g" id="stD">0</div><div class="sl">مكتمل</div></div>
    <div class="sdiv"></div>
    <div class="si"><div class="sv go" id="stE">0</div><div class="sl">خطأ</div></div>
    <div style="margin-right:auto">
      <button class="btn bgh bsm" onclick="clrDone()">🧹 مسح المكتملة</button>
    </div>
  </div>
  <div id="qList">
    <div class="pe" id="qEmp">الطابور فارغ — ابدأ التحميل من أحد التبويبات</div>
  </div>
</div>

</main>
</div>
<div class="toast" id="toast"></div>

<script>
/* ── State ── */
var bUrls=[], plEnt=[], selFmt='mp4', qItems={};

/* ── Nav ── */
function gp(n,b){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active')});
  document.querySelectorAll('.nb').forEach(function(x){x.classList.remove('active')});
  document.getElementById('page-'+n).classList.add('active');
  if(b) b.classList.add('active');
}
function gpN(n){
  gp(n, document.querySelector('.nb[onclick*="\''+n+'\'"]'));
}
function setFmt(f,b){
  selFmt=f;
  document.querySelectorAll('.seg').forEach(function(s){s.classList.remove('active')});
  b.classList.add('active');
}

/* ── Toast ── */
function toast(m,t){
  var e=document.getElementById('toast');
  e.textContent=m; e.className='toast '+(t||'s')+' show';
  setTimeout(function(){e.classList.remove('show')},3200);
}

/* ── Utils ── */
function fmtD(s){
  if(!s) return '—';
  s=parseInt(s);
  var h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
  if(h) return h+':'+String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0');
  return m+':'+String(sec).padStart(2,'0');
}
function esc(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function post(url, body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json()});
}

/* ── Fetch info ── */
async function fsi(){
  var url=document.getElementById('sUrl').value.trim();
  if(!url){toast('أدخل الرابط أولاً','e');return}
  var b=document.getElementById('sInfo');
  b.className='ib'; b.innerHTML='<span class="spin">⏳</span> &nbsp;جارٍ جلب معلومات الفيديو...';
  var d=await post('/info',{url:url});
  if(d.error){b.innerHTML='❌ '+esc(d.error);return}
  b.className='ib ok';
  b.innerHTML='<div><strong>'+esc(d.title)+'</strong><br>'+
    '<span style="color:var(--muted);font-size:12px">⏱ '+fmtD(d.duration)+
    ' &nbsp;•&nbsp; 👁 '+(d.view_count?Number(d.view_count).toLocaleString():'—')+
    ' &nbsp;•&nbsp; 👤 '+esc(d.uploader||'—')+'</span></div>';
}

/* ── Single download ── */
async function dlSingle(){
  var url=document.getElementById('sUrl').value.trim();
  var q=document.getElementById('sQ').value;
  if(!url){toast('أدخل الرابط أولاً','e');return}
  var pw=document.getElementById('sPW'), bar=document.getElementById('sBar'),
      txt=document.getElementById('sTxt'), wrap=document.getElementById('sDlWrap');
  pw.style.display='block'; wrap.style.display='none';
  bar.style.width='0%'; txt.textContent='⏳ جارٍ التحميل على السيرفر...';
  var d=await post('/download',{url:url,quality:q,format:selFmt});
  if(d.error){toast(d.error,'e');return}
  addQI(d.id,url,null);
  pollDl(d.id,function(p,s,fn){
    bar.style.width=p+'%';
    if(s==='done'){
      txt.textContent='✅ جاهز! اضغط زر التحميل';
      wrap.style.display='block';
      wrap.innerHTML='<a class="dl-btn" href="/fetch/'+d.id+'" download="'+esc(fn||'video')+'">⬇ تحميل الملف على جهازك</a>';
      toast('✅ جاهز للتحميل');
    } else if(s==='error'){
      txt.textContent='❌ فشل التحميل';
      toast('❌ فشل التحميل','e');
    } else {
      txt.textContent='⬇ '+p.toFixed(0)+'%';
    }
  });
}

/* ── Playlist ── */
async function fpl(){
  var url=document.getElementById('plUrl').value.trim();
  if(!url){toast('أدخل الرابط','e');return}
  var list=document.getElementById('plList');
  list.innerHTML='<div class="pe"><span class="spin">⏳</span> جارٍ جلب القائمة...</div>';
  plEnt=[];
  var d=await post('/playlist_info',{url:url});
  if(d.error){list.innerHTML='<div class="pe" style="color:var(--accent)">❌ '+esc(d.error)+'</div>';return}
  plEnt=d.entries;
  list.innerHTML=d.entries.map(function(e,i){
    return '<div class="pli">'+
      '<input type="checkbox" id="plc'+i+'" checked>'+
      '<span class="pn">'+(i+1)+'</span>'+
      '<span class="ptit">'+esc(e.title||'بدون عنوان')+'</span>'+
      '<span class="pdur">'+fmtD(e.duration)+'</span>'+
      '</div>';
  }).join('');
  document.getElementById('plCnt').textContent='إجمالي: '+d.entries.length+' فيديو';
}
function sAll(v){
  document.querySelectorAll('#plList input[type=checkbox]').forEach(function(c){c.checked=v});
}
async function dlPl(){
  var sel=plEnt.filter(function(_,i){var c=document.getElementById('plc'+i);return c&&c.checked});
  if(!sel.length){toast('لم تحدد أي فيديوهات','e');return}
  var q=document.getElementById('plQ').value;
  var pw=document.getElementById('plPW'), bar=document.getElementById('plBar'), txt=document.getElementById('plTxt');
  pw.style.display='block';
  var done=0, total=sel.length;
  for(var i=0;i<sel.length;i++){
    var e=sel[i], vurl=e.url||e.webpage_url;
    if(!vurl) continue;
    txt.textContent='جارٍ تحميل: '+esc(e.title||vurl);
    var d=await post('/download',{url:vurl,quality:q,format:q==='mp3'?'mp3':'mp4'});
    if(d.id){ addQI(d.id,vurl,e.title); await waitDl(d.id); }
    done++;
    bar.style.width=(done/total*100)+'%';
    txt.textContent='مكتمل: '+done+' / '+total;
  }
  toast('✅ '+done+' فيديوهات جاهزة في الطابور');
  gpN('queue');
}

/* ── Batch ── */
function addBt(){
  var inp=document.getElementById('btEnt'), url=inp.value.trim();
  if(!url||!url.startsWith('http')){toast('أدخل رابطاً صحيحاً يبدأ بـ http','e');return}
  bUrls.push(url); inp.value=''; rBt();
}
function rBt(){
  var list=document.getElementById('btList');
  document.getElementById('btCnt').textContent=bUrls.length;
  if(!bUrls.length){list.innerHTML='<div class="pe">لم تُضف أي روابط بعد</div>';return}
  list.innerHTML=bUrls.map(function(u,i){
    return '<div class="bi">'+
      '<button class="brm" onclick="rmBt('+i+')">✕</button>'+
      '<span class="bidx">'+(i+1)+'</span>'+
      '<span class="bu">'+esc(u)+'</span></div>';
  }).join('');
}
function rmBt(i){bUrls.splice(i,1);rBt()}
function clrBt(){bUrls=[];rBt()}
async function dlBt(){
  if(!bUrls.length){toast('لا توجد روابط','e');return}
  var q=document.getElementById('btQ').value;
  var pw=document.getElementById('btPW'), bar=document.getElementById('btBar'), txt=document.getElementById('btTxt');
  pw.style.display='block';
  var done=0, total=bUrls.length;
  for(var i=0;i<bUrls.length;i++){
    var url=bUrls[i];
    txt.textContent='جارٍ تحميل '+( i+1)+' من '+total+'...';
    var d=await post('/download',{url:url,quality:q,format:q==='mp3'?'mp3':'mp4'});
    if(d.id){ addQI(d.id,url,null); await waitDl(d.id); }
    done++;
    bar.style.width=(done/total*100)+'%';
    txt.textContent='مكتمل: '+done+' / '+total;
  }
  toast('✅ '+done+' روابط جاهزة في الطابور');
  gpN('queue');
}

/* ── Queue ── */
function addQI(id, url, title){
  qItems[id]={id:id,url:url,title:title||url.slice(-50),status:'pending',progress:0};
  document.getElementById('qEmp').style.display='none';
  var el=document.createElement('div');
  el.id='qi-'+id; el.className='qi';
  el.innerHTML=
    '<div class="qt">'+
      '<span class="qico" id="qic-'+id+'">⏳</span>'+
      '<div style="flex:1">'+
        '<div class="qtit" id="qtt-'+id+'">'+esc(qItems[id].title)+'</div>'+
        '<div class="qspd" id="qsp-'+id+'"></div>'+
      '</div>'+
    '</div>'+
    '<div class="qp"><div class="qpi" id="qb-'+id+'"></div></div>'+
    '<div class="qbot">'+
      '<div class="qst" id="qst-'+id+'">في الانتظار...</div>'+
      '<div id="qdl-'+id+'"></div>'+
    '</div>';
  document.getElementById('qList').appendChild(el);
  upStats();
}
function upQI(id, fn){
  var it=qItems[id]; if(!it) return;
  var bar=document.getElementById('qb-'+id),
      ico=document.getElementById('qic-'+id),
      tit=document.getElementById('qtt-'+id),
      spd=document.getElementById('qsp-'+id),
      st =document.getElementById('qst-'+id),
      dlw=document.getElementById('qdl-'+id),
      el =document.getElementById('qi-'+id);
  if(tit&&it.title) tit.textContent=it.title;
  if(bar) bar.style.width=it.progress+'%';
  if(it.status==='done'){
    if(ico) ico.textContent='✅';
    if(bar){bar.style.width='100%';bar.classList.remove('err')}
    if(st){st.textContent='✅ جاهز للتحميل';st.className='qst done'}
    if(el){el.classList.add('done');el.classList.remove('err')}
    if(spd) spd.textContent='';
    if(dlw&&fn) dlw.innerHTML='<a class="dl-btn" href="/fetch/'+id+'" download="'+esc(fn)+'">⬇ تحميل</a>';
  } else if(it.status==='error'){
    if(ico) ico.textContent='❌';
    if(bar) bar.classList.add('err');
    if(st){st.textContent='❌ '+(it.error||'فشل التحميل');st.className='qst err'}
    if(el){el.classList.add('err');el.classList.remove('done')}
  } else if(it.status==='downloading'){
    if(ico) ico.textContent='⬇';
    var s='';
    if(it.speed) s+='السرعة: '+it.speed;
    if(it.eta)   s+=(s?' • ':'')+' المتبقي: '+it.eta;
    if(spd) spd.textContent=s;
    if(st){st.textContent=(it.progress||0).toFixed(0)+'%';st.className='qst'}
  }
}
function pollDl(id, cb){
  var tmr=setInterval(async function(){
    try{
      var r=await fetch('/status/'+id);
      var d=await r.json();
      if(!d.id){clearInterval(tmr);return}
      Object.assign(qItems[id],d);
      upQI(id, d.filename);
      upStats();
      if(cb) cb(d.progress||0, d.status, d.filename);
      if(d.status==='done'||d.status==='error'){
        clearInterval(tmr);
        if(cb) cb(d.progress||100, d.status, d.filename);
      }
    }catch(e){clearInterval(tmr)}
  }, 800);
}
function waitDl(id){
  return new Promise(function(res){
    pollDl(id, function(p,s){if(s==='done'||s==='error') res()});
  });
}
function upStats(){
  var items=Object.values(qItems);
  var tot=items.length,
      act=items.filter(function(i){return i.status==='downloading'}).length,
      done=items.filter(function(i){return i.status==='done'}).length,
      err=items.filter(function(i){return i.status==='error'}).length;
  document.getElementById('stT').textContent=tot;
  document.getElementById('stA').textContent=act;
  document.getElementById('stD').textContent=done;
  document.getElementById('stE').textContent=err;
  var b=document.getElementById('qBdg');
  if(act>0){b.style.display='';b.textContent=act} else b.style.display='none';
}
function clrDone(){
  Object.keys(qItems).forEach(function(id){
    var it=qItems[id];
    if(it.status==='done'||it.status==='error'){
      var el=document.getElementById('qi-'+id);
      if(el) el.remove();
      delete qItems[id];
    }
  });
  upStats();
  if(!Object.keys(qItems).length) document.getElementById('qEmp').style.display='';
}
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# API Routes
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return TEMPLATE

@app.route("/info", methods=["POST"])
def get_info():
    url = request.get_json().get("url","").strip()
    if not url:
        return jsonify({"error": "لم يتم توفير رابط"})
    try:
        with yt_dlp.YoutubeDL({**YDL_BASE, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        return jsonify({
            "title":      info.get("title","—"),
            "duration":   info.get("duration", 0),
            "uploader":   info.get("uploader","—"),
            "view_count": info.get("view_count", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:120]})

@app.route("/playlist_info", methods=["POST"])
def playlist_info():
    url = request.get_json().get("url","").strip()
    if not url:
        return jsonify({"error": "لم يتم توفير رابط"})
    try:
        with yt_dlp.YoutubeDL({**YDL_BASE, "extract_flat": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = info.get("entries",[])
        if not entries and info.get("_type") == "video":
            entries = [info]
        result = []
        for e in (entries or []):
            if not e: continue
            vid_url = e.get("url") or e.get("webpage_url") or ""
            if not vid_url.startswith("http"):
                vid_id = e.get("id","")
                if vid_id:
                    vid_url = f"https://www.youtube.com/watch?v={vid_id}"
            result.append({
                "title":    e.get("title","بدون عنوان"),
                "duration": e.get("duration", 0),
                "url":      vid_url,
            })
        return jsonify({"entries": result, "total": len(result)})
    except Exception as e:
        return jsonify({"error": str(e)[:120]})

@app.route("/download", methods=["POST"])
def start_download():
    data    = request.get_json()
    url     = data.get("url","").strip()
    quality = data.get("quality","best")
    fmt     = data.get("format","mp4")
    if not url:
        return jsonify({"error": "لم يتم توفير رابط"})
    dl_id      = str(uuid.uuid4())[:10]
    tmp_folder = os.path.join(TMP_DIR, dl_id)
    os.makedirs(tmp_folder, exist_ok=True)
    downloads[dl_id] = {
        "id":       dl_id,
        "status":   "pending",
        "progress": 0,
        "speed":    "",
        "eta":      "",
        "title":    "",
        "filename": "",
        "filepath": "",
        "error":    "",
    }
    threading.Thread(
        target=_run,
        args=(dl_id, url, quality, fmt, tmp_folder),
        daemon=True
    ).start()
    return jsonify({"id": dl_id})

def _build_format(quality, fmt):
    if fmt == "mp3" or quality == "mp3":
        return "bestaudio/best", True

    # كل سطر فيه 3 fallbacks بالترتيب:
    # 1) video+audio منفصلين مع merge (محتاج ffmpeg) بأعلى جودة للـ height المطلوب
    # 2) pre-merged format بنفس الـ height (مش محتاج ffmpeg)
    # 3) أي format بنفس الـ height كـ last resort
    qmap = {
        "best": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo+bestaudio"
            "/best"
        ),
        "2160": (
            "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=2160]+bestaudio"
            "/best[height<=2160]"
            "/bestvideo[height<=2160]"
        ),
        "1440": (
            "bestvideo[height<=1440][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=1440]+bestaudio"
            "/best[height<=1440]"
            "/bestvideo[height<=1440]"
        ),
        "1080": (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=1080]+bestaudio"
            "/best[height<=1080]"
            "/bestvideo[height<=1080]"
        ),
        "720": (
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720]"
            "/bestvideo[height<=720]"
        ),
        "480": (
            "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=480]+bestaudio"
            "/best[height<=480]"
            "/bestvideo[height<=480]"
        ),
        "360": (
            "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=360]+bestaudio"
            "/best[height<=360]"
            "/bestvideo[height<=360]"
        ),
        "240": (
            "bestvideo[height<=240][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=240]+bestaudio"
            "/best[height<=240]"
            "/bestvideo[height<=240]"
        ),
        "144": (
            "bestvideo[height<=144][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=144]+bestaudio"
            "/best[height<=144]"
            "/bestvideo[height<=144]"
        ),
    }
    f = qmap.get(quality, qmap["best"])
    if fmt == "webm":
        f = f.replace("[ext=mp4]", "[ext=webm]").replace("[ext=m4a]", "[ext=webm]")
    return f, False

def _run(dl_id, url, quality, fmt, tmp_folder):
    state = downloads[dl_id]
    state["status"] = "downloading"
    format_str, is_audio = _build_format(quality, fmt)

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            dled  = d.get("downloaded_bytes", 0)
            state["progress"] = round(dled / total * 100, 1) if total else 0
            spd = d.get("speed")
            eta = d.get("eta")
            state["speed"] = f"{spd/1024/1024:.1f} MB/s" if spd else ""
            state["eta"]   = f"{int(eta)}ث" if eta else ""
            if d.get("info_dict") and not state["title"]:
                state["title"] = d["info_dict"].get("title","")
        elif d["status"] == "finished":
            state["progress"] = 100

    try:
        # Fetch title first (fast, no download)
        with yt_dlp.YoutubeDL({**YDL_BASE, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            state["title"] = info.get("title", url[:60])

        opts = {
            **YDL_BASE,
            "format":              format_str,
            "outtmpl":             os.path.join(tmp_folder, "%(title)s.%(ext)s"),
            "progress_hooks":      [hook],
            "merge_output_format": "mp4" if not is_audio else None,
        }
        if is_audio:
            opts["postprocessors"] = [{
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            }]

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        # Find what got saved (exclude .part temp files)
        files = [f for f in os.listdir(tmp_folder) if not f.endswith(".part")]
        if not files:
            raise Exception("الملف لم يُوجد بعد انتهاء التحميل")

        filepath = os.path.join(tmp_folder, files[0])
        state["filepath"] = filepath
        state["filename"]  = files[0]
        state["status"]    = "done"
        state["progress"]  = 100

    except Exception as e:
        state["status"] = "error"
        state["error"]  = str(e)[:200]
        shutil.rmtree(tmp_folder, ignore_errors=True)

@app.route("/status/<dl_id>")
def get_status(dl_id):
    if dl_id not in downloads:
        return jsonify({"error": "not found"}), 404
    s = downloads[dl_id]
    return jsonify({
        "id":       s["id"],
        "status":   s["status"],
        "progress": s["progress"],
        "speed":    s["speed"],
        "eta":      s["eta"],
        "title":    s["title"],
        "filename": s["filename"],
        "error":    s["error"],
    })

@app.route("/fetch/<dl_id>")
def fetch_file(dl_id):
    """Stream the file to the user's browser, then delete it from /tmp."""
    if dl_id not in downloads:
        return "الملف غير موجود", 404
    state = downloads[dl_id]
    if state["status"] != "done" or not state.get("filepath"):
        return "الملف لم يكتمل بعد", 400
    filepath = state["filepath"]
    if not os.path.exists(filepath):
        return "انتهت صلاحية الملف — أعد التحميل", 410

    filename = state["filename"]

    def stream_then_delete():
        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)  # 64 KB
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                shutil.rmtree(os.path.dirname(filepath), ignore_errors=True)
                def _clean_state():
                    time.sleep(10)
                    downloads.pop(dl_id, None)
                threading.Thread(target=_clean_state, daemon=True).start()
            except Exception:
                pass

    ext     = filename.rsplit(".", 1)[-1].lower()
    mime_map = {"mp4":"video/mp4","webm":"video/webm","mp3":"audio/mpeg","m4a":"audio/mp4"}
    mime    = mime_map.get(ext, "application/octet-stream")
    safe    = quote(filename.encode("utf-8"))

    resp = Response(stream_then_delete(), mimetype=mime)
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{safe}"
    resp.headers["Content-Length"]      = os.path.getsize(filepath)
    resp.headers["Cache-Control"]       = "no-cache"
    return resp

# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import webbrowser, time as _t
    print("\n" + "="*50)
    print("  YouTube Downloader Pro  |  عمر جمال")
    print("="*50)
    print("  http://localhost:5000")
    print("  Ctrl+C للإيقاف")
    print("="*50 + "\n")
    threading.Thread(
        target=lambda: (_t.sleep(1.2), webbrowser.open("http://localhost:5000")),
        daemon=True
    ).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
