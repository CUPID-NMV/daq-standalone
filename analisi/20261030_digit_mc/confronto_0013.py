"""Fondo LNGS 0013 contro il MC digitalizzato come i dati (digit_mc.py):
Compton dei gamma di Hall A con lo spettro a righe (limite superiore) e con
lo spettro NaI (limite inferiore), muoni di Hall A; tutto in RATE e con lo
stesso trigger e la stessa selezione.

    python3 analisi/20261030_digit_mc/confronto_0013.py OUT.png

Sopra: eventi col trigger (self-trigger in OR, ampiezza oltre il bordo
misurato) ed entrambi i PMT sopra 3 sigma del piedistallo. Sotto: tutti gli
eventi col trigger, senza il taglio sull'altro PMT.
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
MIS = _os.path.join(ROOT, "measurements")
T13 = 2367.313986
d = np.load(_os.path.join(MIS, "carica_0013_testbeam.npz"))
qd = np.stack([d["q_caen_ch20"], d["q_caen_ch21"]], 1)
S = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))["sigma_q"]
K = float(np.load(_os.path.join(MIS, "mc_digit_mu_lngs.npz"))["k"])
both_d = (qd[:, 0] > 3 * S[0]) & (qd[:, 1] > 3 * S[1])
MC = {"righe": ("MC Hall A γ, line spectrum (upper bound)", "#e8710a", "-"),
      "nai": ("MC Hall A γ, NaI spectrum (lower bound)", "#d62728", "--"),
      "mu": ("MC Hall A μ", "#2a6fdb", "-")}
mc = {}
for nome, f in (("righe", "mc_digit_gamma_righe.npz"), ("nai", "mc_digit_gamma_nai.npz"),
                ("mu", "mc_digit_mu_lngs.npz")):
    z = np.load(_os.path.join(MIS, f))
    mc[nome] = (z["q"], z["trig"], float(z["peso_hz"]))

b = np.linspace(-1000, 30000, 125)
bc = 0.5 * (b[1:] + b[:-1])
fig, ax = plt.subplots(2, 2, figsize=(19, 12))
for riga, (titolo, sel_d, sel_mc) in enumerate((
        ("trigger + both PMT > 3σ_ped", both_d,
         lambda q, tr: tr & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])),
        ("trigger only (all recorded events)", np.ones(len(qd), bool), lambda q, tr: tr))):
    for j, ch in enumerate((20, 21)):
        a = ax[riga, j]
        h = np.histogram(qd[sel_d, j], b)[0]
        a.errorbar(bc[h > 0], h[h > 0] / T13, yerr=np.sqrt(h[h > 0]) / T13, fmt="o", ms=3, color="k", lw=0.8,
                   label="DATA LNGS 0013 ch%d: %.3f Hz" % (ch, sel_d.sum() / T13))
        tot = np.zeros(len(bc))
        for nome, (lab, col, ls) in MC.items():
            q, tr, w = mc[nome]
            s = sel_mc(q, tr)
            hm = np.histogram(q[s, j], b)[0] * w
            if nome != "nai":
                tot += hm
            a.step(bc, hm, where="mid", color=col, lw=1.6, ls=ls, label="%s: %.3g Hz" % (lab, s.sum() * w))
        a.step(bc, tot, where="mid", color="#7b2d8b", lw=2.2, label="MC sum: line-spectrum γ + μ")
        a.set_yscale("log")
        a.set_ylim(1e-7, 1)
        a.set_xlim(-1000, 30000)
        a.ticklabel_format(axis="x", style="plain")
        a.set_xlabel("PMT charge, absolute value [ADC·ns]   (gate 240-500 ns, pedestal 0-150 ns)")
        a.set_ylabel("rate per bin [Hz] (bin = %.0f ADC·ns)" % (b[1] - b[0]))
        a.set_title("ch%d — %s" % (ch, titolo), fontsize=10, loc="left")
        a.legend(fontsize=7.8, frameon=False, loc="upper right")
        a.grid(alpha=0.25, which="both")
        sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda p: p * K))
        sec.set_xlabel("photoelectrons (K = %.0f ADC·ns/PE)" % K, fontsize=9)
fig.suptitle("LNGS 0013 (self-trigger offset 100) vs Monte Carlo digitised like the data: H14600-103 QE, measured pulse "
             "shape, real noise traces, same trigger threshold (310/319 counts), same analysis", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
for nome in mc:
    q, tr, w = mc[nome]
    s = tr & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])
    print("%-6s trigger %.4f Hz, + entrambi > 3 sigma %.4f Hz" % (nome, tr.sum() * w, s.sum() * w))
print("dati   trigger %.4f Hz, + entrambi > 3 sigma %.4f Hz" % (len(qd) / T13, both_d.sum() / T13))
