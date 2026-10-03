#!/usr/bin/env python3
"""Zebra QR demo: two jurors -> local de-identification/encoding -> protected similarity.

Hackathon proof-of-concept only. Use synthetic cases.

Run:
  pip install "qrcode[pil]"     # optional, for QR images
  python zebra_qr_demo.py --host 0.0.0.0 --port 8765 --save-encrypted ./zebra_payloads

Then open the printed dashboard URL on the presentation laptop.
"""

import argparse, html, io, json, secrets, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

LOCK = threading.Lock()
STATE = {"session": secrets.token_urlsafe(10), "a": None, "b": None, "score": None, "updated": time.time()}
PUBLIC_BASE = None
SAVE_DIR = None

STYLE = r"""
:root{--bg:#f5f7f8;--card:#fff;--text:#17212b;--muted:#64717d;--line:#d9e0e5;--accent:#176c69;--ok:#287a4a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
main{max-width:980px;margin:auto;padding:28px 18px 60px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}@media(max-width:720px){.grid{grid-template-columns:1fr}}
h1{font-size:30px;line-height:1.15;margin:4px 0 8px}h2{font-size:19px;margin:0 0 10px}.muted,small{color:var(--muted)}label{display:block;font-size:14px;font-weight:700;margin:13px 0 5px}
input,select,textarea,button{font:inherit}input,select,textarea{width:100%;border:1px solid var(--line);border-radius:10px;padding:11px 12px;background:#fff}textarea{min-height:135px;resize:vertical}
button{border:1px solid var(--line);border-radius:10px;padding:11px 14px;background:#fff;font-weight:750;cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button:disabled{opacity:.45;cursor:not-allowed}
.box{border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:12px}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 9px;margin:3px 4px 3px 0;font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all;font-size:12px}.hidden{display:none}.topline{font-size:12px;letter-spacing:.09em;font-weight:850;color:var(--muted)}.qr{width:210px;max-width:100%;height:auto;border:1px solid var(--line);border-radius:12px;padding:8px;background:#fff}.url{word-break:break-all;font-size:13px;color:var(--muted)}.center{text-align:center}.score{font-size:78px;font-weight:900;line-height:1}.steps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin:16px 0}.step{border:1px solid var(--line);border-radius:10px;padding:8px;font-size:12px;text-align:center;background:#fff}@media(max-width:650px){.steps{grid-template-columns:1fr}}
"""

def page(title, body, script=""):
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{STYLE}</style></head><body>{body}{script}</body></html>"

def local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8",80)); return s.getsockname()[0]
    except Exception:
        try:return socket.gethostbyname(socket.gethostname())
        except Exception:return "127.0.0.1"
    finally:s.close()

def write_json(name,obj):
    if not SAVE_DIR:return
    SAVE_DIR.mkdir(parents=True,exist_ok=True)
    (SAVE_DIR/name).write_text(json.dumps(obj,indent=2),encoding="utf-8")

def reset():
    with LOCK:
        STATE["a"]=STATE["b"]=STATE["score"]=None; STATE["updated"]=time.time()
    if SAVE_DIR:
        for n in ("juror_A_encrypted.json","juror_B_encrypted_result.json","similarity_score.json"):
            try:(SAVE_DIR/n).unlink()
            except FileNotFoundError:pass

