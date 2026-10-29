"""Forme d'onda dei due PMT per gli eventi della run da 24 ore con dt fra +32 e
+44 ns (il gruppo di dt_asimmetria_24h.py), accanto ad alcuni eventi con dt
vicino a zero.

    python3 analisi/20261029_geometria_pannelli/forme_dt_24h.py F_24H OUT.png

La domanda: il ritardo di ch20 e' un ritardo vero della luce, o il massimo di
ch20 cade su un secondo impulso e il passaggio al 50% (cercato risalendo dal
massimo) si sposta con lui? Per distinguerlo, oltre al tempo al 50% si misura
il primo istante in cui l'impulso supera il 20% del suo massimo, cercato in
AVANTI dall'inizio del cancello: se c'e' una componente precoce, cade molto
prima. PMT in polarita' vera (negativi), in mV (1 Vpp su 12 bit).
Gira dove sono i dati (PC DAQ).
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import padme

F, OUT = sys.argv[1], sys.argv[2]
GRUPPO = [5, 6, 12, 18, 31, 34]
DT = 4 / 3
MV = 1000.0 / 4096
h, c, d, s = padme.leggi(F, [20, 21], None, serve_sic=False)
t = np.arange(d.shape[2]) * DT
y = -(d.astype(float) - d[:, :, t < 150].mean(2, keepdims=True))     # positivi per i conti
g = (t >= 240) & (t < 500)
tg = t[g]
q = y[:, :, g].sum(2) * DT


def t50(w):
    p = int(w.argmax())
    soglia = 0.5 * w[p]
    i = p
    while i > 0 and w[i - 1] >= soglia:
        i -= 1
    return np.nan if i == 0 else tg[i - 1] + (soglia - w[i - 1]) / (w[i] - w[i - 1]) * DT


def t_primo(w, frazione=0.2):
    """Primo superamento in avanti di una frazione del massimo, interpolato."""
    soglia = frazione * w.max()
    i = int(np.argmax(w >= soglia))
    return tg[0] if i == 0 else tg[i - 1] + (soglia - w[i - 1]) / (w[i] - w[i - 1]) * DT


T50 = np.array([[t50(y[e, j, g]) for j in (0, 1)] for e in range(len(q))])
TP = np.array([[t_primo(y[e, j, g]) for j in (0, 1)] for e in range(len(q))])
dt = T50[:, 0] - T50[:, 1]
S = np.array([239.4, 234.5])                 # sigma della carica, run 0013
both = (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])
cand = [e for e in range(len(q)) if both[e] and e not in (17, 24) and e not in GRUPPO and abs(dt[e]) < 5]
# riferimento: i tre con carica piu' simile a quella del gruppo (ch21 dominante)
cand.sort(key=lambda e: abs(np.log(q[e, 0] / q[e, 1]) - np.median(np.log(q[GRUPPO, 0] / q[GRUPPO, 1]))))
RIF = sorted(cand[:3])
print("evento  Q20    Q21   t50(20) t50(21)  dt50 | primo 20%%: t(20)  t(21)  dt | t50 - primo20%%: ch20  ch21")
for e in GRUPPO + RIF:
    print("%3d   %6.0f %6.0f   %6.1f %6.1f %+6.1f |        %6.1f %6.1f %+6.1f |               %5.1f %5.1f %s"
          % (e, q[e, 0], q[e, 1], T50[e, 0], T50[e, 1], dt[e], TP[e, 0], TP[e, 1], TP[e, 0] - TP[e, 1],
             T50[e, 0] - TP[e, 0], T50[e, 1] - TP[e, 1], "riferimento" if e in RIF else ""))

fig, ax = plt.subplots(3, 3, figsize=(19, 13), sharex=True)
for a, e in zip(ax.ravel(), GRUPPO + RIF):
    for j, (ch, col) in enumerate(((20, "#d62728"), (21, "#2a6fdb"))):
        a.plot(t, -y[e, j] * MV, color=col, lw=1.0, label="ch%d: Q = %.0f ADC·ns (%.1f PE)" % (ch, q[e, j], q[e, j] / 1696))
        a.axvline(T50[e, j], color=col, ls="--", lw=1.1)
        a.axvline(TP[e, j], color=col, ls=":", lw=1.1)
    gruppo = e in GRUPPO
    a.set_title("event %d%s:  Δt(50%%) = %+.1f ns,  Δt(first 20%%) = %+.1f ns"
                % (e, "" if gruppo else " (reference, Δt ≈ 0)", dt[e], TP[e, 0] - TP[e, 1]),
                fontsize=10, loc="left", color="k" if gruppo else "#555")
    a.set_xlim(300, 480)
    a.axhline(0, color="#999", lw=0.6)
    a.legend(fontsize=8.5, frameon=False, loc="lower right")
    a.grid(alpha=0.25)
for a in ax[-1]:
    a.set_xlabel("time in the trace [ns]")
for a in ax[:, 0]:
    a.set_ylabel("PMT signal [mV]  (true polarity)")
fig.suptitle("24-hour run: the six events with Δt = t(ch20) − t(ch21) between +32 and +44 ns (top two rows) and three "
             "references with Δt ≈ 0 (bottom row)\ndashed: 50% of each pulse's maximum (the Δt of the scatter plot); "
             "dotted: first crossing of 20% of the maximum, searched forward", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
print("scritto", OUT)
