"""Fondo LNGS 0013 contro i gamma di Hall A nel MC digitalizzato, con la
normalizzazione corretta (J * A, flusso_haffke.py) e K ritarato sui muoni con
la scintillazione delle fibre.

    python3 analisi/20261029_normalizzazione_gamma/confronto_rinormalizzato.py OUT.png [PRIMA.npz]

PRIMA.npz (facoltativo): una digitalizzazione con la normalizzazione vecchia,
per disegnarla tratteggiata; per esempio quella del commit 472c264:
    git show 472c264:measurements/mc_digit_gamma_righe_s1750.npz > /tmp/prima.npz
Classi come in mc_scint_vs_dati.py: entrambi i PMT sopra 3 sigma, e la
carica del PMT piu' illuminato sotto 7000 (B), fra 7000 e 10000, sopra 10000
ADC*ns (C).
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = sys.argv[1]
PRIMA = sys.argv[2] if len(sys.argv) > 2 else None
MIS = _os.path.join(ROOT, "measurements")
T13 = 2367.313986
S = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))["sigma_q"]
CLASSI = [("both > 3σ", None, None), ("B: < 7000", None, 7000), ("7000-10000", 7000, 10000),
          ("C: ≥ 10000", 10000, None)]


def serie(q, tr, w, rep):
    both = tr & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])
    qm = q[both].max(1)
    cl = {}
    for nome, lo, hi in CLASSI:
        m = np.ones(len(qm), bool)
        if lo is not None:
            m &= qm >= lo
        if hi is not None:
            m &= qm < hi
        # le repliche dello stesso evento Geant4 non sono indipendenti: errore come se
        # gli eventi fossero n / repliche
        cl[nome] = (m.sum() * w, np.sqrt(m.sum() * rep) * w)
    return qm, w, rep, cl


d = np.load(_os.path.join(MIS, "carica_0013_testbeam.npz"))
qd = np.stack([d["q_caen_ch20"], d["q_caen_ch21"]], 1)
dati = serie(qd, np.ones(len(qd), bool), 1 / T13, 1)
mc = {}
for chiave, f in (("s1750", "mc_digit_gamma_righe_s1750.npz"), ("noscint", "mc_digit_gamma_righe.npz"),
                  ("mu", "mc_digit_mu_lngs_s1750.npz")):
    z = np.load(_os.path.join(MIS, f))
    mc[chiave] = serie(z["q"], z["trig"], float(z["peso_hz"]), int(z["repliche"]))
    mc[chiave + "_info"] = (float(z["rate_hz"]), float(z["k"]), int(z["n_gen"]))
if PRIMA:
    z = np.load(PRIMA)
    mc["prima"] = serie(z["q"], z["trig"], float(z["peso_hz"]), int(z["repliche"]))
    mc["prima_info"] = (float(z["rate_hz"]), float(z["k"]), int(z["n_gen"]))

for nome, _, _ in CLASSI:
    r = [dati[3][nome][0]] + [mc[k][3][nome][0] for k in ("s1750", "noscint", "mu")]
    print("%-12s dati %.3f Hz | MC gamma con scint. %.3f (dati/MC %.2f) | senza scint. %.3f | muoni %.5f%s"
          % (nome, r[0], r[1], r[0] / r[1], r[2], r[3],
             "" if not PRIMA else " | prima %.3f (dati/MC %.1f)" % (mc["prima"][3][nome][0],
                                                                  r[0] / mc["prima"][3][nome][0])))

COL = {"dati": "k", "s1750": "#d62728", "noscint": "#2a6fdb", "prima": "#d62728"}
fig, ax = plt.subplots(1, 3, figsize=(22, 6.6), width_ratios=[1.35, 1, 1])
a = ax[0]
b = np.linspace(-1000, 40000, 83)
bc = 0.5 * (b[1:] + b[:-1])
h = np.histogram(dati[0], b)[0]
ok = h > 0
a.errorbar(bc[ok], h[ok] / T13, yerr=np.sqrt(h[ok]) / T13, fmt="o", ms=3.5, color="k", lw=0.8,
           label="DATA LNGS 0013: %.3f Hz" % dati[3]["both > 3σ"][0])
r, k, n = mc["s1750_info"]
a.step(bc, np.histogram(mc["s1750"][0], b)[0] * mc["s1750"][1], where="mid", color=COL["s1750"], lw=1.8,
       label="MC Hall A γ + fibre scintillation, %.0f Hz generated, K = %.0f: %.3f Hz"
             % (r, k, mc["s1750"][3]["both > 3σ"][0]))
a.step(bc, np.histogram(mc["noscint"][0], b)[0] * mc["noscint"][1], where="mid", color=COL["noscint"], lw=1.2,
       label="same, no fibre scintillation: %.3f Hz" % mc["noscint"][3]["both > 3σ"][0])
if PRIMA:
    r0, k0, _ = mc["prima_info"]
    a.step(bc, np.histogram(mc["prima"][0], b)[0] * mc["prima"][1], where="mid", color=COL["prima"], lw=1.1,
           ls="--", alpha=0.6, label="MC with fibre scintillation BEFORE: %.0f Hz generated, K = %.0f: %.3f Hz"
                                     % (r0, k0, mc["prima"][3]["both > 3σ"][0]))
a.axvline(7000, color="#888", ls=":", lw=1)
a.axvline(10000, color="#888", ls=":", lw=1)
a.set_yscale("log")
a.set_ylim(1e-5, 0.5)
a.set_xlim(-1000, 40000)
a.ticklabel_format(axis="x", style="plain")
a.set_xlabel("charge of the brighter PMT [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns)")
a.set_ylabel("rate per bin [Hz] (bin = %.0f ADC·ns)" % (b[1] - b[0]))
a.set_title("both PMTs > 3σ, with the trigger: absolute rates, no free normalisation", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1]
br = np.array([0, 2000, 4000, 6000, 8000, 10000, 12500, 15000, 20000, 25000, 30000, 40000])
cr = 0.5 * (br[1:] + br[:-1])
hd = np.histogram(dati[0], br)[0]
for chiave, lab in (("s1750", "MC with fibre scintillation (now)"), ("prima", "same, BEFORE (old normalisation)")):
    if chiave not in mc:
        continue
    qm, w, rep, _ = mc[chiave]
    hm = np.histogram(qm, br)[0]
    ok = (hm > 0) & (hd > 0)
    rr = (hd[ok] / T13) / (hm[ok] * w)
    err = rr * np.sqrt(1 / hd[ok] + rep / hm[ok])
    a.errorbar(cr[ok], rr, yerr=err, xerr=0.5 * np.diff(br)[ok], fmt="o" if chiave == "s1750" else "s",
               color=COL[chiave], ms=5, lw=1, alpha=1 if chiave == "s1750" else 0.45, label=lab)
a.axhline(1, color="k", lw=0.8)
a.axvline(7000, color="#888", ls=":", lw=1)
a.axvline(10000, color="#888", ls=":", lw=1)
a.set_yscale("log")
a.set_ylim(0.3, 100)
a.set_xlim(0, 40000)
a.ticklabel_format(axis="x", style="plain")
a.set_xlabel("charge of the brighter PMT [ADC·ns]")
a.set_ylabel("DATA / MC")
a.set_title("data / MC per charge bin (MC statistical error counts replicas as correlated)",
            fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper left")
a.grid(alpha=0.25, which="both")

a = ax[2]
nomi = [c[0] for c in CLASSI]
x = np.arange(len(nomi))
barre = [("DATA 0013", dati, "k", 1.0)]
if PRIMA:
    barre.append(("MC γ + scint., BEFORE", mc["prima"], COL["prima"], 0.35))
barre += [("MC γ + scint., now", mc["s1750"], COL["s1750"], 0.9), ("MC γ, no scint., now", mc["noscint"], COL["noscint"], 0.9)]
wb = 0.8 / len(barre)
for i, (lab, s, col, al) in enumerate(barre):
    v = np.array([s[3][n][0] for n in nomi])
    e = np.array([s[3][n][1] for n in nomi])
    xx = x + (i - (len(barre) - 1) / 2) * wb
    a.bar(xx, v, wb, yerr=e, color=col, alpha=al, label=lab, error_kw=dict(lw=0.8))
    for xi, vi in zip(xx, v):
        a.text(xi, vi * 1.25, "%.3f" % vi, ha="center", fontsize=7, rotation=90)
a.set_yscale("log")
a.set_ylim(5e-4, 5)
a.set_xticks(x)
a.set_xticklabels(nomi, fontsize=9)
a.set_ylabel("rate [Hz]")
a.set_title("rates per class (muons, MC: ≤ 1e-4 Hz in every class)", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False, loc="upper right")
a.grid(alpha=0.25, axis="y", which="both")
fig.suptitle("LNGS background vs Hall A γ in the MC, with the gamma rate corrected (J·A, φ = 1.12 cm$^{-2}$s$^{-1}$) "
             "and K recalibrated on muons with fibre scintillation", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
print("scritto", OUT)
