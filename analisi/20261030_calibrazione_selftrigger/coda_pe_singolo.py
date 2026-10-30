"""Gli eventi con un PMT solo a offset 100 sono la coda alta degli impulsi da un
fotoelettrone? Scan del self-trigger del 2026-10-30.

    python3 analisi/20261030_calibrazione_selftrigger/coda_pe_singolo.py OUT

Al passo a offset 10 un PMT scatta ~25 Hz, quasi solo su impulsi propri da un
fotoelettrone, con un'ampiezza che fluttua moltissimo (sigma/media ~0.6). Se
agli eventi di quel passo si applica in software la soglia di offset 100 --
fra il bordo basso e i quantili della calibrazione, perche' per impulsi corti
la soglia hardware non e' un gradino -- si ottiene quanti di quegli impulsi
avrebbero fatto scattare il trigger a offset 100, e con che carica. Si
confronta con gli eventi che l'hardware ha davvero registrato al passo a
offset 100, con un PMT solo.

Legge measurements/un_pmt_scan_20261030.npz (cariche e ampiezze nel cancello
240-500 ns) e soglie_pe_20261030.json (la calibrazione). Scrive OUT.png.
"""
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MIS = os.path.join(ROOT, "measurements")
OUT = sys.argv[1]
MV = 1000.0 / 4096.0

z = np.load(os.path.join(MIS, "un_pmt_scan_20261030.npz"))
cal = json.load(open(os.path.join(MIS, "soglie_pe_20261030.json")))
durate = dict(zip(z["passo"].tolist(), z["durata"].tolist()))
S = z["sigma_q"]
K = 1647.0
b = np.concatenate([np.arange(0, 2000, 250.0), np.logspace(np.log10(2000), np.log10(20000), 13)])
bc = np.where(b[:-1] >= 2000, np.sqrt(b[1:] * b[:-1]), 0.5 * (b[1:] + b[:-1]))

fig, ax = plt.subplots(1, 2, figsize=(17, 6.8), sharey=True)
for a, (j, ch) in zip(ax, ((0, 20), (1, 21))):
    o = 1 - j
    c = cal[str(ch)]
    soglia_bordo = (c["mv_per_offset_bordo"] * 100 + c["intercetta_bordo_mv"]) / MV
    soglia_quant = (c["mv_per_offset_quantili"] * 100 + c["intercetta_quantili_mv"]) / MV
    k10 = (z["canale"] == ch) & (z["offset"] == 10)
    q10, a10 = z["q"][k10], z["amp"][k10]
    t10 = durate["ch%d_off10" % ch]
    k100 = (z["canale"] == ch) & (z["offset"] == 100)
    q100 = z["q"][k100]
    t100 = durate["ch%d_off100" % ch]
    solo100 = (q100[:, j] > 3 * S[j]) & (q100[:, o] <= 3 * S[o])

    h10 = np.histogram(q10[:, j], b)[0]
    a.stairs(h10 / t10 / (np.diff(b) / 100), b, color="#9a9a9a", lw=1.3,
             label="offset 10, all events: %.1f Hz (mostly single-photoelectron pulses)" % (len(q10) / t10))
    righe = []
    for soglia, nome, col, ls in ((soglia_bordo, "lower edge", "#2a78d6", "-"),
                                  (soglia_quant, "rate quantiles", "#1baf7a", "--")):
        sel = (a10[:, j] >= soglia) & (q10[:, o] <= 3 * S[o])
        h = np.histogram(q10[sel, j], b)[0]
        a.stairs(h / t10 / (np.diff(b) / 100), b, color=col, lw=1.8, ls=ls,
                 label="offset 10 cut IN SOFTWARE at the offset-100 threshold (%s, %.0f mV), other PMT dark: %.2f Hz"
                 % (nome, soglia * MV, sel.sum() / t10))
        righe.append((nome, soglia * MV, sel.sum() / t10, sel.sum()))
    h = np.histogram(q100[solo100, j], b)[0]
    ok = h > 0
    a.errorbar(bc[ok], h[ok] / t100 / (np.diff(b)[ok] / 100), yerr=np.sqrt(h[ok]) / t100 / (np.diff(b)[ok] / 100),
               fmt="o", color="k", ms=6, lw=1, zorder=5,
               label="offset 100 in HARDWARE, ONE PMT above 3σ: %.2f Hz (%d events)" % (solo100.sum() / t100,
                                                                                         solo100.sum()))
    for v in (1, 2, 3):
        a.axvline(v * K, color="#bbb", ls=":", lw=1)
    a.text(2 * K * 1.02, 2e-4, "2 PE", fontsize=9, color="#777")
    a.set_xscale("symlog", linthresh=2000, linscale=0.8)
    a.set_xlim(0, 20000)
    a.set_yscale("log")
    a.set_ylim(1e-4, 5)
    a.set_xlabel("charge of PMT ch%d [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns; 1 PE ≈ %.0f)" % (ch, K))
    a.set_title("PMT ch%d alone (other channels parked): who fires the trigger at offset 100?" % ch,
                fontsize=10, loc="left")
    a.legend(fontsize=8.3, frameon=False, loc="upper right")
    a.grid(alpha=0.25, which="both")
    for nome, mv, r, n in righe:
        print("ch%d: offset 10 tagliato a %.0f mV (%s): %.2f Hz (%d eventi); offset 100 hardware, un PMT solo: %.2f Hz"
              % (ch, mv, nome, r, n, solo100.sum() / t100))
ax[0].set_ylabel("rate per 100 ADC·ns [Hz]")
fig.suptitle("Self-trigger scan of 2026-10-30: the events with one PMT at offset 100 vs the high tail of the "
             "pulses recorded at offset 10", fontsize=11)
fig.tight_layout()
fig.savefig(OUT + ".png", dpi=100)
print("scritto", OUT + ".png")