JUROR_JS = r"""
<script>
(()=>{
'use strict';
const ROLE='__ROLE__', SESSION='__SESSION__';
const F=[
['bloating',['bloating','abdominal distension','distended abdomen']],
['early_satiety',['early satiety','full quickly','reduced appetite','poor appetite']],
['pelvic_pressure_pain',['pelvic pressure','pelvic pain','pelvic discomfort']],
['urinary_frequency_urgency',['urinary frequency','urinary urgency','frequent urination']],
['fatigue',['fatigue','tiredness','tired','low energy']],
['abdominal_pain_discomfort',['abdominal pain','abdominal discomfort','stomach pain']],
['constipation_diarrhea',['constipation','diarrhea','diarrhoea','bowel changes']],
['nausea_vomiting',['nausea','vomiting','vomited','feeling sick']],
['weight_loss',['weight loss','lost weight','unintentional weight']],
['abnormal_bleeding',['abnormal bleeding','postmenopausal bleeding','vaginal bleeding']],
['dizziness_vertigo',['dizziness','dizzy','vertigo','room spinning']],
['chest_discomfort',['chest pain','chest discomfort','chest pressure','tightness in chest']],
['shortness_of_breath',['shortness of breath','breathless','dyspnea','dyspnoea']],
['back_jaw_arm_discomfort',['back pain','jaw pain','arm pain','shoulder pain']],
['headache_neuro',['severe headache','weakness','numbness','slurred speech','double vision','ataxia']]
];
const $=id=>document.getElementById(id);let v=null,iv=null,pub=null,priv=null,timer=null;const SCALE=10000;
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
function duration(t){t=t.toLowerCase();let m=0;for(const z of t.matchAll(/(\d{1,2})\s*(month|months|mo\b)/g))m=Math.max(m,+z[1]);for(const z of t.matchAll(/(\d{1,2})\s*(week|weeks|wk\b)/g))m=Math.max(m,+z[1]/4.345);if(m)return clamp(m/12,0,1);return /persistent|progressive|recurrent|long[- ]?standing/.test(t)?.6:0}
function encode(){
  const age=clamp(+$('age').value||0,0,120)/100, g=$('gender').value, text=$('symptoms').value.trim().toLowerCase();
  const pain=clamp(+$('pain').value||0,0,10)/10;
  const fever=$('fever').value==='yes'?1:0;
  const weightLoss=$('weightLoss').value==='yes'?1:0;
  const worsening=$('worsening').value==='yes'?1:0;
  const nightSymptoms=$('nightSymptoms').value==='yes'?1:0;
  // Name and city are intentionally never read here.
  // First entries remain age + gender block. Structured clinical fields follow.
  const raw=[age,g==='female'?1:0,g==='male'?1:0,(g==='other'||g==='unknown')?1:0,
             pain,fever,weightLoss,worsening,nightSymptoms], found=[];
  if(pain>0)found.push('pain level '+Math.round(pain*10)+'/10');
  if(fever)found.push('fever');
  if(weightLoss)found.push('unintentional weight loss');
  if(worsening)found.push('worsening course');
  if(nightSymptoms)found.push('night symptoms');
  for(const [lab,terms] of F){const hit=terms.some(k=>text.includes(k));raw.push(hit?1:0);if(hit)found.push(lab)}
  const d=duration(text);raw.push(d);if(d)found.push('persistence');
  const n=Math.sqrt(raw.reduce((s,x)=>s+x*x,0))||1;v=raw.map(x=>x/n);iv=v.map(x=>Math.max(0,Math.round(x*SCALE)));
  $('privacy').innerHTML='<b>Deleted locally:</b> name, city<br><b>Retained:</b> age, gender block, pain level, fever, weight loss, worsening/night pattern, recognized symptoms, persistence';
  $('recognized').innerHTML=found.length?found.map(x=>`<span class="badge">${x.replaceAll('_',' ')}</span>`).join(''):'<span class="muted">No demo symptom concepts recognized.</span>';
  $('vector').textContent='['+v.map(x=>x.toFixed(3)).join(', ')+']';$('local').classList.remove('hidden');$('submit').disabled=false;$('status').textContent='✓ Vector created locally. Nothing has been sent.';
}
function modPow(b,e,m){b%=m;let r=1n;while(e>0n){if(e&1n)r=r*b%m;b=b*b%m;e>>=1n}return r}
function gcd(a,b){while(b){let t=a%b;a=b;b=t}return a<0n?-a:a}
function egcd(a,b){if(b===0n)return[a,1n,0n];const[g,x,y]=egcd(b,a%b);return[g,y,x-(a/b)*y]}
function inv(a,m){const[g,x]=egcd((a%m+m)%m,m);if(g!==1n)throw Error('inverse');return(x%m+m)%m}
function lcm(a,b){return a/gcd(a,b)*b}
function rbig(bits){const a=new Uint8Array(Math.ceil(bits/8));crypto.getRandomValues(a);a[0]|=0x80;a[a.length-1]|=1;let x=0n;for(const b of a)x=x<<8n|BigInt(b);return x}
function below(n){const bits=n.toString(2).length;while(true){const a=new Uint8Array(Math.ceil(bits/8));crypto.getRandomValues(a);let x=0n;for(const b of a)x=x<<8n|BigInt(b);if(x<n)return x}}
function prime(n,R=9){const sm=[2n,3n,5n,7n,11n,13n,17n,19n,23n,29n,31n,37n];if(n<2n)return false;for(const p of sm){if(n===p)return true;if(n%p===0n)return false}let d=n-1n,s=0;while((d&1n)===0n){d>>=1n;s++}for(let i=0;i<R;i++){const a=2n+below(n-3n);let x=modPow(a,d,n);if(x===1n||x===n-1n)continue;let ok=false;for(let r=1;r<s;r++){x=modPow(x,2n,n);if(x===n-1n){ok=true;break}}if(!ok)return false}return true}
async function genPrime(bits){let k=0;while(true){const n=rbig(bits);if(prime(n))return n;if(++k%30===0)await new Promise(r=>setTimeout(r,0))}}
async function keygen(){const B=256;let p=await genPrime(B),q=await genPrime(B);while(p===q)q=await genPrime(B);const n=p*q,g=n+1n,nsq=n*n,lam=lcm(p-1n,q-1n),mu=inv(lam,n);return{pub:{n,g,nsq},priv:{lam,mu}}}
function enc(m,P){let r;do{r=1n+below(P.n-1n)}while(gcd(r,P.n)!==1n);return modPow(P.g,BigInt(m),P.nsq)*modPow(r,P.n,P.nsq)%P.nsq}
function dec(c,P,S){const u=modPow(c,S.lam,P.nsq),L=(u-1n)/P.n;return L*S.mu%P.n}
function edot(E,b,P){let a=1n;for(let i=0;i<E.length;i++)a=a*modPow(E[i],BigInt(b[i]),P.nsq)%P.nsq;return a}
async function api(path,method='GET',body=null){const o={method,headers:{}};if(body!==null){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}const r=await fetch(path,o);if(!r.ok)throw Error('server '+r.status);return r.json()}
async function sendA(){
 $('submit').disabled=true;$('status').textContent='Generating demo homomorphic key locally…';const k=await keygen();pub=k.pub;priv=k.priv;$('status').textContent='Encrypting vector locally…';const E=iv.map(x=>enc(x,pub));
 await api('/api/a','POST',{session:SESSION,n:pub.n.toString(),g:pub.g.toString(),ciphertexts:E.map(String),scale:SCALE,dimension:E.length});
 $('cipher').textContent='Encrypted vector sent: '+E[0].toString().slice(0,40)+'…';$('cipherBox').classList.remove('hidden');$('status').textContent='🔒 Protected vector submitted. Raw form and vector remain on this phone.';timer=setInterval(pollB,650)
}
async function pollB(){try{const d=await api('/api/b');if(!d.ready)return;clearInterval(timer);const m=dec(BigInt(d.encrypted_dot),pub,priv);let s=clamp(Number(m)/(SCALE*SCALE),0,1);await api('/api/score','POST',{session:SESSION,score:s});$('status').textContent='✓ Only the final similarity scalar was decrypted and released.'}catch(e){}}
async function sendB(){
 $('submit').disabled=true;$('status').textContent='Waiting for Juror A encrypted vector…';let d;for(let i=0;i<240;i++){d=await api('/api/a');if(d.ready)break;await new Promise(r=>setTimeout(r,500))}if(!d||!d.ready)throw Error('Juror A not ready');
 const p=d.payload;if(+p.dimension!==iv.length)throw Error('dimension mismatch');const P={n:BigInt(p.n),g:BigInt(p.g)};P.nsq=P.n*P.n;const E=p.ciphertexts.map(BigInt);$('status').textContent='Computing encrypted dot product locally; your vector is not uploaded…';const c=edot(E,iv,P);await api('/api/b','POST',{session:SESSION,encrypted_dot:c.toString()});$('cipher').textContent='Encrypted result sent: '+c.toString().slice(0,40)+'…';$('cipherBox').classList.remove('hidden');$('status').textContent='✓ Encrypted result submitted. Your form and plaintext vector never left this phone.';
}
$('encode').addEventListener('click',encode);$('submit').addEventListener('click',async()=>{try{if(!iv)encode();if(ROLE==='A')await sendA();else await sendB()}catch(e){$('status').textContent='Error: '+e.message;$('submit').disabled=false}});
})();
</script>
"""

