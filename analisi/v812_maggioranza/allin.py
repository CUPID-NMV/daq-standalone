# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import glob,sys,numpy as np
sys.path.insert(0,"tools"); import daqio
tot=0;rows=[]
for fn in sorted(glob.glob(ROOT + "/data/LNGS/scan/*.h5.gz"))+sorted(glob.glob(ROOT + "/data/LNGS/test__54-0V_00[234]*_55PT.h5*")):
    try: d=daqio.load(fn)
    except Exception as e: continue
    w=d["waveforms"].astype(float) if "waveforms" in d else None
    if w is None: continue
    chs=list(d["channels"]); dt=d.get("dt_ns",1/0.75)
    ped=np.median(w[...,:150],axis=-1,keepdims=True); y=w-ped
    sgn=np.array([1,1,1,-1,-1])[:len(chs)].reshape(1,-1,1)
    y=y*sgn
    amp=y[...,20:-20].max(-1); t=(y[...,20:-20].argmax(-1)+20)*dt
    sig=1.4826*np.median(np.abs(y[...,:150]-0),axis=-1)
    ok=(amp>8*np.maximum(sig,1)).all(1)
    tot+=len(w)
    for i in np.where(ok)[0]: rows.append((fn.split("/")[-1],i,*amp[i].round(0),*t[i].round(0)))
print("eventi letti",tot,"con tutti e 5 sopra 8 sigma:",len(rows))
for r in rows[:25]: print(r)
