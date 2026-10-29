"""Forma degli impulsi dei PMT a parita' d'ampiezza: eccesso LNGS contro muoni
di Frascati. Verifica dell'ipotesi della scintillazione del nucleo delle
fibre BCF-9995XL (polistirene, ~1750 fotoni/MeV a 324 nm, assorbiti dal
colorante della fibra che assorbe fra 280 e 390 nm).

    python3 analisi/20261029_scint_fibre/forma_coda.py F_0013 F_0005 OUT.png

La luce del polistirene ha componenti lente: 2.26 ns (60%), 11.8 ns (23%),
24.8 ns (17%) (polistirene non drogato, arXiv 2605.12861): il ~12% arriva dopo
20 ns. Il Cherenkov nell'acqua (i muoni) e' pronto, salvo i percorsi nel tank
e i 2.7 ns del colorante. Se l'eccesso viene dalle fibre, i suoi impulsi
devono avere piu' coda dei muoni ALLA STESSA AMPIEZZA: l'ampiezza conta
perche' gli impulsi della catena si allargano anche da soli salendo d'ampiezza.

Gruppi (cariche e classi come in analisi/20261030_eccesso/ipotesi_0013.py):
  LNGS C    0013, entrambi i PMT > 3 sigma e carica massima >= 10000 ADC*ns:
            il PMT dominante (l'eccesso)
  LNGS B    0013, entrambi > 3 sigma e carica massima < 7000: il dominante
  LNGS A    0013, un solo PMT > 3 sigma: impulsi locali del PMT, la risposta
            piu' veloce che abbiamo (riferimento)
  LNF mu    Frascati 0005, tre SiPM sopra soglia: i due PMT

Per ogni impulso (verso reso positivo, piedistallo da 180 a 40 ns prima del
picco): ampiezza A; t50, il passaggio al 50% sul fronte, interpolato; FWHM;
f20 e f40 = frazione della carica in [t50+20, t50+100] e [t50+40, t50+100] ns
sul totale in [t50-10, t50+100] ns. Esclusi gli impulsi saturi (ADC a 0).

Previsioni: la forma del singolo impulso estratta dalla 0013
(measurements/libreria_digit_0013.npz, impulsi veloci di un solo PMT) presa
come risposta R, convoluta col colorante (2.7 ns) e con colorante +
polistirene; f20, f40 e FWHM calcolati allo stesso modo.
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

F13, F05, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
MIS = _os.path.join(ROOT, "measurements")
DT = 4 / 3
BINS_A = np.array([300, 500, 800, 1200, 2000, 2800])
GRIGLIA = np.arange(-20, 120, 0.5)          # ns dal t50, per le forme medie
PS = [(0.60, 2.26), (0.23, 11.81), (0.17, 24.76)]
TAU_COLORANTE = 2.7


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


def misura(w, t, i0, i1):
    """w: traccia (positiva, piedistallo NON tolto) di un impulso. Ritorna
    (A, t50, fwhm, f20, f40, forma sulla GRIGLIA) oppure None."""
    dt = t[1] - t[0]
    p = i0 + int(np.argmax(w[i0:i1]))
    b0 = max(0, p - int(180 / dt))
    b1 = max(b0 + 5, p - int(40 / dt))
    y = w - w[b0:b1].mean()
    A = y[p]
    if A <= 0:
        return None
    h = 0.5 * A
    i = p
    while i > 0 and y[i - 1] >= h:
        i -= 1
    if i == 0:
        return None
    t50 = t[i - 1] + (h - y[i - 1]) / (y[i] - y[i - 1]) * dt
    j = p
    while j < len(y) - 1 and y[j + 1] >= h:
        j += 1
    if j >= len(y) - 1:
        return None
    t50b = t[j] + (y[j] - h) / (y[j] - y[j + 1]) * dt
    tot = (t >= t50 - 10) & (t < t50 + 100)
    q = y[tot].sum()
    if q <= 0:
        return None
    f20 = y[(t >= t50 + 20) & (t < t50 + 100)].sum() / q
    f40 = y[(t >= t50 + 40) & (t < t50 + 100)].sum() / q
    forma = np.interp(GRIGLIA + t50, t, y / A)
    return A, t50, t50b - t50, f20, f40, forma


def raccogli(d, quali, t, i0, i1):
    """d: eventi x canali x campioni (int16); quali: lista (evento, canale)."""
    out = []
    for e, j in quali:
        raw = d[e, j]
        if raw[i0:i1].min() <= 2:                    # saturo: l'ADC tocca lo zero
            continue
        r = misura(-raw.astype(float), t, i0, i1)
        if r is not None:
            out.append(r)
    if not out:
        return None
    A, t50, fw, f20, f40, forme = map(np.array, zip(*out))
    return dict(A=A, fwhm=fw, f20=f20, f40=f40, forme=forme)


# --- LNGS 0013 ---
h, c, d13, s = padme.leggi(F13, [20, 21], None)
t13 = np.arange(d13.shape[2]) * DT
q = np.load(_os.path.join(MIS, "carica_0013_testbeam.npz"))
q13 = np.stack([q["q_caen_ch20"], q["q_caen_ch21"]], 1)
S = np.array([sig(q13[:, 0]), sig(q13[:, 1])])
sopra = q13 > 3 * S[None, :]
both = sopra.all(1)
dom = q13.argmax(1)
qmax = q13.max(1)
i0, i1 = np.searchsorted(t13, 230), np.searchsorted(t13, 330)
G = {}
G["LNGS C: excess (both > 3σ, max Q ≥ 10000), dominant PMT"] = raccogli(
    d13, [(e, dom[e]) for e in np.where(both & (qmax >= 10000))[0]], t13, i0, i1)
G["LNGS B: both > 3σ, max Q < 7000, dominant PMT"] = raccogli(
    d13, [(e, dom[e]) for e in np.where(both & (qmax < 7000))[0]], t13, i0, i1)
G["LNGS A: one PMT only (PMT-local, fastest reference)"] = raccogli(
    d13, [(e, int(np.argmax(sopra[e]))) for e in np.where(sopra.sum(1) == 1)[0]], t13, i0, i1)
del d13

# --- Frascati 0005: muoni, tre SiPM sopra soglia ---
sel = np.load(_os.path.join(MIS, "frascati_0005_sel_3sipm_500k.npy"))
h, c, d05, s = padme.leggi(F05, [24, 26], None, serve_sic=False)
t05 = np.arange(d05.shape[2]) * DT
i0, i1 = np.searchsorted(t05, 310), np.searchsorted(t05, 410)
ev = np.where(sel)[0]
G["LNF muons (3 SiPM), both PMTs"] = raccogli(d05, [(e, j) for e in ev for j in (0, 1)], t05, i0, i1)
del d05

# --- previsioni dalla risposta R ---
lib = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))
xf = lib["forma_x_ns"]
R = lib["forma_ch20"]
passo = float(lib["fine_ns"])
tk = np.arange(0, 200, passo)
k_col = np.exp(-tk / TAU_COLORANTE)
k_ps = sum(f * np.exp(-tk / tau) / tau for f, tau in PS)
PREV = {"R (fast reference)": R,
        "R ⊗ WLS dye 2.7 ns: prompt light, e.g. Cherenkov": np.convolve(R, k_col)[:len(R)],
        "R ⊗ dye ⊗ polystyrene: fibre scintillation": np.convolve(np.convolve(R, k_col)[:len(R)], k_ps)[:len(R)]}
# le curve previste si misurano come i dati, su una traccia allungata con
# zeri davanti perche' la finestra del piedistallo (180-40 ns prima del
# picco) cada sul vuoto
TX = np.arange(-220, 300, passo)
prev = {}
for nome, w in PREV.items():
    yy = np.interp(TX, xf, w / w.max(), left=0.0, right=0.0)
    r = misura(yy, TX, np.searchsorted(TX, -10), np.searchsorted(TX, 60))
    prev[nome] = r
    print("previsione %-55s FWHM %.1f ns  f20 %.3f  f40 %.3f" % (nome, r[2], r[3], r[4]))

COL = {"LNGS C": "#d62728", "LNGS B": "#2a6fdb", "LNGS A": "#888", "LNF mu": "k"}
fig, ax = plt.subplots(2, 3, figsize=(20, 11))
bc = np.sqrt(BINS_A[1:] * BINS_A[:-1])
print("%-60s %-12s %6s %8s %7s %7s" % ("gruppo", "ampiezza", "n", "FWHM", "f20", "f40"))
for nome, g in G.items():
    if g is None:
        continue
    col = COL[nome[:6]]
    for k, (oss, a) in enumerate((("fwhm", ax[1, 0]), ("f20", ax[1, 1]), ("f40", ax[1, 2]))):
        m, e = [], []
        for lo, hi in zip(BINS_A[:-1], BINS_A[1:]):
            s_ = (g["A"] >= lo) & (g["A"] < hi)
            if s_.sum() >= 10:
                m.append(np.median(g[oss][s_]))
                e.append(1.2533 * g[oss][s_].std() / np.sqrt(s_.sum()))
                if k == 0:
                    print("%-60s %4d-%-7d %6d %8.2f %7.3f %7.3f" % (nome, lo, hi, s_.sum(), np.median(g["fwhm"][s_]),
                                                                      np.median(g["f20"][s_]), np.median(g["f40"][s_])))
            else:
                m.append(np.nan)
                e.append(np.nan)
        a.errorbar(bc, m, yerr=e, fmt="o-", color=col, ms=5, capsize=3, label="%s (%d pulses)" % (nome, len(g["A"])))
    s_ = (g["A"] >= 800) & (g["A"] < 1200)
    if s_.sum() >= 10:
        f = g["forme"][s_].mean(0)
        ax[0, 0].plot(GRIGLIA, -f, color=col, lw=1.4, label="%s: %d pulses" % (nome, s_.sum()))
        ax[0, 1].plot(GRIGLIA, -f, color=col, lw=1.4)
for (nome, w), ls in zip(PREV.items(), (":", "--", "-.")):
    for a in ax[0, :2]:
        a.plot(GRIGLIA, -prev[nome][5], color="#e8710a", lw=1.2, ls=ls,
               label="prediction: %s" % nome if a is ax[0, 0] else None)
    for k, a in enumerate((ax[1, 0], ax[1, 1], ax[1, 2])):
        a.axhline(prev[nome][2 + k], color="#e8710a", lw=1.2, ls=ls,
                  label="prediction: %s" % nome.split(":")[0] if k == 0 else None)
ax[0, 0].set_title("mean pulse shape, amplitude 800-1200 counts (aligned on the 50% crossing)", fontsize=10, loc="left")
ax[0, 1].set_title("same, zoom on the tail", fontsize=10, loc="left")
ax[0, 1].set_ylim(-0.25, 0.03)
ax[0, 1].set_xlim(5, 110)
for a in ax[0, :2]:
    a.set_xlabel("time from the 50% crossing [ns]")
    a.set_ylabel("ADC − pedestal, normalised to the peak (true polarity)")
    a.grid(alpha=0.25)
ax[0, 0].legend(fontsize=7.5, frameon=False, loc="lower right")
ax[0, 2].axis("off")
ax[0, 2].text(0, 1, "What is compared\n\n"
              "Fibre scintillation (BCF-9995XL core, polystyrene):\n"
              "  emission 2.26 ns (60%), 11.8 ns (23%), 24.8 ns (17%)\n"
              "  -> ~12% of the light after 20 ns\n\n"
              "Water Cherenkov (LNF muons): prompt,\n"
              "  + paths in the tank + 2.7 ns WLS dye\n\n"
              "Same amplitude bins for all groups: the\n"
              "chain itself broadens larger pulses.\n\n"
              "f20, f40: fraction of the charge in\n"
              "  [t50+20, t50+100] and [t50+40, t50+100] ns\n"
              "  over [t50-10, t50+100] ns", va="top", fontsize=10, family="monospace")
for a, tit in ((ax[1, 0], "FWHM [ns]"), (ax[1, 1], "f20: charge fraction after +20 ns"),
               (ax[1, 2], "f40: charge fraction after +40 ns")):
    a.set_xscale("log")
    a.set_xlabel("pulse amplitude [counts]  (1 count = 0.244 mV)")
    a.set_ylabel(tit)
    a.set_title(tit + " vs amplitude (median per bin)", fontsize=10, loc="left")
    a.grid(alpha=0.25, which="both")
ax[1, 0].legend(fontsize=7, frameon=False)
fig.suptitle("Is the LNGS excess slower than the LNF muons at equal amplitude? (fibre scintillation test)", fontsize=12)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
np.savez_compressed(_os.path.join(MIS, "forma_coda_gruppi.npz"),
                    **{"%s_%s" % (n.split(":")[0].replace(" ", "_"), k): v
                       for n, g in G.items() if g is not None for k, v in g.items() if k != "forme"})