def juror(role):
    role=role.upper()
    body=f"""
<main><div class='topline'>ZEBRA • PRIVATE JUROR {role}</div><h1>Clinical similarity demo</h1><p class='muted'>Use a synthetic patient. Name and city are included only to visibly demonstrate local removal.</p>
<div class='card'><div class='grid'>
<div><label>Name</label><input id='name' placeholder='Anna Example'><small>Never read by the encoder.</small></div>
<div><label>City</label><input id='city' placeholder='Kaunas'><small>Never read by the encoder.</small></div>
<div><label>Age</label><input id='age' type='number' min='0' max='120' value='58'></div>
<div><label>Gender / sex (demo)</label><select id='gender'><option value='female'>Female</option><option value='male'>Male</option><option value='other'>Other</option><option value='unknown'>Unknown</option></select></div>
</div>
<div class='grid'>
<div><label>Pain level (0–10)</label><input id='pain' type='number' min='0' max='10' step='1' value='0'></div>
<div><label>Fever?</label><select id='fever'><option value='no'>No</option><option value='yes'>Yes</option></select></div>
<div><label>Unintentional weight loss?</label><select id='weightLoss'><option value='no'>No</option><option value='yes'>Yes</option></select></div>
<div><label>Symptoms worsening?</label><select id='worsening'><option value='no'>No</option><option value='yes'>Yes</option></select></div>
<div><label>Symptoms wake patient at night?</label><select id='nightSymptoms'><option value='no'>No</option><option value='yes'>Yes</option></select></div>
</div>
<label>Symptoms / short clinical description</label><textarea id='symptoms' placeholder='Example: 8 months of progressive bloating, early satiety, pelvic pressure and urinary frequency...'></textarea>
<div class='steps'><div class='step'>1 Form</div><div class='step'>2 Drop identifiers</div><div class='step'>3 Encode</div><div class='step'>4 Encrypt</div><div class='step'>5 Similarity only</div></div>
<button id='encode' class='primary' type='button'>1. Create private vector locally</button>
<div id='local' class='hidden'><div class='box'><b>Privacy filter</b><div id='privacy' class='muted' style='margin-top:6px'></div></div><div class='box'><b>Recognized clinical concepts</b><div id='recognized' style='margin-top:6px'></div></div><div class='box'><b>Local vector</b><div id='vector' class='mono' style='margin-top:6px'></div><small>This plaintext vector is visible only on this phone.</small></div></div>
<button id='submit' type='button' style='margin-top:14px;width:100%' disabled>{'2. Encrypt & submit protected vector' if role=='A' else '2. Compute protected comparison'}</button>
<div id='cipherBox' class='box hidden'><b>Protected payload</b><div id='cipher' class='mono muted' style='margin-top:6px'></div></div><p id='status' class='muted'>Nothing has been sent.</p></div>
<p class='muted' style='font-size:12px'>Hackathon PoC only; synthetic cases only. Browser cryptography is intentionally lightweight for demo speed, not production security.</p></main>"""
    js=JUROR_JS.replace('__ROLE__',role).replace('__SESSION__',STATE['session'])
    return page(f"Zebra Juror {role}",body,js)

