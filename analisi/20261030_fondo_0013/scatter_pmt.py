"""Carica di ch20 contro carica di ch21, run 0013, con le selezioni in uso.

    python3 analisi/20261030_fondo_0013/scatter_pmt.py OUT.png

Selezioni: trigger hardware = self-trigger in OR su ch20/ch21 (offset 100,
nei dati ~300 conteggi d'ampiezza, ~3800-4000 ADC*ns di carica); taglio
offline = entrambe le cariche sopra 3 sigma del piedistallo. Cariche dal
.npz di tools/carica_padme.py (cancello 240-500 ns, piedistallo 0-150 ns,
solo correzione CAEN), in valore assoluto.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = sys.argv[1]
r = np.load(_os.path.join(ROOT, "measurements", "carica_0013_testbeam.npz"))
a, b = r["q_caen_ch20"], r["q_caen_ch21"]
Q_TRIG = 3900.0      # carica corrispondente alla soglia del trigger (bordo misurato), indicativa


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s
        m, s = x[k].mean(), x[k].std()
    return s


sa, sb = sig(a), sig(b)
both = (a > 3 * sa) & (b > 3 * sb)
print("eventi %d, entrambi > 3 sigma %d; sigma %.0f %.0f" % (len(a), both.sum(), sa, sb))

fig, ax = plt.subplots(1, 2, figsize=(17, 8))
for k, (lim, tit) in enumerate(((60000, "full range"), (12000, "zoom 0-12000"))):
    p = ax[k]
    p.scatter(a[~both], b[~both], s=3, color="#bbb", alpha=0.5, lw=0,
              label="not selected (one PMT ≤ 3σ): %d" % (~both).sum())
    p.scatter(a[both], b[both], s=5, color="k", alpha=0.6, lw=0,
              label="SELECTED: both PMT > 3σ_ped: %d" % both.sum())
    p.axvline(3 * sa, color="#e8710a", lw=1, ls="--", label="3σ_ped ch20 = %.0f" % (3 * sa))
    p.axhline(3 * sb, color="#c23b5a", lw=1, ls="--", label="3σ_ped ch21 = %.0f" % (3 * sb))
    p.axvline(Q_TRIG, color="#2a6fdb", lw=1, ls=":", label="≈ trigger threshold (offset 100) in charge, ~%.0f" % Q_TRIG)
    p.axhline(Q_TRIG, color="#2a6fdb", lw=1, ls=":")
    p.plot([0, lim], [0, lim], color="#888", lw=0.8, label="ch20 = ch21")
    p.set_xlim(-1000, lim)
    p.set_ylim(-1000, lim)
    p.set_aspect("equal")
    p.ticklabel_format(style="plain")
    p.set_xlabel("ch20 charge, absolute value [ADC·ns]")
    p.set_ylabel("ch21 charge, absolute value [ADC·ns]")
    p.set_title("%s" % tit, fontsize=10, loc="left")
    p.grid(alpha=0.25)
ax[0].legend(fontsize=8, frameon=False, loc="upper left", markerscale=3)
fig.suptitle("LNGS 0013 (PMT self-trigger in OR, offset 100): charge ch20 vs ch21, %d events in 2367 s "
             "(gate 240-500 ns, pedestal 0-150 ns)" % len(a), fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
