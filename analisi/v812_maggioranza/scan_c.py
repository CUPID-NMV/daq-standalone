# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
# Scan della soglia di maggioranza del V812 a run aperta: scrive valori grezzi
# nel registro 0x48 e annota i confini in eventi di ogni passo.
import json,time,urllib.request,glob,os,subprocess,h5py
B="http://localhost:8766"; D=ROOT + "/data/LNGS"
def req(p,body=None,post=False):
    data=json.dumps(body).encode() if body is not None else (b"" if post else None)
    r=urllib.request.Request(B+p,data=data,method="POST" if data is not None else "GET",headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r,timeout=60))
def ferma():
    print("ferma:",req("/api/ferma?chi=claude",post=True).get("messaggio"),flush=True)
    for _ in range(300):
        if not req("/api/stato")["in_corso"]: return
        time.sleep(1)
def config(mod):
    m=req("/api/config")["mtime"]; r=req("/api/config",{"chi":"claude","mtime":m,"modifiche":mod}); print("config:",r.get("esito"),r.get("messaggio"),[(d["chiave"],d["a"]) for d in r.get("diff") or []],flush=True)
    if not r.get("esito"): raise SystemExit("configurazione rifiutata, non parto")
def n_eventi(fn):
    try:
        with h5py.File(fn,"r",libver="latest",swmr=True) as f: return f["events/event_counter"].shape[0]
    except Exception: return 0
if req("/api/stato")["in_corso"]: ferma()
config([["cfd","Channels","0, 1, 2"],["cfd","Threshold","30, 30, 30"],["cfd","Majority","2"],
        ["digitizer","NEvents","100000000"]])
prima=set(glob.glob(D+"/*.h5"))
print("avvia:",req("/api/avvia?chi=claude",post=True).get("messaggio"),flush=True)
for _ in range(120):
    nuovi=set(glob.glob(D+"/*.h5"))-prima
    if nuovi: break
    time.sleep(1)
fn=nuovi.pop(); print("file",fn,flush=True)
time.sleep(25)  # baseline e avvio
valori=[8,10,12,14,15,16,17,18,19,20,21,22,24,27,30,31,32,33,34,35,36,37,38,40,43,46,48,50,52,54,56,58,60,64,70]
passi=[]
for v in valori:
    dur=10 if v<=24 else (20 if v<=40 else 40)
    subprocess.run([_os.path.join(ROOT, "analisi", "v812_maggioranza", "majthr"),str(v)],check=True)
    t0=time.time(); n0=n_eventi(fn)
    time.sleep(dur)
    n1=n_eventi(fn); t1=time.time()
    passi.append({"majthr":v,"n0":n0,"n1":n1,"t0":t0,"t1":t1})
    print(f"MAJTHR {v:3d}: {n1-n0:6d} eventi in {t1-t0:.0f} s = {(n1-n0)/(t1-t0):7.1f} Hz",flush=True)
ferma()
json.dump({"file":fn,"passi":passi},open("/tmp/scan_c.json","w"),indent=1)
print("fatto")