DASH_JS="""<script>async function poll(){try{let s=await (await fetch('/api/status',{cache:'no-store'})).json();document.getElementById('sa').textContent=s.a_ready?'Encrypted A received ✓':'Waiting';document.getElementById('sb').textContent=s.b_ready?'Encrypted B-result received ✓':'Waiting';document.getElementById('ss').textContent=s.score===null?'Not released':(s.score*100).toFixed(1)+'%'}catch(e){}setTimeout(poll,700)}async function resetDemo(){await fetch('/api/reset',{method:'POST'});location.reload()}poll()</script>"""

def dashboard():
    a=f"{PUBLIC_BASE}/juror/A?s={STATE['session']}";b=f"{PUBLIC_BASE}/juror/B?s={STATE['session']}";s=f"{PUBLIC_BASE}/screen"
    body=f"""<main><div class='topline'>ZEBRA • DEMO CONTROL</div><h1>Two-juror private comparison</h1><p class='muted'>Give one QR to each juror. Project only the similarity screen.</p>
<div class='grid'><div class='card center'><h2>Juror A</h2><img class='qr' src='/qr?u={html.escape(a)}'><div class='url'>{html.escape(a)}</div><p id='sa' class='muted'>Waiting</p></div><div class='card center'><h2>Juror B</h2><img class='qr' src='/qr?u={html.escape(b)}'><div class='url'>{html.escape(b)}</div><p id='sb' class='muted'>Waiting</p></div></div>
<div class='card' style='margin-top:18px'><h2>Projector</h2><div class='url'>{html.escape(s)}</div><p>Released result: <b id='ss'>Not released</b></p><p><a href='/screen' target='_blank'>Open projector-only screen</a></p><button onclick='resetDemo()'>Reset demo</button></div>
<div class='card' style='margin-top:18px'><h2>What this laptop receives</h2><p>✓ A: public key + encrypted vector</p><p>✓ B: encrypted scalar</p><p>✓ Final: similarity only</p><p>✗ No name, city, raw symptom text, or plaintext vector.</p></div></main>"""
    return page('Zebra Demo Control',body,DASH_JS)

