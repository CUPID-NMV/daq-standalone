"""Differenza di tempo fra gli impulsi dei due PMT, run 0013, per gli eventi
con entrambe le cariche sopra 3 sigma del piedistallo.

    python3 analisi/20261030_fondo_0013/dt_pmt.py F_0013 OUT.png

Tempo di ogni impulso: passaggio al 50% del suo massimo (frazione costante,
non soglia fissa, cosi' impulsi piccoli e grandi si confrontano senza il
ritardo di salita dovuto all'ampiezza), interpolato fra i due campioni,
cercato nel cancello 240-500 ns. dt = t(ch20) - t(ch21).
Cariche come in tools/carica_padme.py: cancello 240-500 ns, piedistallo
0-150 ns, solo correzione CAEN.
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
import padme

F, OUT = sys.argv[1], sys.argv[2]
Q_TRIG = 3900.0
h, c, d, s = padme.leggi(F, [20, 21], None)
dt = 4 / 3
t = np.arange(d.shape[2]) * dt
y = -(d.astype(float) - d[:, :, t < 150].mean(2, keepdims=True))      # impulsi resi positivi
g = (t >= 240) & (t < 500)
tg = t[g]
yg = y[:, :, g]
q = yg.sum(2) * dt
amp = yg.max(2)


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


S = np.array([sig(q[:, 0]), sig(q[:, 1])])
both = (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])


def t50(w):
    """Primo passaggio al 50% del massimo, risalendo dal picco, interpolato."""
    p = int(w.argmax())
    soglia = 0.5 * w[p]
    i = p
    while i > 0 and w[i - 1] >= soglia:
        i -= 1
    if i == 0:
        return np.nan
    return tg[i - 1] + (soglia - w[i - 1]) / (w[i] - w[i - 1]) * dt


ev = np.where(both)[0]
T = np.array([[t50(yg[e, 0]), t50(yg[e, 1])] for e in ev])
DT = T[:, 0] - T[:, 1]
qa, qb = q[ev, 0], q[ev, 1]
forti = (qa > Q_TRIG) & (qb > Q_TRIG)            # entrambi oltre la soglia del trigger
alti = np.maximum(qa, qb) > 20000                # oltre il massimo dei muoni
print("selezionati %d; entrambi > %.0f: %d; max > 20000: %d" % (len(ev), Q_TRIG, forti.sum(), alti.sum()))
for nome, m in (("tutti", np.ones(len(ev), bool)), ("entrambi > soglia trigger", forti), ("max > 20000", alti)):
    x = DT[m & np.isfinite(DT)]
    print("%-28s n=%4d  mediana %.1f ns  rms %.1f ns  frazione |dt|<10 ns %.2f"
          % (nome, len(x), np.median(x), x.std(), np.mean(np.abs(x) < 10)))

fig, ax = plt.subplots(2, 2, figsize=(17, 11))
b = np.linspace(-150, 150, 151)
a = ax[0, 0]
a.hist(DT, b, histtype="step", color="k", lw=1.5, label="all selected (both > 3σ): %d" % len(ev))
a.hist(DT[forti], b, histtype="step", color="#2a6fdb", lw=1.3,
       label="both above the trigger threshold (Q > %.0f): %d" % (Q_TRIG, forti.sum()))
a.hist(DT[alti], b, histtype="step", color="#d62728", lw=1.3, label="max(Q) > 20000 ADC·ns: %d" % alti.sum())
a.set_yscale("log")
a.set_xlabel("Δt = t(ch20) − t(ch21) [ns]  (50% of each pulse's maximum)")
a.set_ylabel("events / 2 ns")
a.set_title("time difference between the two PMT pulses", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[0, 1]
b2 = np.linspace(-30, 30, 121)
a.hist(DT, b2, histtype="step", color="k", lw=1.5, label="all selected")
a.hist(DT[forti], b2, histtype="step", color="#2a6fdb", lw=1.3, label="both > trigger threshold")
a.hist(DT[alti], b2, histtype="step", color="#d62728", lw=1.3, label="max(Q) > 20000")
a.set_xlabel("Δt [ns]")
a.set_ylabel("events / 0.5 ns")
a.set_title("zoom ±30 ns", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25)

a = ax[1, 0]
lr = np.log(qa / qb)
sc = a.scatter(lr, DT, s=6, c=np.log10(np.maximum(qa, qb)), cmap="viridis", lw=0)
plt.colorbar(sc, ax=a, label="log10 max(Q) [ADC·ns]")
a.set_ylim(-60, 60)
a.set_xlabel("ln(Q20 / Q21)   (> 0: more light on ch20)")
a.set_ylabel("Δt = t(ch20) − t(ch21) [ns]")
a.set_title("Δt vs charge asymmetry", fontsize=10, loc="left")
a.grid(alpha=0.25)

a = ax[1, 1]
a.scatter(np.minimum(qa, qb), DT, s=6, color="k", lw=0, alpha=0.6)
a.set_xscale("log")
a.set_ylim(-60, 60)
a.set_xlabel("min(Q20, Q21) [ADC·ns]  (the smaller of the two pulses)")
a.set_ylabel("Δt [ns]")
a.set_title("Δt vs the smaller charge: small pulses = poor timing", fontsize=10, loc="left")
a.grid(alpha=0.25, which="both")
fig.suptitle("LNGS 0013: time difference between the two PMT pulses, events with both charges > 3σ_ped", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
