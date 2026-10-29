"""Fondo LNGS 0013 contro il MC dei gamma di Hall A (spettro a righe)
digitalizzato, senza e con la scintillazione del nucleo delle fibre.

    python3 analisi/20261029_scint_fibre/mc_scint_vs_dati.py MC_SCINT.npz OUT.png [ETICHETTA]

MC_SCINT.npz e' l'uscita di analisi/20261030_digit_mc/digit_mc.py su una
produzione con /wc/fiber/scintYield > 0; il MC senza scintillazione e'
measurements/mc_digit_gamma_righe.npz (10 M gamma). Tutto in rate, stesso
trigger simulato e stessa selezione dei dati.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

F_SC, OUT = sys.argv[1], sys.argv[2]
ETI = sys.argv[3] if len(sys.argv) > 3 else "MC with fibre scintillation"
MIS = _os.path.join(ROOT, "measurements")
T13 = 2367.313986
S = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))["sigma_q"]


def classi(q, tr):
    both = tr & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])
    return {"trigger": tr, "both PMT > 3σ": both, "C: both, max Q ≥ 10000": both & (q.max(1) >= 10000),
            "B: both, max Q < 7000": both & (q.max(1) < 7000),
            "one PMT only": tr & ((q > 3 * S[None, :]).sum(1) == 1)}


d = np.load(_os.path.join(MIS, "carica_0013_testbeam.npz"))
qd = np.stack([d["q_caen_ch20"], d["q_caen_ch21"]], 1)
serie = [("DATA LNGS 0013", qd, np.ones(len(qd), bool), 1.0 / T13, "k", "o")]
for f, nome, col in ((_os.path.join(MIS, "mc_digit_gamma_righe.npz"), "MC Hall A γ, no fibre scintillation (10 M γ)", "#2a6fdb"),
                     (F_SC, ETI, "#d62728")):
    z = np.load(f)
    serie.append((nome + " (%d M γ)" % (int(z["n_gen"]) // 10**6) if "no fibre" not in nome else nome,
                  z["q"], z["trig"], float(z["peso_hz"]), col, "s"))

b = np.linspace(-1000, 40000, 83)
bc = 0.5 * (b[1:] + b[:-1])
fig, ax = plt.subplots(1, 2, figsize=(19, 7), width_ratios=[1.4, 1])
a = ax[0]
for nome, q, tr, w, col, mk in serie:
    c = classi(q, tr)["both PMT > 3σ"]
    h = np.histogram(q[c].max(1), b)[0]
    ok = h > 0
    if nome.startswith("DATA"):
        a.errorbar(bc[ok], h[ok] * w, yerr=np.sqrt(h[ok]) * w, fmt="o", ms=3.5, color=col, lw=0.8,
                   label="%s: %.3f Hz" % (nome, c.sum() * w))
    else:
        a.step(bc, h * w, where="mid", color=col, lw=1.8, label="%s: %.3f Hz" % (nome, c.sum() * w))
a.axvline(10000, color="#888", ls=":", lw=1)
a.text(10300, 0.3, "class C →", fontsize=9, color="#555")
a.set_yscale("log")
a.set_ylim(1e-5, 0.5)
a.set_xlim(-1000, 40000)
a.ticklabel_format(axis="x", style="plain")
a.set_xlabel("charge of the brighter PMT [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns)")
a.set_ylabel("rate per bin [Hz] (bin = %.0f ADC·ns)" % (b[1] - b[0]))
a.set_title("events with both PMTs > 3σ (with the trigger): rates, no free normalisation", fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1]
nomi_cl = list(classi(qd, np.ones(len(qd), bool)))
x = np.arange(len(nomi_cl))
for k, (nome, q, tr, w, col, mk) in enumerate(serie):
    cl = classi(q, tr)
    v = [cl[n].sum() * w for n in nomi_cl]
    a.bar(x + (k - 1) * 0.27, v, 0.27, color=col, alpha=0.85, label=nome.split(" (")[0])
    for xi, vi, n in zip(x, v, nomi_cl):
        a.text(xi + (k - 1) * 0.27, vi * 1.15, ("%.3f" % vi) if vi > 0 else "0", ha="center", fontsize=7, rotation=90)
a.set_yscale("log")
a.set_ylim(1e-3, 20)
a.set_xticks(x)
a.set_xticklabels(nomi_cl, fontsize=8, rotation=15)
a.set_ylabel("rate [Hz]")
a.set_title("rates per class", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False, loc="upper right")
a.grid(alpha=0.25, axis="y", which="both")
fig.suptitle("LNGS background vs Monte Carlo with and without the scintillation of the fibre cores "
             "(Hall A γ, line spectrum, digitised like the data)", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