SCREEN_JS="""<script>async function poll(){try{let s=await (await fetch('/api/status',{cache:'no-store'})).json();let w=document.getElementById('w'),o=document.getElementById('o');if(s.score!==null){w.classList.add('hidden');o.classList.remove('hidden');document.getElementById('score').textContent=(s.score*100).toFixed(1)+'%'}else{o.classList.add('hidden');w.classList.remove('hidden')}}catch(e){}setTimeout(poll,600)}poll()</script>"""

def screen():
    body="""<main class='center' style='max-width:900px;padding-top:100px'><div class='topline'>ZEBRA • PRIVATE CLINICAL SIMILARITY</div><div id='w'><h1 style='font-size:46px;margin-top:32px'>Waiting for private submissions…</h1><p class='muted'>No patient text or vector is displayed.</p></div><div id='o' class='hidden'><div id='score' class='score'>--</div><h1>Clinical similarity index</h1><p class='muted'>Only the final similarity value was released.</p></div></main>"""
    return page('Zebra Similarity',body,SCREEN_JS)

class H(BaseHTTPRequestHandler):
    server_version='ZebraQR/0.3'
    def log_message(self,*a):pass
    def sendb(self,b,ct='text/html; charset=utf-8',code=200):
        self.send_response(code);self.send_header('Content-Type',ct);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b)
    def sendj(self,o,code=200):self.sendb(json.dumps(o).encode(),'application/json',code)
    def bodyj(self):
        n=int(self.headers.get('Content-Length','0') or 0);return json.loads(self.rfile.read(n).decode() if n else '{}')
    def do_GET(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        if p=='/':return self.sendb(dashboard().encode())
        if p=='/screen':return self.sendb(screen().encode())
        if p.startswith('/juror/'):
            if q.get('s',[''])[0]!=STATE['session']:return self.sendb(b'Invalid demo link', 'text/plain',403)
            return self.sendb(juror(p.rsplit('/',1)[-1]).encode())
        if p=='/qr':
            target=q.get('u',[''])[0]
            try:
                import qrcode
                im=qrcode.make(target);buf=io.BytesIO();im.save(buf,format='PNG');return self.sendb(buf.getvalue(),'image/png')
            except Exception:
                svg=f"<svg xmlns='http://www.w3.org/2000/svg' width='320' height='210'><rect width='100%' height='100%' fill='white'/><text x='15' y='95' font-size='16'>Install qrcode[pil] for QR</text><text x='15' y='125' font-size='11'>{html.escape(target[:45])}</text></svg>";return self.sendb(svg.encode(),'image/svg+xml')
        if p=='/api/status':
            with LOCK:o={'a_ready':STATE['a'] is not None,'b_ready':STATE['b'] is not None,'score':STATE['score']}
            return self.sendj(o)
        if p=='/api/a':
            with LOCK:x=STATE['a']
            return self.sendj({'ready':x is not None,'payload':x})
        if p=='/api/b':
            with LOCK:x=STATE['b']
            return self.sendj({'ready':x is not None,'encrypted_dot':x})
        return self.sendj({'error':'not found'},404)
    def do_POST(self):
        p=urlparse(self.path).path
        try:d=self.bodyj()
        except:return self.sendj({'error':'bad json'},400)
        if p=='/api/reset':reset();return self.sendj({'ok':True})
        if d.get('session')!=STATE['session']:return self.sendj({'error':'bad session'},403)
        if p=='/api/a':
            req={'n','g','ciphertexts','scale','dimension'}
            if not req.issubset(d):return self.sendj({'error':'missing fields'},400)
            x={'n':str(d['n']),'g':str(d['g']),'ciphertexts':[str(z) for z in d['ciphertexts']],'scale':int(d['scale']),'dimension':int(d['dimension'])}
            with LOCK:STATE['a']=x;STATE['b']=STATE['score']=None;STATE['updated']=time.time()
            write_json('juror_A_encrypted.json',x);return self.sendj({'ok':True})
        if p=='/api/b':
            x=str(d.get('encrypted_dot',''))
            if not x:return self.sendj({'error':'missing result'},400)
            with LOCK:STATE['b']=x;STATE['updated']=time.time()
            write_json('juror_B_encrypted_result.json',{'encrypted_dot':x});return self.sendj({'ok':True})
        if p=='/api/score':
            try:x=max(0,min(1,float(d['score'])))
            except:return self.sendj({'error':'bad score'},400)
            with LOCK:STATE['score']=x;STATE['updated']=time.time()
            write_json('similarity_score.json',{'similarity':x});return self.sendj({'ok':True})
        return self.sendj({'error':'not found'},404)

def main():
    global PUBLIC_BASE,SAVE_DIR
    ap=argparse.ArgumentParser();ap.add_argument('--host',default='0.0.0.0');ap.add_argument('--port',type=int,default=8765);ap.add_argument('--public-host');ap.add_argument('--save-encrypted')
    a=ap.parse_args();ip=a.public_host or local_ip();PUBLIC_BASE=f'http://{ip}:{a.port}'
    if a.save_encrypted:SAVE_DIR=Path(a.save_encrypted).resolve();SAVE_DIR.mkdir(parents=True,exist_ok=True)
    print('\nZEBRA QR DEMO');print('============');print('Dashboard: ',PUBLIC_BASE+'/');print('Projector: ',PUBLIC_BASE+'/screen');print('Juror A:   ',PUBLIC_BASE+f"/juror/A?s={STATE['session']}");print('Juror B:   ',PUBLIC_BASE+f"/juror/B?s={STATE['session']}");print('\nPhones and laptop must be on the same Wi-Fi/hotspot. Use SYNTHETIC cases only.')
    if SAVE_DIR:print('Encrypted payload folder:',SAVE_DIR)
    print()
    s=ThreadingHTTPServer((a.host,a.port),H)
    try:s.serve_forever()
    except KeyboardInterrupt:pass
    finally:s.server_close()

if __name__=='__main__':main()
