"""Da dove vengono gli eventi del WC ad alta carica al LNGS: tre controlli
sulla run 0013 (self-trigger sui soli PMT, offset 100), che distinguono fra
le ipotesi.

    python3 analisi/20261030_eccesso/ipotesi_0013.py F_0013 OUT.png

1. Pannelli: il trigger e' sui soli PMT, quindi un impulso dei SiPM allineato
   col trigger vuol dire che pannelli e WC vedono la stessa cosa nello stesso
   istante. Si confronta l'ampiezza dei SiPM in una finestra in tempo
   (300-480 ns: gli impulsi correlati cadono ~125 ns dopo i PMT per la
   catena, ch18 ~40 ns prima degli altri) con una fuori tempo larga uguale
   (700-880 ns), che da' quanto fa il buio da solo. 1 p.e. = 2.75 / 3.02 /
   2.92 mV a 54 V (run 0039).
2. L'altro PMT: frazione degli eventi in cui il PMT meno illuminato supera
   3 sigma, contro la carica di quello piu' illuminato. Una sorgente locale
   a un PMT che fa arrivare all'altro una frazione fissa f della sua luce
   (lungo le fibre, lette ai due capi) da' 1 - exp(-f Q / K).
3. Il tempo: rate in intervalli di 2 minuti e intervalli fra eventi
   successivi ad alta carica, contro l'esponenziale di un processo casuale.
   Lampi da scariche, luce esterna intermittente o un LED danno grappoli o
   periodicita'.

Classi di eventi dei PMT (cancello 240-500 ns, piedistallo 0-150 ns):
  A  un solo PMT sopra 3 sigma (soprattutto buio appena sopra soglia)
  B  entrambi sopra 3 sigma, carica massima < 7000 ADC*ns
  C  entrambi sopra 3 sigma, carica massima >= 10000 ADC*ns (l'eccesso)
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import h5py
import daqio
import padme

F, OUT = sys.argv[1], sys.argv[2]
DT = 4 / 3
PE_SIPM = np.array([2.75, 3.02, 2.92]) / (1000.0 / 4096)        # conteggi per p.e.
K = json.load(open(_os.path.join(ROOT, "measurements", "mc_digit_K.json")))["K_adcns_per_pe"]

h, c, d, s = padme.leggi(F, [16, 17, 18, 20, 21], None)
n, _, ns = d.shape
t = np.arange(ns) * DT
PED = t < 150
G = (t >= 240) & (t < 500)
ON = (t >= 300) & (t < 480)
OFF = (t >= 700) & (t < 880)

# PMT: impulsi negativi resi positivi
yp = -(d[:, 3:5, :].astype(np.float32) - d[:, 3:5, PED].mean(2, keepdims=True))
q = yp[:, :, G].sum(2) * DT


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


S = np.array([sig(q[:, 0]), sig(q[:, 1])])
sopra = q > 3 * S[None, :]
both = sopra[:, 0] & sopra[:, 1]
qmax = q.max(1)
A = sopra.sum(1) == 1
B = both & (qmax < 7000)
C = both & (qmax >= 10000)
CLASSI = [("A: one PMT > 3σ", A, "#888"), ("B: both > 3σ, max Q < 7000", B, "#2a6fdb"),
          ("C: both > 3σ, max Q ≥ 10000 (excess)", C, "#d62728")]

# SiPM: positivi; base dalla mediana 0-150 ns (regge gli impulsi di buio)
ys = (d[:, 0:3, :].astype(np.float32) - np.median(d[:, 0:3, PED], axis=2, keepdims=True)) / PE_SIPM[None, :, None]
on = ys[:, :, ON].max(2)                 # p.e., massimo per pannello in tempo
off = ys[:, :, OFF].max(2)
on_max, off_max = on.max(1), off.max(1)

# tempo: Trigger Time Tag a 17.07 ns su 31 bit, un giro ogni ~36.7 s. Non 30:
# srotolato a 30 bit la 0013 durava 1213 s contro i 2367 del log e del
# registro del controllore (eventi regolari a 4.2 Hz, nessuna pausa).
# Al rate della run gli intervalli fra eventi stanno sempre sotto un giro.
f, tmp = daqio.open_file(F)
ttt = np.asarray(f["/events/trigger_time_tag"][:n], dtype=np.int64)
f.close()
daqio._cleanup(tmp)
dtt = np.diff(ttt)
dtt[dtt < 0] += 2 ** 31
tempo = np.concatenate([[0.0], np.cumsum(dtt) * 17.07e-9])

print("eventi %d in %.0f s di Trigger Time Tag; sigma ped %.0f %.0f ADC*ns" % (n, tempo[-1], S[0], S[1]))
for nome, m, _ in CLASSI:
    for soglia in (5, 10, 30):
        print("  %-38s n=%5d  SiPM > %2d p.e.: in tempo %5.1f%%  fuori tempo %5.1f%%"
              % (nome, m.sum(), soglia, 100 * np.mean(on_max[m] > soglia), 100 * np.mean(off_max[m] > soglia)))

fig, ax = plt.subplots(2, 3, figsize=(21, 12))
a = ax[0, 0]
for nome, m, col in CLASSI:
    for j, ls in zip(range(3), ("-", "--", ":")):
        a.plot(t, ys[m, j].mean(0), color=col, lw=1.1, ls=ls,
               label="%s, ch%d (%d ev.)" % (nome, 16 + j, m.sum()) if True else None)
a.axvspan(300, 480, color="#e8710a", alpha=0.08, label="on-time window")
a.axvspan(700, 880, color="#888", alpha=0.08, label="off-time window")
a.axvline(259, color="k", lw=0.8, ls="-.", label="PMT pulse (trigger), 259 ns")
a.set_xlim(150, 1000)
a.set_xlabel("time [ns]")
a.set_ylabel("SiPM mean waveform [p.e.]")
a.set_title("1. SiPM panels in events triggered by the WC PMTs only", fontsize=10, loc="left")
a.legend(fontsize=6.5, frameon=False, ncol=1)
a.grid(alpha=0.25)

a = ax[0, 1]
soglie = [2, 5, 10, 20, 30, 50, 100]
for nome, m, col in CLASSI:
    a.plot(soglie, [100 * np.mean(on_max[m] > x) for x in soglie], "o-", color=col, label="%s, on-time" % nome)
    a.plot(soglie, [100 * np.mean(off_max[m] > x) for x in soglie], "o--", color=col, alpha=0.5,
           label="%s, off-time (dark counts)" % nome)
a.set_xscale("log")
a.set_yscale("log")
a.set_xlabel("threshold on the largest SiPM pulse among the 3 panels [p.e.]")
a.set_ylabel("% of events")
a.set_title("1. fraction of WC events with a panel signal: on-time vs off-time", fontsize=10, loc="left")
a.legend(fontsize=7, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[0, 2]
for nome, m, col in CLASSI[1:]:
    a.scatter(on_max[m], q[m].sum(1), s=6, color=col, alpha=0.6, lw=0, label=nome)
a.set_xscale("symlog", linthresh=1)
a.set_yscale("log")
a.set_xlabel("largest SiPM pulse on-time [p.e.]")
a.set_ylabel("PMT charge Q20 + Q21 [ADC·ns]")
a.set_title("1. WC charge vs panel signal, same event", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False, markerscale=3)
a.grid(alpha=0.25, which="both")

a = ax[1, 0]
dom = q.max(1)
deb_sopra = np.where(q[:, 0] >= q[:, 1], sopra[:, 1], sopra[:, 0])
trig = sopra.any(1)
bb = np.array([2000, 3000, 4000, 5000, 6000, 8000, 10000, 13000, 17000, 22000, 30000, 45000, 70000])
bc = np.sqrt(bb[1:] * bb[:-1])
fr, er = [], []
for lo, hi in zip(bb[:-1], bb[1:]):
    m = trig & (dom >= lo) & (dom < hi)
    p = deb_sopra[m].mean() if m.any() else np.nan
    fr.append(p)
    er.append(np.sqrt(p * (1 - p) / max(m.sum(), 1)))
fr, er = np.array(fr), np.array(er)
a.errorbar(bc, fr, yerr=er, fmt="o", color="k", label="data: P(weaker PMT > 3σ)")
ok = np.isfinite(fr) & (er > 0)
best = None
for fv in np.linspace(0.005, 1.0, 400):
    mod = 1 - np.exp(-fv * bc / K)
    chi = np.sum(((fr - mod) / np.maximum(er, 0.02))[ok] ** 2)
    if best is None or chi < best[0]:
        best = (chi, fv)
xx = np.logspace(np.log10(2000), np.log10(70000), 200)
a.plot(xx, 1 - np.exp(-best[1] * xx / K), color="#e8710a",
       label="1 − exp(−f·Q/K), f = %.2f (light shared at a fixed fraction)" % best[1])
a.set_xscale("log")
a.set_ylim(0, 1.05)
a.set_xlabel("charge of the brighter PMT [ADC·ns]  (K = %.0f ADC·ns/PE)" % K)
a.set_ylabel("probability that the other PMT is > 3σ")
a.set_title("2. does the other PMT light up? vs the charge of the first", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1, 1]
tb = np.arange(0, tempo[-1] + 120, 120)
for nome, m, col in CLASSI:
    hh = np.histogram(tempo[m], tb)[0] / 120.0
    a.errorbar(0.5 * (tb[1:] + tb[:-1]) / 60, hh, yerr=np.sqrt(hh * 120) / 120, fmt="o-", ms=3, color=col,
               lw=0.8, label=nome)
a.set_yscale("log")
a.set_xlabel("time from the start of the run [min]")
a.set_ylabel("rate [Hz], 2-minute bins")
a.set_title("3. rate vs time within the run", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1, 2]
tc = tempo[C]
iv = np.diff(tc)
r = len(tc) / tempo[-1]
b2 = np.logspace(-2, np.log10(max(iv.max(), 1) * 1.2), 40)
a.hist(iv, b2, histtype="step", color="#d62728", lw=1.5, label="class C: %d intervals" % len(iv))
cen = np.sqrt(b2[1:] * b2[:-1])
a.plot(cen, len(iv) * (np.exp(-r * b2[:-1]) - np.exp(-r * b2[1:])), color="k", ls="--",
       label="random (Poisson) at %.3f Hz" % r)
a.set_xscale("log")
a.set_xlabel("time between consecutive class-C events [s]")
a.set_ylabel("intervals / bin")
a.set_title("3. are the excess events clustered in time?", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")
fig.suptitle("LNGS 0013 (WC self-trigger on the PMTs only, offset 100): checks on the origin of the high-charge "
             "events", fontsize=12)
fig.tight_layout()
fig.savefig(OUT, dpi=90)
print("f (luce condivisa) = %.2f; classe C %.3f Hz; corto (<1 s) %d intervalli su %d, atteso %.1f"
      % (best[1], r, (iv < 1).sum(), len(iv), len(iv) * (1 - np.exp(-r * 1.0))))
