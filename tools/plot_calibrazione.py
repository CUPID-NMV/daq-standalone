#!/usr/bin/env python3
"""
Grafico di una calibrazione offset -> mV del self-trigger.

Rilegge measurements/calibrazione_*.json: i numeri stanno li' con i punti
grezzi, il grafico si rifa' quando serve.

    python3 tools/plot_calibrazione.py measurements/calibrazione_sipm_larghi_20261009.json
"""
import json
import os
import sys
from math import erf, sqrt

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLORE = "#2a78d6"          # la stessa tavolozza di plot_scan.py
GRIGIO = "#52514e"
MV_PER_CONTEGGIO = 0.244    # 1 Vpp su 12 bit


def phi(z):
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    d = json.load(open(path))
    uscita = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        "plots", os.path.basename(path).replace(".json", ".png"))

    x = np.array([p["distanza"] for p in d["punti"]], dtype=float)
    eff = np.array([p["efficienza"] for p in d["punti"]], dtype=float)
    secondi = d.get("secondi_per_punto", 0) or 0
    rate = np.array([p["rate_hz"] for p in d["punti"]], dtype=float)

    # Barre d'errore poissoniane sul CONTEGGIO, non sul rate: a efficienza
    # zero il rate non ha incertezza relativa, e una barra proporzionale al
    # rate farebbe sparire l'unico punto che dice dov'e' il fondo.
    n = rate * secondi
    err = np.where(n > 0, np.sqrt(np.maximum(n, 1.0)) / max(secondi, 1), 0.0)
    err = err / d["rate_generatore_hz"]

    d50, sigma = d["distanza_50pc"], max(d["sigma_turnoff_offset"], 1e-3)
    k = d["mv_per_offset"]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.0, 4.3),
                                 gridspec_kw={"width_ratios": [1.45, 1]})

    for ax, (lo, hi) in ((a1, (0, max(x) * 1.05)), (a2, (d50 - 12, d50 + 12))):
        xx = np.linspace(lo, hi, 600)
        ax.plot(xx, [phi((d50 - v) / sigma) for v in xx], lw=1.6,
                color=GRIGIO, alpha=.55, zorder=1)
        ax.errorbar(x, eff, yerr=err, fmt="o", ms=6.5, lw=0, elinewidth=1.4,
                    color=COLORE, mec="white", mew=1.2, capsize=3, zorder=3)
        ax.axvline(d50, color="#eb6834", lw=1.2, ls="--", zorder=2)
        ax.axhline(0.5, color="#ddd", lw=1, zorder=0)
        ax.set_xlim(lo, hi)
        # Lo zero e l'uno ci stanno sempre: un'efficienza si legge rispetto a
        # quelli, non rispetto al punto piu' basso che e' stato misurato.
        ax.set_ylim(-0.05, 1.12)
        ax.grid(alpha=.25)
        ax.set_xlabel("threshold distance from baseline  [offset units]")

        # Secondo asse in mV: stessa grandezza, altra unita' -- e' la
        # calibrazione stessa, quindi va letta sul grafico che la misura.
        at = ax.twiny()
        at.set_xlim(lo * k, hi * k)
        at.set_xlabel("threshold at the detector input  [mV]", fontsize=9,
                      color=GRIGIO)
        at.tick_params(labelsize=8, colors=GRIGIO)

    a1.set_ylabel("efficiency")
    a2.tick_params(labelleft=False)
    a1.text(d50 * 1.02, 0.62, "50%% at %.1f" % d50, color="#eb6834", fontsize=9)
    a2.text(d50 + 0.6, 1.02, "d50 = %.1f" % d50, color="#eb6834", fontsize=9)
    a2.text(.03, .08, "zoom", transform=a2.transAxes, fontsize=9, color="#999")

    fig.suptitle("V1742 self-trigger: threshold calibration  ·  "
                 "%s pulses, %.0f ns FWHM, %.1f mV  ·  %s"
                 % (d.get("polarita", "?"), d["larghezza_fwhm_ns"],
                    d["ampiezza_mv"], d["frequenza_campionamento"]),
                 fontsize=11)
    fig.text(0.5, 0.015,
             "%.4f mV per offset unit   ·   Transparent Mode attenuation "
             "%.2f   ·   turn-off σ = %.2f offset = %.2f mV   ·   "
             "generator %.1f Hz from the plateau, %d s per point"
             % (k, d["attenuazione"], sigma, sigma * k,
                d["rate_generatore_hz"], secondi),
             ha="center", fontsize=9, color=GRIGIO)

    fig.tight_layout(rect=(0, 0.055, 1, 0.94))
    os.makedirs(os.path.dirname(uscita) or ".", exist_ok=True)
    fig.savefig(uscita, dpi=110)
    print("grafico:", os.path.abspath(uscita))


if __name__ == "__main__":
    main()
