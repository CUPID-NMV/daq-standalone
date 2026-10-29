"""Il picco a ~4000 ADC*ns negli eventi della 0013 con UN solo PMT acceso:
fotoelettrone singolo dei dark count o altro?

    python3 analisi/20261030_fondo_0013/picco_singolo.py F_0013 OUT.png

Eventi "solo chX": carica di chX sopra 3 sigma del piedistallo, l'altro PMT
sotto. Per questi: carica, ampiezza contro carica, larghezza a meta'
altezza, e forme d'onda prese attorno al picco (polarita' vera).
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
y = -(d.astype(float) - d[:, :, t < 150].mean(2, keepdims=True))     # impulsi resi positivi per i conti
g = (t >= 240) & (t < 500)
q = y[:, :, g].sum(2) * dt
amp = y[:, :, g].max(2)
B = np.linspace(0, 20000, 101)
BC = 0.5 * (B[1:] + B[:-1])


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


def fwhm(w):
    p = w.argmax()
    a, b = p, p
    while a > 0 and w[a] > 0.5 * w[p]:
        a -= 1
    while b < len(w) - 1 and w[b] > 0.5 * w[p]:
        b += 1
    return (b - a) * dt


S = np.array([sig(q[:, 0]), sig(q[:, 1])])
sopra = q > 3 * S[None, :]
entrambi = sopra[:, 0] & sopra[:, 1]
COL = {20: "#e8710a", 21: "#c23b5a"}

fig, ax = plt.subplots(4, 4, figsize=(19, 15))
for j, ch in enumerate((20, 21)):
    solo = sopra[:, j] & ~sopra[:, 1 - j]
    pk = BC[np.histogram(q[solo, j], B)[0].argmax()]
    vicino = np.where(solo & (np.abs(q[:, j] - pk) < 0.15 * pk))[0]
    w = [fwhm(y[e, j, g]) for e in vicino]
    print("ch%d: solo %d eventi su %d, picco %.0f ADC*ns, al picco ampiezza mediana %.0f conteggi, FWHM mediana %.1f ns"
          % (ch, solo.sum(), len(q), pk, np.median(amp[vicino, j]), np.median(w)))

    a = ax[0, 2 * j]
    a.hist(q[solo, j], B, histtype="step", color="k", lw=1.4, label="only ch%d > 3σ: %d ev." % (ch, solo.sum()))
    a.hist(q[entrambi, j], B, histtype="step", color=COL[ch], lw=1.2, label="both > 3σ: %d ev." % entrambi.sum())
    a.axvline(pk, color="#2a6fdb", ls="--", lw=1, label="peak of 'only' ~%.0f ADC·ns" % pk)
    a.set_xlabel("ch%d charge, absolute value [ADC·ns]" % ch)
    a.set_ylabel("events / bin")
    a.set_title("ch%d charge" % ch, fontsize=10, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25)

    a = ax[0, 2 * j + 1]
    a.hist2d(amp[solo, j], q[solo, j], bins=[np.linspace(0, 600, 80), B[::2]], cmap="viridis",
             norm=matplotlib.colors.LogNorm())
    a.set_xlabel("ch%d amplitude, absolute value [counts]" % ch)
    a.set_ylabel("ch%d charge [ADC·ns]" % ch)
    a.set_title("only ch%d: amplitude vs charge (FWHM at the peak: %.1f ns)" % (ch, np.median(w)),
                fontsize=10, loc="left")

    for k, e in enumerate(vicino[:6]):
        a = ax[1 + (6 * j + k) // 4, (6 * j + k) % 4]
        a.plot(t, -y[e, j], color=COL[ch], lw=0.9, label="ch%d" % ch)
        a.plot(t, -y[e, 1 - j], color="#999", lw=0.6, label="ch%d (other PMT)" % (21 if ch == 20 else 20))
        a.axvspan(240, 500, color="#f2f2f2", zorder=0)
        a.set_xlim(150, 700)
        a.set_title("ev %d, only ch%d: Q = %.0f ADC·ns, A = -%.0f, FWHM %.1f ns"
                    % (e, ch, q[e, j], amp[e, j], fwhm(y[e, j, g])), fontsize=8.5, loc="left")
        a.set_xlabel("time [ns]")
        a.set_ylabel("ADC − pedestal [counts]")
        a.legend(fontsize=7, frameon=False, loc="lower right")
        a.grid(alpha=0.25)
fig.suptitle("LNGS 0013 (PMT self-trigger offset 100): events with ONE PMT above 3σ and the peak at ~4000 ADC·ns. "
             "Grey band: gate 240-500 ns", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=90)
