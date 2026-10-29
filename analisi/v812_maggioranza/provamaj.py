# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import json,time,urllib.request,glob,os,re
B="http://localhost:8766"
def req(p,body=None):
    r=urllib.request.Request(B+p,data=json.dumps(body).encode() if body is not None else (b"" if p.startswith("/api/avvia") or p.startswith("/api/ferma") else None),method="POST" if body is not None or p.startswith(("/api/avvia","/api/ferma")) else "GET",headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r,timeout=60))
def stato(): return req("/api/stato")
def ferma():
    print("ferma:",req("/api/ferma?chi=claude").get("messaggio"))
    for _ in range(120):
        if not stato()["in_corso"]: return
        time.sleep(1)
    raise SystemExit("non si ferma")
def maj(n):
    m=req("/api/config")["mtime"]
    r=req("/api/config",{"chi":"claude","mtime":m,"modifiche":[["cfd","Majority",str(n)]]}); print("config:",r.get("esito"),r.get("diff"))
def corri(sec):
    print("avvia:",req("/api/avvia?chi=claude").get("messaggio"))
    time.sleep(sec); ferma()
    L=max(glob.glob(ROOT + "/data/LNGS/*.log"),key=os.path.getmtime)
    t=open(L).read()
    print(os.path.basename(L), re.findall(r"maggioranza \(livello \d\)",t), re.findall(r"Total events recorded: \d+|Acquisition time: [\d.]+ s",t))
import sys
if stato()["in_corso"]: ferma()
for n in (4,2,3):
    maj(n); corri(40)
