# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import json,sys,os,numpy as np,matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0,"tools"); import daqio
S=json.load(open(ROOT + "/measurements/v812_majthr_20261027.json"))
fn=S["file"]; fn=fn if os.path.exists(fn) else fn+".gz"
hdr,w=daqio.load(fn,channels=[16,17,18]); w=w.astype(np.float32)
y=w-np.median(w[...,:150],axis=-1,keepdims=True); mv=y[...,250:750].max(-1)*1000/4096
x=[];R=[];N=[]
for p in S["passi"]:
    a,b=p["n0"]+max(5,int(0.05*(p["n1"]-p["n0"]))),p["n1"]; r=(p["n1"]-p["n0"])/(p["t1"]-p["t0"])
    x.append(4*p["majthr"])
    if b<=a: R.append([0,0,0]); N.append([0,0,0]); continue
    h=np.bincount((mv[a:b]>15).sum(1),minlength=4)[1:4]; N.append(h); R.append(r*h/h.sum())
x=np.array(x);R=np.array(R);N=np.array(N)
col=["#0072B2","#E69F00","#009E73"]
fig,(a1,a2)=plt.subplots(2,1,figsize=(9,8.5),gridspec_kw={"height_ratios":[3,2]})
for k in range(3):
    m=N[:,k]>0
    e=np.where(m,R[:,k]/np.sqrt(np.maximum(N[:,k],1)),0)
    a1.errorbar(x[m],R[m,k],yerr=e[m],fmt="o-",ms=5,lw=1.5,color=col[k],label=["1 SiPM with a pulse","2 SiPMs with a pulse","3 SiPMs with a pulse"][k])
z=x[(N.sum(1)==0)]; a1.plot(z,np.full(len(z),0.03),"v",color="#888",ms=6,label="no events (40 s)")
for lv,thr in ((2,19),(3,31),(4,44)):
    a1.axvline(4*thr,color="#555",ls="--",lw=1); a1.text(4*thr+1,1500,f"Majority={lv}\n(manual)",fontsize=8,color="#333",va="top")
for k,c in zip((1,2,3),col): a1.axvline(50*k,color=c,ls=":",lw=1.5)
a1.text(52,0.04,"dotted: where k-channel events\nshould vanish (50 mV/channel)",fontsize=8,color="#333")
a1.set_yscale("log"); a1.set_ylim(0.01,3000); a1.set_xlim(20,290)
a1.set_xlabel("majority threshold written to the V812 [mV]  (register value x 4)"); a1.set_ylabel("rate [Hz]")
a1.set_title("V812 majority threshold scan, 3 inputs (SiPM, -30 mV), run 0050",fontsize=11); a1.legend(fontsize=8,loc="upper right"); a1.grid(alpha=.25)
k=np.array([1,2,3]); edge=np.array([71,130,180]); err=np.array([2,2,4])
kk=np.linspace(0,5.4,50); S_=lambda q:55*q+17
a2.plot(kk,50*kk,color="#888",ls=":",label="manual: 50 mV x k")
a2.plot(kk,S_(kk),color="#CC3311",label="fit: 55 mV x k + 17 mV")
a2.errorbar(k,edge,yerr=err,fmt="ks",ms=6,zorder=5,label="measured: where k-channel events vanish")
for q in (4,5): a2.plot(q,S_(q),"o",mfc="white",color="#CC3311",zorder=5)
a2.text(4.5,S_(4.5)+12,"extrapolated",fontsize=7,color="#CC3311",ha="right",rotation=33)
for lv in range(2,7):
    t=50*lv-25; ok=S_(lv-1)<t<S_(lv)
    a2.plot([lv-1,lv],[t,t],color="#117733" if ok else "#AA3377",lw=2.5)
    a2.text(lv-0.5,t+(-14 if ok else 8),f"Majority={lv}: "+("ok" if ok else f"passes with {lv-1}"),fontsize=7.5,ha="center",color="#117733" if ok else "#AA3377",va="top" if ok else "bottom")
a2.text(0.1,330,"horizontal bars: threshold of Majority=N from the manual formula (50N-25 mV).\nCorrect only if it lies between the sum of N-1 and of N channels (red line).",fontsize=7.5,va="top")
a2.set_xlim(0,5.6); a2.set_ylim(0,340); a2.set_xlabel("number of channels over threshold, k"); a2.set_ylabel("current sum S [mV]")
a2.legend(fontsize=8,loc="lower right"); a2.grid(alpha=.25)
fig.tight_layout(); fig.savefig(ROOT + "/measurements/v812_majthr_20261027.png",dpi=130); print("ok")
