"""Forma media degli impulsi dei due PMT nella 0013, per classi di asimmetria
di carica ln(Q20/Q21), sugli eventi con entrambe le cariche sopra 3 sigma.

    python3 analisi/20261030_fondo_0013/forma_asimmetria.py F_0013 OUT.png

Classi: ch20 dominante (ln > 1.5), simmetrici (|ln| < 0.3), ch21 dominante
(ln < -1.5); in piu' gli eventi con carica massima > 20000 ADC*ns e, come
riferimento, gli eventi con UN solo PMT sopra 3 sigma (attivita' locale del
PMT, ~3.6 Hz).

Sopra: medie in conteggi, allineate al trigger (come sono registrate).
Sotto: forme normalizzate al picco e allineate sul passaggio al 50% di ogni
impulso, per confrontare la FORMA indipendentemente dall'ampiezza e dal
tempo. Polarita' vera (impulsi negativi).
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
h, c, d, s = padme.leggi(F, [20, 21], None)
dt = 4 / 3
t = np.arange(d.shape[2]) * dt
y = -(d.astype(float) - d[:, :, t < 150].mean(2, keepdims=True))     # positivi per i conti
g = (t >= 240) & (t < 500)
q = y[:, :, g].sum(2) * dt


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
lr = np.full(len(q), np.nan)
lr[both] = np.log(q[both, 0] / q[both, 1])
CLASSI = [
    ("ch20 dominant, ln(Q20/Q21) > 1.5", both & (lr > 1.5), "#e8710a"),
    ("symmetric, |ln(Q20/Q21)| < 0.3", both & (np.abs(lr) < 0.3), "k"),
    ("ch21 dominant, ln(Q20/Q21) < -1.5", both & (lr < -1.5), "#c23b5a"),
    ("max(Q) > 20000 ADC·ns", both & (q.max(1) > 20000), "#2a6fdb"),
]
SOLO = [sopra[:, 0] & ~sopra[:, 1], sopra[:, 1] & ~sopra[:, 0]]

# forme allineate sul 50% di ogni impulso, normalizzate al picco
FIN = np.arange(-40, 400, dt)


def allineata(w):
    p = int(w[g].argmax()) + int(np.argmax(g))
    so = 0.5 * w[p]
    i = p
    while i > 0 and w[i - 1] >= so:
        i -= 1
    t0 = t[i - 1] + (so - w[i - 1]) / (w[i] - w[i - 1]) * dt if i > 0 else t[p]
    return np.interp(FIN + t0, t, w / w[p], left=np.nan, right=np.nan)


def forma(sel, j):
    return np.nanmean([allineata(y[e, j]) for e in np.where(sel)[0]], axis=0)


def descr(f):
    """FWHM e frazione dell'area oltre 50 ns dal fronte, sulla forma media."""
    k = np.isfinite(f)
    ff, xx = f[k], FIN[k]
    sopra_ = ff >= 0.5
    fw = (xx[sopra_].max() - xx[sopra_].min()) if sopra_.any() else np.nan
    coda = ff[xx > 50].sum() / ff[ff > 0].sum()
    return fw, coda


fig, ax = plt.subplots(2, 2, figsize=(18, 11))
for j, ch in enumerate((20, 21)):
    a = ax[0, j]
    for nome, sel, col in CLASSI:
        a.plot(t, -y[sel, j].mean(0), color=col, lw=1.2, label="%s: %d ev." % (nome, sel.sum()))
    a.plot(t, -y[SOLO[j], j].mean(0), color="#888", lw=1, ls="--",
           label="only ch%d > 3σ (PMT-local, ref.): %d ev." % (ch, SOLO[j].sum()))
    a.set_xlim(200, 900)
    a.set_xlabel("time [ns]")
    a.set_ylabel("ch%d: ADC − pedestal, mean [counts]" % ch)
    a.set_title("ch%d — mean waveform per class (as recorded, aligned to the trigger)" % ch, fontsize=10, loc="left")
    a.legend(fontsize=7.8, frameon=False, loc="lower right")
    a.grid(alpha=0.25)

    a = ax[1, j]
    print("ch%d" % ch)
    for nome, sel, col in CLASSI + [("only ch%d (PMT-local)" % ch, SOLO[j], "#888")]:
        f = forma(sel, j)
        fw, coda = descr(f)
        print("  %-36s n=%5d  FWHM forma media %.1f ns  area oltre 50 ns %.0f%%" % (nome, sel.sum(), fw, 100 * coda))
        a.plot(FIN, -f, color=col, lw=1.2, ls="--" if col == "#888" else "-",
               label="%s: FWHM %.1f ns, area after 50 ns %.0f%%" % (nome, fw, 100 * coda))
    a.set_xlim(-30, 300)
    a.set_xlabel("time from the 50% crossing of each pulse [ns]")
    a.set_ylabel("ch%d: normalised to the peak" % ch)
    a.set_title("ch%d — mean SHAPE (each pulse aligned on its 50%% crossing and normalised)" % ch,
                fontsize=10, loc="left")
    a.legend(fontsize=7.6, frameon=False, loc="lower right")
    a.grid(alpha=0.25)
fig.suptitle("LNGS 0013: mean PMT pulse for classes of charge asymmetry (events with both charges > 3σ_ped); "
             "true polarity", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
