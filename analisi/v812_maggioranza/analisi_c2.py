# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import json,sys,numpy as np,h5py
sys.path.insert(0,ROOT + "/tools"); import daqio
S=json.load(open(ROOT + "/measurements/v812_majthr_20261028.json"))
hdr,w=daqio.load(S["file"].replace(".h5",".h5.gz") if not __import__("os").path.exists(S["file"]) else S["file"],channels=[16,17,18])
w=w.astype(np.float32)
y=w-np.median(w[...,:150],axis=-1,keepdims=True)
mv=y[...,250:750].max(-1)*1000/4096     # 333-1000 ns: la zona degli impulsi del trigger
print("eventi nel file",len(w))
print(" MAJTHR  4*MAJTHR[mV]  rate[Hz]  eventi   molteplicita (>15 mV)  1     2     3")
for p in S["passi"]:
    n0,n1=p["n0"],p["n1"]; sk=max(5,int(0.05*(n1-n0))); a,b=n0+sk,n1
    if b<=a: print(p["majthr"],"nessun evento"); continue
    k=(mv[a:b]>15).sum(1); h=np.bincount(k,minlength=4)
    f=h/h.sum()
    m=p["majthr"]; r=(n1-n0)/(p["t1"]-p["t0"])
    print(f"{m:6d} {4*m:8d} {r:10.2f} {b-a:8d}              {f[0]:.2f}  {f[1]:.2f}  {f[2]:.2f}  {f[3]:.2f}   n={h[1]},{h[2]},{h[3]}")
