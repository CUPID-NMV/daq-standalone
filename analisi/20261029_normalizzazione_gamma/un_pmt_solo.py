"""Eventi con un solo PMT sopra 3 sigma: dati 0013 (offset 100) e 0011 (offset
200) contro il MC dei gamma e dei muoni, ciascuno col trigger della sua run.

    python3 analisi/20261029_normalizzazione_gamma/un_pmt_solo.py OUT.png [GAMMA.npz] [MUONI.npz]

La domanda: il massimo a 2-3 PE della carica di un PMT (nella sovrapposizione
col piedistallo) sono eventi in cui un solo PMT supera la soglia. Il MC li
ha? E quanti? Si mostra la carica del PMT acceso, separata per PMT (ch20 solo,
ch21 solo), in rate.
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
GAMMA_F = sys.argv[2] if len(sys.argv) > 2 else "mc_digit_gamma_righe_s3500.npz"
MU_F = sys.argv[3] if len(sys.argv) > 3 else "mc_digit_mu_lngs_s3500.npz"
MIS = _os.path.join(ROOT, "measurements")
SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
RUN = {"0013": "LNGS__54-0V_0013_750Ms_55PT", "0011": "LNGS__54-0V_0011_750Ms_55PT"}
OFFSET = {"0013": 100, "0011": 200}
mc = [np.load(_os.path.join(MIS, f)) for f in (GAMMA_F, MU_F)]
K = float(mc[0]["k"])
b = np.logspace(np.log10(1000), np.log10(200000), 24)
bc = np.sqrt(b[1:] * b[:-1])

fig, ax = plt.subplots(1, 2, figsize=(18, 6.6), sharey=True)
for a, r in zip(ax, RUN):
    s = SOGLIE[RUN[r]]
    S, A, T = np.array(s["sigma_q"]), np.array(s["soglia"]), s["durata_s"]
    z = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % r))
    q = np.stack([z["q_caen_ch20"], z["q_caen_ch21"]], 1)
    sopra = q > 3 * S
    righe = []
    for j, ch, col in ((0, 20, "#2a6fdb"), (1, 21, "#d62728")):
        solo = sopra[:, j] & ~sopra[:, 1 - j]
        h = np.histogram(q[solo, j], b)[0]
        ok = h > 0
        a.errorbar(bc[ok], h[ok] / T, yerr=np.sqrt(h[ok]) / T, fmt="o", ms=4, color=col, lw=0.8,
                   label="DATA, only ch%d above 3σ: %.2f Hz" % (ch, solo.sum() / T))
        # MC: gamma + muoni col trigger di questa run
        hm, tot = np.zeros(len(b) - 1), 0.0
        for m in mc:
            trig = (m["amp"][:, 0] >= A[0]) | (m["amp"][:, 1] >= A[1])
            sm = m["q"] > 3 * S[None, :]
            sel = trig & sm[:, j] & ~sm[:, 1 - j]
            hm += np.histogram(m["q"][sel, j], b)[0] * float(m["peso_hz"])
            tot += sel.sum() * float(m["peso_hz"])
        a.stairs(hm, b, color=col, lw=1.6, ls="--", label="MC (Hall A γ + μ), only ch%d above 3σ: %.3f Hz" % (ch, tot))
        righe.append((ch, solo.sum() / T, tot))
    a.axvline(2 * K, color="#888", ls=":", lw=1)
    a.axvline(3 * K, color="#888", ls=":", lw=1)
    a.text(2 * K * 1.03, 3e-1, "2-3 PE", fontsize=9, color="#555")
    a.set_xscale("log")
    a.set_yscale("log")
    a.set_xlim(1000, 200000)
    a.set_ylim(1e-5, 3)
    a.set_xlabel("charge of the PMT above 3σ [ADC·ns]  (1 PE = %.0f)" % K)
    a.set_title("run %s, self-trigger offset %d (amplitude edge %.0f/%.0f counts), %.1f h"
                % (r, OFFSET[r], A[0], A[1], T / 3600), fontsize=10, loc="left")
    a.legend(fontsize=8.5, frameon=False, loc="upper right")
    a.grid(alpha=0.25, which="both")
    for ch, rd, rm in righe:
        print("%s ch%d solo: dati %.3f Hz, MC %.3f Hz, rapporto %.1f" % (r, ch, rd, rm, rd / rm if rm else np.inf))
ax[0].set_ylabel("rate per bin [Hz]  (10 bins per decade)")
fig.suptitle("Events with ONE PMT above 3σ: data vs Monte Carlo (Hall A γ with fibre scintillation + μ), "
             "each with the trigger of its run", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
print("scritto", OUT)
